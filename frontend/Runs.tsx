import { useEffect, useEffectEvent, useState } from 'react';
import { apiClient } from './api';
import { confirmDialog } from './ConfirmDialog';
import {
  Outputs,
  RunResult,
  stateFiles,
  useRunOutputs,
  type Output,
} from './Outputs';
import { formatRoute } from './route';
import { notificationsWanted, setNotificationsWanted } from './runSignals';
import { readableTime, runStatus, sizeLabel } from './runStatus';
import { StatusBadge } from './StatusBadge';
import './runs.css';

type Run = {
  id: string;
  created: string;
  status: string;
  error: string | null;
  fixture: boolean;
  job: { id: string; name: string; family: string; revision: number };
  manifest: {
    items: { name?: string; path: string; caption: { text: string } }[];
  };
  stages: {
    name: string;
    status: string;
    exit_code?: number;
    reused_from?: string;
    started?: string;
    finished?: string;
  }[];
  directory: string;
  order: number;
  start_after_prepare?: boolean;
  deletion?: {
    error: string | null;
    remaining: { path: string; bytes?: number }[];
  };
};
type DeletePreview = {
  refusal: string | null;
  bytes?: number;
  shared_cache?: string | null;
  dependents?: { id: string; status: string }[];
};
type DeleteResult = { deleted: boolean; freed_bytes?: number };
type Progress = {
  step: number;
  total: number;
  elapsed: string | null;
  eta: string | null;
  loss: string | null;
};
type StepState = 'pending' | 'current' | 'completed' | 'failed' | 'stopped';
const stepLabels: Record<StepState, string> = {
  pending: 'Not started',
  current: 'In progress',
  completed: 'Done',
  failed: 'Failed',
  stopped: 'Stopped',
};

// Groups the stages into what the user is waiting for. A Run can fail outside any stage, such as
// when frozen inputs changed, so that failure lands on the first step that did not finish.
function runSteps(run: Run) {
  const groups = [
    { label: 'Caching', names: ['latent_cache', 'text_cache'] },
    { label: 'Training', names: ['train'] },
    { label: 'Sample', names: ['generate'] },
  ];
  const steps = groups.flatMap(({ label, names }) => {
    const stages = run.stages.filter((stage) => names.includes(stage.name));
    if (stages.length === 0) return [];
    const has = (...values: string[]) =>
      stages.some((stage) => values.includes(stage.status));
    const state: StepState = has('failed')
      ? 'failed'
      : has('stopped', 'interrupted')
        ? 'stopped'
        : has('running')
          ? 'current'
          : stages.every((stage) =>
                ['completed', 'reused'].includes(stage.status),
              )
            ? 'completed'
            : 'pending';
    const failed =
      state === 'failed' && stages.find((stage) => stage.exit_code);
    const detail = failed
      ? `Exit code ${failed.exit_code}`
      : stages.some((stage) => stage.reused_from)
        ? 'verified cache reused'
        : stepLabels[state];
    return [{ label, state, detail }];
  });
  if (
    run.status === 'failed' &&
    !steps.some((step) => step.state === 'failed')
  ) {
    const step =
      steps.find((item) => item.state !== 'completed') ?? steps.at(-1);
    if (step) Object.assign(step, { state: 'failed', detail: 'Failed' });
  }
  const done: StepState = run.status === 'completed' ? 'completed' : 'pending';
  return [...steps, { label: 'Done', state: done, detail: stepLabels[done] }];
}

function duration(from: string, to?: string) {
  const seconds = Math.max(
    0,
    Math.round(((to ? Date.parse(to) : Date.now()) - Date.parse(from)) / 1000),
  );
  const minutes = Math.floor(seconds / 60);
  const pad = (value: number) => String(value).padStart(2, '0');
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}:${pad(minutes % 60)}:${pad(seconds % 60)}`
    : `${pad(minutes)}:${pad(seconds % 60)}`;
}

function TrainingProgress({
  run,
  progress,
}: {
  run: Run;
  progress: Progress | null;
}) {
  const train = run.stages.find((stage) => stage.name === 'train');
  if (!train || train.status === 'pending') return null;
  if (!progress)
    return (
      <div className="run-progress" aria-label="Training progress">
        <p>Step progress unknown: training has not reported a step yet.</p>
      </div>
    );
  const elapsed =
    progress.elapsed ??
    (train.started ? duration(train.started, train.finished) : 'unknown');
  return (
    <div className="run-progress" aria-label="Training progress">
      <p>
        <strong>
          Step {progress.step}/{progress.total}
        </strong>
      </p>
      <progress
        aria-label="Training steps"
        max={progress.total}
        value={progress.step}
      />
      <p>
        Elapsed {elapsed} · ETA {progress.eta ?? 'unknown'} · Loss{' '}
        {progress.loss ?? 'unknown'}
      </p>
    </div>
  );
}

function confirmDelete(run: Run, preview: DeletePreview) {
  const lines = [
    `This frees ${sizeLabel(preview.bytes ?? 0)} and can't be undone. Its checkpoints, samples, saved states, logs and saved inputs are removed, so download any LoRA you want to keep first.`,
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
    title: `Permanently delete run ${run.id.slice(0, 8)} of ${run.job.name} (r${run.job.revision})?`,
    body: lines.map((line) => <p key={line}>{line}</p>),
    confirmLabel: 'Delete run',
    danger: true,
  });
}

