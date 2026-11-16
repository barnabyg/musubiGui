// Which edits in a newer Job revision a resumed segment takes, by tier, as the backend's
// resume compatibility check reports them for Job review and Run details.
export type ResumeTier = 'editable' | 'warned' | 'new_run';

export type ResumeChange = {
  field: string;
  label: string;
  tier: ResumeTier;
  before: unknown;
  after: unknown;
};

export type ResumeCompatibility = {
  compatible: boolean;
  frozen_revision: number;
  revision: number;
  // Unknown in Job review until Inspect saved states has recorded the Run's states.
  saved_step: number | null;
  target_steps: number;
  changes: ResumeChange[];
  blocking: string[];
  errors: string[];
  // Why the warned changes matter; resuming asks the user to acknowledge them.
  warnings: string[];
  notes: string[];
  // Job review: the Job's latest resumable training.
  run_id?: string;
  run_status?: string;
  // Run details: the saved state a resume would use.
  state_id?: string;
  state_name?: string;
};

const tiers: { tier: ResumeTier; title: string }[] = [
  { tier: 'editable', title: 'Applies on resume' },
  {
    tier: 'warned',
    title: 'Applies on resume, but changes what the LoRA learns',
  },
  { tier: 'new_run', title: 'Needs a new Run' },
];

function valueLabel(value: unknown) {
  if (value === null || value === undefined) return 'default';
  if (typeof value === 'boolean') return value ? 'on' : 'off';
  if (typeof value === 'string') return `“${value}”`;
  if (typeof value === 'number') return String(value);
  return JSON.stringify(value);
}

// The acknowledgement a resume confirmation requires when the revision has warned changes.
export const WARNED_ACKNOWLEDGEMENT =
  'I understand these changes change what the LoRA learns';

export function hasWarnedChanges(result: ResumeCompatibility) {
  return result.changes.some((change) => change.tier === 'warned');
}

export function changeText(change: ResumeChange) {
  return `${change.label}: ${valueLabel(change.before)} → ${valueLabel(change.after)}`;
}

export function ResumeWarnings({ result }: { result: ResumeCompatibility }) {
  return (
    <>
      {result.warnings.map((warning) => (
        <p key={warning} className="resume-warning">
          {warning}
        </p>
      ))}
    </>
  );
}

export function ResumeChanges({ result }: { result: ResumeCompatibility }) {
  return (
    <div className="resume-changes">
      {result.errors.length > 0 && (
        <ul className="error" aria-label="Resume problems">
          {result.errors.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      )}
      {result.changes.length === 0 ? (
        <p>No changes from revision {result.frozen_revision}.</p>
      ) : (
        tiers.map(({ tier, title }) => {
          const changes = result.changes.filter(
            (change) => change.tier === tier,
          );
          return (
            changes.length > 0 && (
              <div key={tier}>
                <h4>{title}</h4>
                <ul aria-label={title}>
                  {changes.map((change) => (
                    <li key={change.field}>{changeText(change)}</li>
                  ))}
                </ul>
                {tier === 'warned' && <ResumeWarnings result={result} />}
              </div>
            )
          );
        })
      )}
      {result.notes.map((note) => (
        <p key={note} className="hint">
          {note}
        </p>
      ))}
    </div>
  );
}
