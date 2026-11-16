import { useState } from 'react';
import { apiClient } from './api';
import { confirmDialog } from './ConfirmDialog';
import {
  ResumeChanges,
  ResumeWarnings,
  WARNED_ACKNOWLEDGEMENT,
  changeText,
  hasWarnedChanges,
  type ResumeCompatibility,
} from './ResumeCompatibility';
import type { Run } from './runTypes';

const request = apiClient('Resume check failed.');

// Resume a stopped or completed Run's latest saved state with the Job's latest revision,
// after showing what that revision changes and whether a saved state can take it.
export function ResumeRevision({
  run,
  busy,
  onResume,
}: {
  run: Run;
  busy: boolean;
  onResume: (body: {
    job_revision: number;
    target_steps: number;
    state_id?: string;
  }) => Promise<void>;
}) {
  const [result, setResult] = useState<ResumeCompatibility>();
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);
  async function check() {
    // The check inspects saved states, which hashes every state file.
    setChecking(true);
    try {
      setResult(
        await request<ResumeCompatibility>(
          `/api/runs/${run.id}/resume-check`,
          'POST',
          {},
        ),
      );
      setError('');
    } catch (reason) {
      setResult(undefined);
      setError((reason as Error).message);
    } finally {
      setChecking(false);
    }
  }
  return (
    <section aria-label="Resume with the latest revision">
      <h3>Resume with the Job’s latest revision</h3>
      <p className="hint">
        Edit the Job, for example its sample prompt or training steps, then
        check its latest revision against revision {run.job.revision} that this
        Run trained. A compatible revision resumes the latest saved state with
        its settings.
      </p>
      <button disabled={checking} onClick={() => void check()}>
        Check latest revision
      </button>
      {checking && <p role="status">Checking saved states and revision…</p>}
      {error && <p role="alert">{error}</p>}
      {result && (
        <>
          <p>
            Revision {result.revision} · {result.state_name} · from step{' '}
            {result.saved_step} until step {result.target_steps}.{' '}
            {result.compatible
              ? 'This revision can resume the saved state.'
              : 'This revision can’t resume the saved state.'}
          </p>
          <ResumeChanges result={result} />
          <button
            disabled={!result.compatible || busy}
            onClick={() =>
              void confirmDialog({
                title: `Resume with revision ${result.revision}?`,
                body: (
                  <>
                    <p>
                      Training continues from step {result.saved_step} until
                      step {result.target_steps} with revision {result.revision}
                      ’s settings, as a new segment. The data order restarts.
                      This Run stays as it is.
                    </p>
                    {result.reprepare && (
                      <p>
                        It first takes a new snapshot of the datasets
                        {result.dataset_changes?.changed
                          ? ` (${result.dataset_changes.summary.join(', ')})`
                          : ''}{' '}
                        and caches only the items that changed.
                      </p>
                    )}
                    {hasWarnedChanges(result) && (
                      <>
                        <ul aria-label="Changes that change what the LoRA learns">
                          {result.changes
                            .filter((change) => change.tier === 'warned')
                            .map((change) => (
                              <li key={change.field}>{changeText(change)}</li>
                            ))}
                        </ul>
                        <ResumeWarnings result={result} />
                      </>
                    )}
                  </>
                ),
                acknowledge: hasWarnedChanges(result)
                  ? WARNED_ACKNOWLEDGEMENT
                  : undefined,
                confirmLabel: `Resume with revision ${result.revision}`,
              }).then((confirmed) => {
                if (confirmed)
                  void onResume({
                    job_revision: result.revision,
                    target_steps: result.target_steps,
                    state_id: result.state_id,
                  });
              })
            }
          >
            Resume with revision {result.revision}
          </button>
        </>
      )}
    </section>
  );
}
