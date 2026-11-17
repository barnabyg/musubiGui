import { useState } from 'react';
import { ResumeChanges, type ResumeCompatibility } from './ResumeCompatibility';
import {
  checkResume,
  confirmResume,
  resumeBody,
  type ResumeBody,
} from './resumeFlow';
import type { Run } from './runTypes';

// Run details' Resume: the Run's latest saved state, with the Job's latest revision when that
// revision can take it. An incompatible revision is explained here and nothing is resumed.
export function ResumeRevision({
  run,
  busy,
  onResume,
}: {
  run: Run;
  busy: boolean;
  onResume: (body: ResumeBody) => Promise<void>;
}) {
  const [result, setResult] = useState<ResumeCompatibility>();
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);
  async function resume() {
    // The check inspects saved states, which hashes every state file.
    setChecking(true);
    setError('');
    setResult(undefined);
    let checked: ResumeCompatibility;
    try {
      checked = await checkResume(run.id);
    } catch (reason) {
      setError((reason as Error).message);
      return;
    } finally {
      setChecking(false);
    }
    setResult(checked);
    if (
      checked.compatible &&
      (await confirmResume(checked, {
        title: `Resume with revision ${checked.revision}?`,
        confirmLabel: `Resume with revision ${checked.revision}`,
      }))
    )
      await onResume(resumeBody(checked));
  }
  return (
    <div role="group" aria-label="Resume with the latest revision">
      <p className="hint">
        Resume continues this Run’s latest saved state with the Job’s latest
        revision, for example after editing its sample prompt or training steps,
        when that revision can take it.
      </p>
      <div className="actions">
        <button
          disabled={busy || checking}
          className="primary"
          onClick={() => void resume()}
        >
          Resume
        </button>
      </div>
      {checking && (
        <p role="status">Checking saved states and the latest revision…</p>
      )}
      {error && <p role="alert">{error}</p>}
      {result && (
        <>
          <p>
            Revision {result.revision} · {result.state_name} · from step{' '}
            {result.saved_step} until step {result.target_steps}.{' '}
            {result.compatible
              ? 'This revision can resume the saved state.'
              : `This revision can’t resume the saved state. Choose a save in Resume from an earlier save to continue with revision ${run.job.revision}, or start a fresh training from the Job.`}
          </p>
          <ResumeChanges result={result} />
        </>
      )}
    </div>
  );
}
