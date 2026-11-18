import type { ReactNode } from 'react';
import { confirmDialog } from './ConfirmDialog';
import { formatRoute } from './route';
import { RunControls } from './RunControls';
import { RunStages } from './RunStages';
import { runStatus, sizeLabel } from './runStatus';
import type { RunSummary } from './runHistory';
import type { DeletePreview, Progress, Run } from './runTypes';

export function confirmDelete(
  run: { id: string; job: { name: string; revision: number } },
  preview: DeletePreview,
) {
  const segments = preview.segments ?? [];
  const lines = [
    `This frees ${sizeLabel(preview.bytes ?? 0)} and can't be undone. ${segments.length ? 'Every segment’s' : 'Its'} checkpoints, samples, saved states, logs and saved inputs are removed, so copy any LoRA you want to keep first.`,
  ];
  if (preview.shared_cache)
    lines.push(
      'Its cache stays until the other run using it is deleted, and is not counted above.',
    );
  if (preview.dependents?.length)
    lines.push(
      `${preview.dependents.length} later run(s) came from this one. They stay browsable, but can no longer be retried or restored from.`,
    );
  return confirmDialog({
    title: segments.length
      ? `Permanently delete this training of ${run.job.name} (${segments.length} segments)?`
      : `Permanently delete run ${run.id.slice(0, 8)} of ${run.job.name} (r${run.job.revision})?`,
    body: (
      <>
        {segments.length > 0 && (
          <ul aria-label="Segments to delete">
            {segments.map((segment) => (
              <li key={segment.id}>
                Segment {segment.position} · Run {segment.id.slice(0, 8)} · r
                {segment.job.revision} · steps {segment.steps.start}–
                {segment.steps.end ?? segment.steps.target} ·{' '}
                {runStatus(segment.status).label}
              </li>
            ))}
          </ul>
        )}
        {lines.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </>
    ),
    confirmLabel: segments.length ? 'Delete training' : 'Delete run',
    danger: true,
  });
}

// The Actions panel: what the user can do with the Run in its current state.
export function RunActions({
  run,
  progress,
  busy,
  pairedStarted,
  segments,
  action,
  deleteRun,
  resume,
}: {
  run: Run;
  progress: Progress | null;
  busy: boolean;
  pairedStarted: boolean;
  // The training's segments when it was resumed, in creation order; otherwise empty.
  segments: RunSummary[];
  action: (name: string, body?: object) => Promise<void>;
  deleteRun: (chain: boolean) => Promise<void>;
  // Resume, for an ended Run that no later segment continues.
  resume?: ReactNode;
}) {
  const place = segments.find((segment) => segment.id === run.id)?.chain;
  const chained = segments.length > 1 && run.status !== 'deleting';
  return (
    <section className="panel" aria-label="Actions">
      <h2>Actions</h2>
      {run.start_after_prepare &&
        ['pending_preparation', 'preparing'].includes(run.status) && (
          <p>Training starts by itself when preparation succeeds.</p>
        )}
      {run.status === 'pending_training' && (
        <p>
          Remove this Run from the training queue. Training will not start;
          prepared files remain available.
        </p>
      )}
      {pairedStarted &&
        ['failed', 'stopped', 'interrupted'].includes(run.status) && (
          <p>
            Resume, or choose a paired save in Resume from an earlier save, to
            continue both experts and the schedule as a new segment. Updates
            after the selected save are lost. Prepare from Jobs to restart both
            experts.
            {run.resume_of && (
              <>
                {' '}
                You can also restore again from the{' '}
                <a
                  href={formatRoute({
                    screen: 'Runs',
                    id: run.resume_of,
                  })}
                >
                  source Run
                </a>
                , including when this segment failed before saving state.
              </>
            )}
          </p>
        )}
      {!pairedStarted &&
        ['failed', 'cancelled', 'interrupted'].includes(run.status) && (
          <div aria-label="Retry frozen inputs">
            <p>
              Retry creates a new Run using this Run’s frozen job revision and
              dataset inputs. Only verified matching latent and text caches are
              eligible for reuse. Training will not start automatically.
              Training and sampling run again after you start training (sampling
              only if enabled in the frozen job). It does not resume optimizer
              state.
            </p>
            <p>
              To change the build, edit the job, save and review it, then
              prepare from Jobs. For saved optimizer state, use Resume, or
              choose a save in Resume from an earlier save.
            </p>
          </div>
        )}
      {run.status === 'deleting' && (
        <div aria-label="Unfinished delete">
          <p className="error">
            {run.deletion?.error ??
              'This run is being deleted, or its delete was interrupted. If this stays, choose Delete again.'}
          </p>
          {!!run.deletion?.remaining.length && (
            <>
              <p>Still on disk:</p>
              <ul>
                {run.deletion.remaining.map((item) => (
                  <li key={item.path}>
                    {item.path}
                    {item.bytes !== undefined && ` · ${sizeLabel(item.bytes)}`}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      {['training', 'generating', 'stopping'].includes(run.status) && (
        <RunControls
          run={run}
          progress={progress}
          busy={busy}
          action={action}
        />
      )}
      {resume}
      <div className="actions">
        {['orphaned', 'uncertain', 'interrupted'].includes(run.status) && (
          <>
            <button disabled={busy} onClick={() => void action('reconcile')}>
              Check for leftover processes
            </button>
            {run.status !== 'interrupted' && (
              <button
                className="danger"
                disabled={busy}
                onClick={() =>
                  void confirmDialog({
                    title: 'Terminate recovered work?',
                    body: (
                      <p>
                        This ends the recovered processes belonging to this run.
                        Unsaved work may be lost.
                      </p>
                    ),
                    confirmLabel: 'Terminate recovered work',
                    danger: true,
                  }).then((confirmed) => {
                    if (confirmed)
                      void action('terminate-orphan', {
                        confirmed: true,
                      });
                  })
                }
              >
                Terminate recovered work
              </button>
            )}
          </>
        )}
        {run.status === 'prepared' && (
          <button
            disabled={busy}
            className="primary"
            onClick={() => void action('start')}
          >
            Start training
          </button>
        )}
        {run.status === 'prepared' && (
          <button disabled={busy} onClick={() => void action('queue')}>
            Add to queue
          </button>
        )}
        {['pending_preparation', 'pending_training', 'preparing'].includes(
          run.status,
        ) && (
          <button disabled={busy} onClick={() => void action('cancel')}>
            {run.status === 'pending_training'
              ? 'Cancel queued training'
              : run.status === 'pending_preparation'
                ? 'Cancel queued preparation'
                : 'Cancel preparation'}
          </button>
        )}
        {!pairedStarted &&
          ['failed', 'cancelled', 'interrupted'].includes(run.status) && (
            <button disabled={busy} onClick={() => void action('retry')}>
              Prepare new Run from frozen inputs
            </button>
          )}
        {chained && (
          <button
            className="danger"
            disabled={busy}
            onClick={() => void deleteRun(true)}
          >
            Delete training
          </button>
        )}
        {(!chained || place?.later_segments === 0) && (
          <button
            className="danger"
            disabled={busy}
            onClick={() => void deleteRun(false)}
          >
            {run.status === 'deleting'
              ? 'Delete again'
              : chained
                ? 'Delete segment'
                : 'Delete run'}
          </button>
        )}
      </div>
      {chained && !!place?.later_segments && (
        <p>
          Later segments continue from this one, so it is deleted with the whole
          training, or after its later segments.
        </p>
      )}
      <RunStages run={run} />
    </section>
  );
}
