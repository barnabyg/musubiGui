import { useState } from 'react';
import { apiClient } from './api';
import { confirmDialog } from './ConfirmDialog';
import type { Output } from './Outputs';
import { sizeLabel } from './runStatus';
import type { Run } from './runTypes';

const request = apiClient('Run request failed.');

// The Resume training panel: inspect a Run's saved optimizer states and restore one as a new segment.
export function SavedStates({
  runId,
  files,
  config,
  onRestore,
}: {
  runId: string;
  files: Output[];
  config: Run['job']['config'];
  onRestore: (stateId: string, steps: number) => Promise<void>;
}) {
  const [states, setStates] = useState<
    {
      id: string;
      name: string;
      valid: boolean;
      error: string;
      optimizer_step?: number;
      paired?: boolean;
      counts?: { low: number; high: number };
    }[]
  >([]);
  const [steps, setSteps] = useState(1);
  const [error, setError] = useState('');
  const [inspecting, setInspecting] = useState(false);
  const [inspected, setInspected] = useState(false);
  async function inspect() {
    // Inspection hashes every state file, which takes seconds for large folders.
    setInspecting(true);
    try {
      setStates(await request(`/api/runs/${runId}/states`));
      setInspected(true);
      setError('');
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setInspecting(false);
    }
  }
  return (
    <section className="panel" aria-label="Resume training">
      <h2>Resume training from saved state</h2>
      {/* Reference text: closed by default so the controls come first. */}
      <details>
        <summary>How resuming works</summary>
        <p>
          Restoration starts a new segment with saved optimizer, scheduler and
          RNG state. Upstream resets its displayed step counter and data
          position. A LoRA checkpoint alone cannot restore training state.
          Stopping does not guarantee a checkpoint or state save.
        </p>
        <p>
          After stopping or restarting the app, inspect saved states, choose the
          additional steps to train and select Restore as new segment. Work
          since the last saved state must run again. The original Run stays in
          history.
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
      {inspected && states.length === 0 && !error && (
        <p>
          No saved optimizer states found. A LoRA alone cannot resume optimizer
          state. Start a new Run and enable Save optimizer/training state for
          future Runs.
        </p>
      )}
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
          {state.valid
            ? state.counts
              ? `low-noise ${state.counts.low} steps · high-noise ${state.counts.high} steps`
              : `optimizer step ${state.optimizer_step}`
            : state.error}
          <button
            disabled={!state.valid || !Number.isInteger(steps) || steps < 1}
            onClick={() =>
              void confirmDialog({
                title: 'Restore this optimizer state as a new segment?',
                body: (
                  <p>
                    {state.paired &&
                      'Both expert states and the alternating schedule continue from this saved position. New paired runs extend each expert by the requested steps after catching up an interrupted pair; legacy runs count total updates. '}
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
