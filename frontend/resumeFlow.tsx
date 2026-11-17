import { apiClient } from './api';
import { confirmDialog } from './ConfirmDialog';
import {
  ResumePreparationSummary,
  ResumeWarnings,
  WARNED_ACKNOWLEDGEMENT,
  changeText,
  hasWarnedChanges,
  type ResumeCompatibility,
} from './ResumeCompatibility';

const request = apiClient('Resume check failed.');

// What a resume posts: the checked revision, total and saved state.
export type ResumeBody = {
  job_revision: number;
  target_steps: number;
  state_id?: string;
};

// Whether `revision` of the Run's Job (its latest when omitted) can resume the Run's latest
// valid saved state, until the revision's steps. Inspects, so hashes, the saved states.
export function checkResume(runId: string, revision?: number) {
  return request<ResumeCompatibility>(
    `/api/runs/${runId}/resume-check`,
    'POST',
    revision === undefined ? {} : { job_revision: revision },
  );
}

export function resumeBody(result: ResumeCompatibility): ResumeBody {
  return {
    job_revision: result.revision,
    target_steps: result.target_steps,
    state_id: result.state_id,
  };
}

// A compatible resume needs a word from the user only when it changes what the LoRA learns
// or first prepares the changed dataset items.
export function resumeNeedsConfirmation(result: ResumeCompatibility) {
  return hasWarnedChanges(result) || result.reprepare;
}

// Confirms resuming `result`'s saved state with its revision, showing the re-prepare work and
// asking for an acknowledgement of changes to what the LoRA learns.
export function confirmResume(
  result: ResumeCompatibility,
  { title, confirmLabel }: { title: string; confirmLabel: string },
) {
  return confirmDialog({
    title,
    body: (
      <>
        <p>
          Training continues from step {result.saved_step} until step{' '}
          {result.target_steps} with revision {result.revision}’s settings, as a
          new segment. The data order restarts. Earlier segments stay as they
          are.
        </p>
        {result.reprepare && (
          <>
            <p>
              It first takes a new snapshot of the datasets
              {result.dataset_changes?.changed
                ? ` (${result.dataset_changes.summary.join(', ')})`
                : ''}{' '}
              and caches only the items that changed.
            </p>
            {result.preparation && (
              <ResumePreparationSummary preparation={result.preparation} />
            )}
          </>
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
    acknowledge: hasWarnedChanges(result) ? WARNED_ACKNOWLEDGEMENT : undefined,
    confirmLabel,
  });
}

// Why a training can't resume: the changed fields that need a new Run, then any other problem.
export function ResumeRefusal({
  result,
  failure,
}: {
  result?: ResumeCompatibility;
  failure?: string;
}) {
  const fields = result?.changes.filter((change) => change.tier === 'new_run');
  return (
    <>
      {fields && fields.length > 0 && (
        <>
          <p>
            Revision {result!.revision} changes fields that need a new Run: the
            saved state was trained with the earlier values.
          </p>
          <ul aria-label="Changed fields that need a new Run">
            {fields.map((change) => (
              <li key={change.field}>{changeText(change)}</li>
            ))}
          </ul>
        </>
      )}
      {(Boolean(result?.errors.length) || Boolean(failure)) && (
        <ul aria-label="Why it can’t resume">
          {result?.errors.map((message) => (
            <li key={message}>{message}</li>
          ))}
          {failure && <li>{failure}</li>}
        </ul>
      )}
    </>
  );
}
