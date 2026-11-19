import { useEffect, useRef, useState } from 'react';
import { apiClient } from './api';
import { confirmDialog } from './ConfirmDialog';
import type { Run } from './runTypes';

const request = apiClient('Run request failed.');

type SavedState = {
  id: string;
  name: string;
  valid: boolean;
  error: string;
  optimizer_step?: number;
  paired?: boolean;
  counts?: { low: number; high: number };
  // The step a segment restored from this state continues numbering from.
  resume_step?: number;
};

const noStates =
  'A LoRA alone cannot resume optimizer state. Start a new Run and enable Save optimizer/training state for future Runs.';

// Resume from an earlier save: inspect a Run's saved optimizer states and restore one, with this
// Run's own revision, as a new segment.
export function SavedStates({
  runId,
  config,
  canResume,
  busy,
  onRestore,
}: {
  runId: string;
  config: Run['job']['config'];
  canResume: boolean;
  busy: boolean;
  onRestore: (stateId: string, steps: number) => Promise<void>;
}) {
  const [states, setStates] = useState<SavedState[]>();
  // A total, not an increment: defaults to the frozen Job's step count.
  const [steps, setSteps] = useState(config?.steps ?? 1);
  const [error, setError] = useState('');
  const [inspecting, setInspecting] = useState(false);
  const inspection = useRef<AbortController>(null);
  useEffect(() => () => inspection.current?.abort(), []);
  const validTarget = Number.isInteger(steps) && steps >= 1;

  async function restore(state: SavedState, signal?: AbortSignal) {
    const confirmed = await confirmDialog({
      title: 'Restore this optimizer state as a new segment?',
      body: (
        <p>
          {state.paired &&
            'Both expert states and the alternating schedule continue from this saved position. New paired runs train each expert until the chosen step after catching up an interrupted pair; legacy runs count total updates. '}
          Training continues until step {steps}; step numbering continues from{' '}
          {state.resume_step === undefined
            ? 'the saved step'
            : `saved step ${state.resume_step}`}
          . The data order restarts. Original run inputs are preserved.
        </p>
      ),
      confirmLabel: 'Restore as new segment',
    });
    if (confirmed && !signal?.aborted) await onRestore(state.id, steps);
  }

  async function inspect(resumeLatest: boolean) {
    // Inspection hashes every state file, which takes seconds for large folders.
    const controller = new AbortController();
    inspection.current = controller;
    setInspecting(true);
    setError('');
    let found: SavedState[];
    try {
      found = await request(
        `/api/runs/${runId}/states`,
        'GET',
        undefined,
        controller.signal,
      );
    } catch (reason) {
      if (!controller.signal.aborted) setError((reason as Error).message);
      return;
    } finally {
      if (!controller.signal.aborted) setInspecting(false);
    }
    if (controller.signal.aborted) return;
    if (!resumeLatest) {
      setStates(found);
      return;
    }
    // Match Resume in Actions: furthest trained, with the last listed save winning ties.
    let latest: SavedState | undefined;
    let latestStep = -1;
    for (const state of found) {
      if (
        state.valid &&
        state.resume_step !== undefined &&
        state.resume_step >= latestStep
      ) {
        latest = state;
        latestStep = state.resume_step;
      }
    }
    if (!latest) {
      setError(`No valid saved optimizer state found. ${noStates}`);
    } else if (steps <= latestStep) {
      setError(
        `Choose a step after ${latestStep} to resume the latest saved state.`,
      );
    } else {
      await restore(latest, controller.signal);
    }
  }
  return (
    <section className="panel" aria-label="Resume from an earlier save">
      <h2>Resume from an earlier save</h2>
      {/* Reference text: closed by default so the controls come first. */}
      <details>
        <summary>How resuming works</summary>
        <p>
          Restoration starts a new segment with saved optimizer, scheduler and
          RNG state. Step numbering continues from the saved step, so
          checkpoints, states and previews are named for the overall step. The
          data order restarts from the beginning of the dataset. A LoRA
          checkpoint alone cannot restore training state. Stopping does not
          guarantee a checkpoint or state save.
        </p>
        <p>
          Resume continues from the latest valid save with this Run’s revision.
          To choose a different save, select Inspect saved states, set the step
          to train until and select Restore as new segment. Work since the
          chosen save must run again. The original Run stays in history.
        </p>
        {config?.save_state === false ? (
          <p>
            Saved optimizer state was not enabled for this Run. If no valid
            state is available, start a new Run. For future Runs, enable Save
            optimizer/training state in the Job before training.
          </p>
        ) : (
          <p>
            For future Runs, enable Save optimizer/training state in the Job and
            choose a checkpoint interval to control how much work can be lost.
            {config?.save_state && config.save_every
              ? ` This Run saves state every ${config.save_every} training steps.`
              : ''}
          </p>
        )}
      </details>
      <div className="actions">
        <button
          disabled={!canResume || busy || inspecting || !validTarget}
          onClick={() => void inspect(true)}
        >
          Resume
        </button>
        <button
          disabled={busy || inspecting}
          onClick={() => void inspect(false)}
        >
          Inspect saved states
        </button>
      </div>
      {inspecting && <p role="status">Checking saved states…</p>}
      {error && <p role="alert">{error}</p>}
      {states?.length === 0 && !error && (
        <p>No saved optimizer states found. {noStates}</p>
      )}
      <label className="field number-field">
        Train until step
        <input
          type="number"
          min={1}
          disabled={busy || inspecting}
          value={steps}
          onChange={(event) => {
            setSteps(Number(event.target.value));
            setError('');
          }}
        />
      </label>
      {states?.map((state) => (
        <p key={state.id}>
          {state.name} ·{' '}
          {state.valid
            ? state.counts
              ? `low-noise ${state.counts.low} steps · high-noise ${state.counts.high} steps`
              : `optimizer step ${state.optimizer_step}`
            : state.error}
          {state.valid &&
            state.resume_step !== undefined &&
            !(steps > state.resume_step) &&
            ` · Choose a step after ${state.resume_step} to restore this state.`}
          <button
            disabled={
              busy ||
              inspecting ||
              !state.valid ||
              !validTarget ||
              (state.resume_step !== undefined && steps <= state.resume_step)
            }
            onClick={() => void restore(state)}
          >
            Restore as new segment
          </button>
        </p>
      ))}
    </section>
  );
}