const request = apiClient('Run request failed.');

function RunBadge({ status }: { status: string }) {
  const { label, tone } = runStatus(status);
  return <StatusBadge tone={tone}>{label}</StatusBadge>;
}

function SavedStates({
  runId,
  files,
  onRestore,
}: {
  runId: string;
  files: Output[];
  onRestore: (stateId: string, steps: number) => Promise<void>;
}) {
  const [states, setStates] = useState<
    {
      id: string;
      name: string;
      valid: boolean;
      error: string;
      optimizer_step?: number;
    }[]
  >([]);
  const [steps, setSteps] = useState(1);
  const [error, setError] = useState('');
  const [inspecting, setInspecting] = useState(false);
  async function inspect() {
    // Inspection hashes every state file, which takes seconds for large folders.
    setInspecting(true);
    try {
      setStates(await request(`/api/runs/${runId}/states`));
      setError('');
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setInspecting(false);
    }
  }
  return (
    <section className="panel">
      <h2>Saved optimizer state</h2>
      <p>
        Restoration starts a new segment with saved optimizer, scheduler and RNG
        state. Upstream resets its displayed step counter and data position. A
        LoRA checkpoint alone cannot restore training state. Stopping does not
        guarantee a checkpoint or state save.
      </p>
      {files.length > 0 && (
        <details>
          <summary>Optimizer state files ({files.length})</summary>
          <ul className="state-files">
            {files.map((file) => (
              <li key={file.id}>
                {file.path}
                {file.bytes !== undefined && ` · ${sizeLabel(file.bytes)}`}
                {file.file_url && (
                  <>
                    {' · '}
                    <a href={`${file.file_url}?download=true`} download>
                      Download
                    </a>
                  </>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      <button disabled={inspecting} onClick={() => void inspect()}>
        Inspect saved states
      </button>
      {inspecting && <p role="status">Checking saved states…</p>}
      {error && <p role="alert">{error}</p>}
      <label className="field number-field">
        Additional segment steps
        <input
          type="number"
          min={1}
          value={steps}
          onChange={(event) => setSteps(Number(event.target.value))}
        />
      </label>
      {states.map((state) => (
        <p key={state.id}>
          {state.name} ·{' '}
          {state.valid ? `optimizer step ${state.optimizer_step}` : state.error}
          <button
            disabled={!state.valid || !Number.isInteger(steps) || steps < 1}
            onClick={() =>
              void confirmDialog({
                title: 'Restore this optimizer state as a new segment?',
                body: (
                  <p>
                    The displayed step counter and data position restart.
                    Original run inputs are preserved.
                  </p>
                ),
                confirmLabel: 'Restore as new segment',
              }).then((confirmed) => {
                if (confirmed) void onRestore(state.id, steps);
              })
            }
          >
            Restore as new segment
          </button>
        </p>
      ))}
    </section>
  );
}

export function Runs({
  selectedId,
  onSelect,
}: {
  selectedId?: string;
  onSelect?: (id?: string, replace?: boolean) => void;
}) {
  const [runs, setRuns] = useState<Run[]>([]);
  const selected = selectedId ?? '';
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [deleting, setDeleting] = useState('');
  const [busy, setBusy] = useState(false);
  const [owner, setOwner] = useState<{ run_id: string } | null>(null);
  const [fixture, setFixture] = useState(false);
  const [notify, setNotify] = useState(notificationsWanted);
  const [live, setLive] = useState<{
    runId: string;
    text: string;
    progress: Progress | null;
  } | null>(null);
  // Deleted, perhaps from another tab, or never there: a stale link leaves nothing to show.
  const forgetRun = useEffectEvent(() => onSelect?.(undefined, true));
  useEffect(() => {
    let current = true;
    async function refresh() {
      try {
        const [records, capabilities] = await Promise.all([
          request<Run[]>('/api/runs'),
          request<{ fixture: boolean; gpu_owner: { run_id: string } | null }>(
            '/api/runs/capabilities',
          ),
        ]);
        if (!current) return;
        setRuns(records);
        setOwner(capabilities.gpu_owner);
        setFixture(capabilities.fixture);
        if (selected && !records.some((record) => record.id === selected)) {
          forgetRun();
        } else if (selected) {
          const result = await request<{
            text: string;
            progress: Progress | null;
          }>(`/api/runs/${selected}/logs`);
          if (current) setLive({ runId: selected, ...result });
        }
      } catch (reason) {
        if (current) setError((reason as Error).message);
      }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [selected]);
  const run = runs.find((record) => record.id === selected);
  const ownerRun = runs.find((record) => record.id === owner?.run_id);
  const current = live?.runId === run?.id ? live : null;
  const showFiles = run && run.status !== 'deleting' && run.id !== deleting;
  // Changes whenever the Run or one of its stages does, so finished work appears without a click.
  const outputs = useRunOutputs(
    showFiles ? run.id : undefined,
    run ? [run.status, ...run.stages.map((stage) => stage.status)].join() : '',
  );
  async function action(name: string, body: object = {}) {
    if (!run) return;
    setBusy(true);
    setError('');
    try {
      const result = await request<Run>(
        `/api/runs/${run.id}/${name}`,
        'POST',
        body,
      );
      onSelect?.(result.id);
      setRuns(await request<Run[]>('/api/runs'));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function deleteRun() {
    if (!run) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const preview = await request<DeletePreview>(
        `/api/runs/${run.id}/delete-preview`,
      );
      if (preview.refusal) throw new Error(preview.refusal);
      if (!(await confirmDelete(run, preview))) return;
      setNotice('Deleting run…');
      // Unmounting the outputs closes any preview stream holding one of the files open.
      setDeleting(run.id);
      const result = await request<DeleteResult>(
        `/api/runs/${run.id}/delete`,
        'POST',
        { confirmed: true },
      );
      setNotice(
        result.deleted
          ? `Run deleted. Freed ${sizeLabel(result.freed_bytes ?? 0)}.`
          : '',
      );
      if (result.deleted) onSelect?.();
      setRuns(await request<Run[]>('/api/runs'));
    } catch (reason) {
      setNotice('');
      setError((reason as Error).message);
    } finally {
      setDeleting('');
      setBusy(false);
    }
  }
  async function moveEarlier(id: string) {
    const pending = runs
      .filter((record) =>
        ['pending_preparation', 'pending_training'].includes(record.status),
      )
      .sort((a, b) => a.order - b.order);
    const index = pending.findIndex((record) => record.id === id);
    if (index < 1) return;
    [pending[index - 1], pending[index]] = [pending[index], pending[index - 1]];
    try {
      await request('/api/runs/queue/order', 'POST', {
        run_ids: pending.map((record) => record.id),
      });
      setRuns(await request<Run[]>('/api/runs'));
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  return (
    <>
      <section className="title">
        <h1>Runs</h1>
        <p>
          Each Run keeps the job settings and dataset files it was prepared
          with. Closing the browser leaves training running.
        </p>
      </section>
      {fixture && (
        <p className="error">
          Synthetic fixture mode — these runs do not establish GPU or model
          support.
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <p role="status">
        {!owner ? (
          'GPU available'
        ) : ownerRun ? (
          <>
            GPU in use by{' '}
            <a href={formatRoute({ screen: 'Runs', id: ownerRun.id })}>
              {ownerRun.job.name}
            </a>
          </>
        ) : (
          'GPU in use by other work'
        )}
      </p>
      {notice && <p role="status">{notice}</p>}
      <div className="runs-layout">
        <div>
          <section className="panel">
            <h2>Pending queue</h2>
            {runs
              .filter((record) =>
                ['pending_preparation', 'pending_training'].includes(
                  record.status,
                ),
              )
              .sort((a, b) => a.order - b.order)
              .map((record, index) => (
                <p key={record.id}>
                  {index + 1}. {record.job.name}{' '}
                  <RunBadge status={record.status} />{' '}
                  <button
                    className="quiet"
                    disabled={index === 0}
                    onClick={() => void moveEarlier(record.id)}
                  >
                    Move earlier
                  </button>
                </p>
              ))}
          </section>
          <section className="panel run-history">
            <h2>Run history</h2>
            {runs.length === 0 && (
              <p>Save and review a job, then choose Prepare.</p>
            )}
            {runs.map((record) => (
              <div key={record.id}>
                <button
                  aria-pressed={selected === record.id}
                  onClick={() => onSelect?.(record.id)}
                >
                  {record.job.name} · {record.job.family.toUpperCase()} · r
                  {record.job.revision}
                </button>
                <p>
                  <RunBadge status={record.status} />{' '}
                  <small>{readableTime(record.created)}</small>
                </p>
              </div>
            ))}
          </section>
          <label className="job-toggle">
            <input
              type="checkbox"
              checked={notify}
              onChange={(event) =>
                void setNotificationsWanted(event.target.checked).then(
                  setNotify,
                )
              }
            />
            <span>
              Send a browser notification when a Run is ready or finished
            </span>
          </label>
        </div>
        <div>
          {!run && runs.length > 0 && <p>Choose a Run to see its details.</p>}
          {showFiles && <RunResult outputs={outputs} />}
          {run && (
            <section className="panel">
              <h2>{run.job.name}</h2>
              <p title={run.id}>Run {run.id.slice(0, 8)}</p>
              <p>
                <strong>
                  State: <RunBadge status={run.status} />
                </strong>
              </p>
              {run.error && <p className="error">{run.error}</p>}
              {run.start_after_prepare &&
                ['pending_preparation', 'preparing'].includes(run.status) && (
                  <p>Training starts by itself when preparation succeeds.</p>
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
                            {item.bytes !== undefined &&
                              ` · ${sizeLabel(item.bytes)}`}
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              )}
              <div className="actions">
                {['orphaned', 'uncertain', 'interrupted'].includes(
                  run.status,
                ) && (
                  <>
                    <button
                      disabled={busy}
                      onClick={() => void action('reconcile')}
                    >
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
                                This ends the recovered processes belonging to
                                this run. Unsaved work may be lost.
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
                {['training', 'generating', 'stopping'].includes(
                  run.status,
                ) && (
                  <>
                    <button
                      disabled={busy || run.status === 'stopping'}
                      onClick={() => void action('stop', { force: false })}
                    >
                      Request graceful stop
                    </button>
                    <button
                      className="danger"
                      disabled={busy}
                      onClick={() =>
                        void confirmDialog({
                          title: 'Force terminate this Run?',
                          body: (
                            <p>
                              This ends the Run's whole process tree. Unsaved
                              checkpoints and optimizer state may be lost.
                            </p>
                          ),
                          confirmLabel: 'Force terminate',
                          danger: true,
                        }).then((confirmed) => {
                          if (confirmed)
                            void action('stop', {
                              force: true,
                              confirmed: true,
                            });
                        })
                      }
                    >
                      Force terminate
                    </button>
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
                {[
                  'pending_preparation',
                  'pending_training',
                  'preparing',
                ].includes(run.status) && (
                  <button disabled={busy} onClick={() => void action('cancel')}>
                    Cancel preparation
                  </button>
                )}
                {['failed', 'cancelled', 'interrupted'].includes(
                  run.status,
                ) && (
                  <button disabled={busy} onClick={() => void action('retry')}>
                    Retry preparation
                  </button>
                )}
                <button
                  className="danger"
                  disabled={busy}
                  onClick={() => void deleteRun()}
                >
                  {run.status === 'deleting' ? 'Delete again' : 'Delete run'}
                </button>
              </div>
              {run.stages.length > 0 && (
                <ol className="run-stepper" aria-label="Run stages">
                  {runSteps(run).map((step) => (
                    <li
                      key={step.label}
                      className={step.state}
                      aria-current={
                        step.state === 'current' ? 'step' : undefined
                      }
                    >
                      <strong>{step.label}</strong>
                      <small>{step.detail}</small>
                    </li>
                  ))}
                </ol>
              )}
              <TrainingProgress
                run={run}
                progress={current?.progress ?? null}
              />
              <details>
                <summary>
                  Inputs saved with this Run ({run.manifest.items.length}{' '}
                  included items)
                </summary>
                <pre>{JSON.stringify(run.manifest, null, 2)}</pre>
              </details>
              <p>Output: {run.directory}</p>
              <details>
                <summary>Full Run ID</summary>
                <code>{run.id}</code>
              </details>
            </section>
          )}
          {run && (
            <details className="panel" open={run.status === 'failed'}>
              <summary>Stage logs</summary>
              <pre aria-label="Run logs">
                {current?.text || 'Waiting for stage output…'}
              </pre>
            </details>
          )}
          {showFiles && (
            <SavedStates
              key={`states-${run.id}`}
              runId={run.id}
              files={stateFiles(outputs)}
              onRestore={(stateId, steps) =>
                action('restore-state', {
                  state_id: stateId,
                  additional_steps: steps,
                  confirmed: true,
                })
              }
            />
          )}
          {showFiles && (
            <Outputs
              key={`outputs-${run.id}`}
              runId={run.id}
              outputs={outputs}
            />
          )}
        </div>
      </div>
    </>
  );
}
