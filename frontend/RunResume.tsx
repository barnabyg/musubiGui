import { useEffect, useId, useRef, useState } from 'react';
import { apiClient } from './api';
import { confirmDialog } from './ConfirmDialog';
import { ResumeChanges, type ResumeCompatibility } from './ResumeCompatibility';
import { checkResume, confirmResume, resumeBody } from './resumeFlow';
import type { Run } from './runTypes';

const request = apiClient('Could not check saved states. Try again.');

type SavedState = {
  id: string;
  name: string;
  valid: boolean;
  error: string;
  counts?: { low: number; high: number };
  // The global step a restored segment continues from.
  resume_step?: number;
};

type UsableSave = SavedState & { resume_step: number };

function saveLabel(state: UsableSave) {
  return state.counts
    ? `Step ${state.resume_step} · low-noise ${state.counts.low} steps · high-noise ${state.counts.high} steps`
    : `Optimizer step ${state.resume_step}`;
}

export function RunResume({
  run,
  allowEditedRevision,
  busy,
  onAction,
}: {
  run: Run;
  allowEditedRevision: boolean;
  busy: boolean;
  onAction: (name: string, body: object) => Promise<void>;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [states, setStates] = useState<SavedState[]>();
  const [choice, setChoice] = useState('');
  const [edited, setEdited] = useState(false);
  const [steps, setSteps] = useState(run.job.config?.steps ?? 1);
  const [error, setError] = useState('');
  const [result, setResult] = useState<ResumeCompatibility>();
  const [waiting, setWaiting] = useState<'states' | 'revision' | 'confirm'>();
  const operation = useRef<AbortController>(null);
  useEffect(() => () => operation.current?.abort(), []);

  const usable =
    states?.filter(
      (state): state is UsableSave =>
        state.valid && state.resume_step !== undefined,
    ) ?? [];
  // The furthest-trained save, with the last listed save winning ties.
  const latest = usable.reduce<UsableSave | undefined>(
    (best, state) =>
      !best || state.resume_step >= best.resume_step ? state : best,
    undefined,
  );
  const state = usable.find((save) => save.id === choice) ?? latest;
  const unavailable = states?.filter((save) => !save.valid) ?? [];
  const useEditedRevision = allowEditedRevision && edited;
  const disabled = busy || waiting !== undefined;
  const validTarget =
    state && Number.isInteger(steps) && steps > state.resume_step;

  async function perform(
    kind: NonNullable<typeof waiting>,
    work: (signal: AbortSignal) => Promise<void>,
  ) {
    if (operation.current) return;
    const controller = new AbortController();
    operation.current = controller;
    setWaiting(kind);
    setError('');
    setResult(undefined);
    try {
      await work(controller.signal);
    } catch (reason) {
      if (!controller.signal.aborted) setError((reason as Error).message);
    } finally {
      operation.current = null;
      if (!controller.signal.aborted) setWaiting(undefined);
    }
  }

  async function inspect() {
    setStates(undefined);
    setChoice('');
    await perform('states', async (signal) => {
      const found = await request<SavedState[]>(
        `/api/runs/${run.id}/states`,
        'GET',
        undefined,
        signal,
      );
      if (!signal.aborted) setStates(found);
    });
  }

  async function review() {
    if (!state) return;
    if (useEditedRevision) {
      await perform('revision', async (signal) => {
        const checked = await checkResume(run.id, undefined, {
          stateId: state.id,
          signal,
        });
        if (signal.aborted) return;
        setResult(checked);
        setWaiting('confirm');
        if (
          checked.compatible &&
          (await confirmResume(checked, {
            title: `Resume with revision ${checked.revision}?`,
            confirmLabel: `Resume with revision ${checked.revision}`,
          })) &&
          !signal.aborted
        ) {
          await onAction('resume', resumeBody(checked));
        }
      });
    } else if (validTarget) {
      await perform('confirm', async (signal) => {
        const confirmed = await confirmDialog({
          title: 'Restore this save as a new segment?',
          body: (
            <p>
              Training continues from saved step {state.resume_step} until step{' '}
              {steps} with revision {run.job.revision}’s settings.
              {state.counts &&
                ' Both experts continue from the selected save.'}{' '}
              The data order restarts.
            </p>
          ),
          confirmLabel: 'Restore as new segment',
        });
        if (confirmed && !signal.aborted) {
          await onAction('restore-state', {
            state_id: state.id,
            target_steps: steps,
            confirmed: true,
          });
        }
      });
    }
  }

  return (
    <div role="group" aria-label="Resume training" className="run-resume">
      <button
        type="button"
        className="primary"
        aria-expanded={open}
        aria-controls={id}
        disabled={disabled}
        onClick={() => {
          setOpen(!open);
          if (!open) void inspect();
        }}
      >
        Resume
      </button>
      <div id={id} hidden={!open}>
        {waiting === 'states' && <p role="status">Checking saved states…</p>}
        {!states && error && (
          <>
            <p role="alert">{error}</p>
            <button disabled={disabled} onClick={() => void inspect()}>
              Retry saved-state check
            </button>
          </>
        )}
        {states && !state && (
          <p role="status">
            No valid saved optimizer state found. Enable Save optimizer/training
            state in the Job to make future Runs resumable.
          </p>
        )}
        {state && (
          <>
            <p>Selected save · {saveLabel(state)}</p>
            {usable.length > 1 && (
              <details>
                <summary>Choose an earlier save</summary>
                <label className="field">
                  Saved state
                  <select
                    value={choice}
                    disabled={disabled}
                    onChange={(event) => {
                      setChoice(event.target.value);
                      setResult(undefined);
                      setError('');
                    }}
                  >
                    <option value="">
                      Latest save · {latest && saveLabel(latest)}
                    </option>
                    {usable
                      .filter((save) => save !== latest)
                      .map((save) => (
                        <option key={save.id} value={save.id}>
                          {saveLabel(save)} · {save.name}
                        </option>
                      ))}
                  </select>
                </label>
              </details>
            )}
            {allowEditedRevision && (
              <label className="field">
                Job revision
                <select
                  value={useEditedRevision ? 'edited' : 'frozen'}
                  disabled={disabled}
                  onChange={(event) => {
                    setEdited(event.target.value === 'edited');
                    setResult(undefined);
                    setError('');
                  }}
                >
                  <option value="frozen">
                    This Run’s revision ({run.job.revision})
                  </option>
                  <option value="edited">Latest Job revision</option>
                </select>
              </label>
            )}
            {!useEditedRevision && (
              <>
                <label className="field number-field">
                  Train until step
                  <input
                    type="number"
                    min={state.resume_step + 1}
                    disabled={disabled}
                    value={steps || ''}
                    onChange={(event) => setSteps(Number(event.target.value))}
                  />
                </label>
                {!validTarget && (
                  <p>Choose a whole-number step after {state.resume_step}.</p>
                )}
              </>
            )}
            <button
              disabled={disabled || (!useEditedRevision && !validTarget)}
              onClick={() => void review()}
            >
              Review resume
            </button>
            {waiting === 'revision' && (
              <p role="status">
                Checking the saved state and the latest Job revision…
              </p>
            )}
            {error && <p role="alert">{error}</p>}
            {result && (
              <>
                <p>
                  Revision {result.revision} · from step {result.saved_step}{' '}
                  until step {result.target_steps}.{' '}
                  {result.compatible
                    ? 'This revision can resume the saved state.'
                    : 'This revision can’t resume the saved state.'}
                </p>
                <ResumeChanges result={result} />
              </>
            )}
          </>
        )}
        {unavailable.length > 0 && (
          <details>
            <summary>Unavailable saves</summary>
            <ul>
              {unavailable.map((save) => (
                <li key={save.id}>
                  {save.name} · {save.error}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  );
}
