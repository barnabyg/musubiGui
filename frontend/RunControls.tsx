import { confirmDialog } from './ConfirmDialog';
import type { ControlAction, Progress, Run } from './runTypes';

const LABELS: Record<ControlAction, string> = {
  save: 'Save now',
  sample: 'Sample now',
  save_and_stop: 'Save and stop',
};

function steps(count: number) {
  return `${count} ${count === 1 ? 'step' : 'steps'}`;
}

// Why Sample now can't be requested for this Run's frozen Job, if it can't.
export function sampleUnavailable(run: Run) {
  if (run.job.config?.recipe === 'alternating')
    return 'Sample now is unavailable: alternating Wan previews run as separate sampling stages.';
  if (!run.job.config?.preview_every)
    return 'Sample now is unavailable: previews are off for this Job.';
  return '';
}

// What became of the latest request, or what is waiting for the next step.
function controlNote(run: Run) {
  const pending = run.control_request;
  if (pending)
    return `${LABELS[pending.action]} requested. It runs at the next training step.`;
  const last = run.controls?.at(-1);
  if (!last) return '';
  if (last.error) return `${LABELS[last.action]} wasn't applied: ${last.error}`;
  if (last.action === 'save_and_stop')
    return `Saving at step ${last.step}, then stopping.`;
  return `${LABELS[last.action]} ran at step ${last.step}.`;
}

function confirmStop(run: Run, progress: Progress | null) {
  const unsaved = progress?.unsaved_steps;
  const body =
    run.status !== 'training'
      ? 'Sampling stops. Checkpoints and saved states already written are kept.'
      : unsaved === 0
        ? 'Nothing trained since the last save is lost.'
        : `${unsaved == null ? 'Steps' : `The ${steps(unsaved)}`} trained since the last save will be lost. Save and stop keeps them.`;
  return confirmDialog({
    title: 'Stop this Run now?',
    body: <p>{body}</p>,
    confirmLabel: 'Stop now',
  });
}

// Save now, Sample now, Save and stop, Stop now and Force terminate for an active Run.
export function RunControls({
  run,
  progress,
  busy,
  action,
}: {
  run: Run;
  progress: Progress | null;
  busy: boolean;
  action: (name: string, body?: object) => Promise<void>;
}) {
  const training = run.status === 'training';
  const waiting = Boolean(run.control_request);
  const sampleReason = sampleUnavailable(run);
  const last = run.controls?.at(-1);
  const savingToStop =
    run.status === 'stopping' &&
    last?.action === 'save_and_stop' &&
    !last.error;
  const note = training || savingToStop ? controlNote(run) : '';
  return (
    <>
      <div aria-live="polite">{note && <p>{note}</p>}</div>
      {training && sampleReason && <p>{sampleReason}</p>}
      <div className="actions run-controls">
        {training && (
          <>
            <button
              disabled={busy || waiting}
              onClick={() => void action('control', { action: 'save' })}
            >
              Save now
            </button>
            <button
              disabled={busy || waiting || Boolean(sampleReason)}
              onClick={() => void action('control', { action: 'sample' })}
            >
              Sample now
            </button>
            <button
              disabled={busy || waiting}
              onClick={() =>
                void action('control', { action: 'save_and_stop' })
              }
            >
              Save and stop
            </button>
          </>
        )}
        <button
          disabled={busy || run.status === 'stopping'}
          onClick={() =>
            void confirmStop(run, progress).then((confirmed) => {
              if (confirmed) void action('stop', { force: false });
            })
          }
        >
          Stop now
        </button>
        <button
          className="danger"
          disabled={busy}
          onClick={() =>
            void confirmDialog({
              title: 'Force terminate this Run?',
              body: (
                <p>
                  This ends the Run's whole process tree. Unsaved checkpoints
                  and optimizer state may be lost.
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
      </div>
    </>
  );
}
