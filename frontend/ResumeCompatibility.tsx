import { sizeLabel } from './runStatus';

// Which edits in a newer Job revision a resumed segment takes, by tier, as the backend's
// resume compatibility check reports them for Job review and Run details.
export type ResumeTier = 'editable' | 'warned' | 'reprepare' | 'new_run';

export type ResumeChange = {
  field: string;
  label: string;
  tier: ResumeTier;
  before: unknown;
  after: unknown;
};

// Caption and media edits in the datasets since the Run froze them, by item name.
export type DatasetChanges = {
  changed: boolean;
  captions: string[];
  added: string[];
  removed: string[];
  replaced: string[];
  summary: string[];
};

type CacheWork = { reuse: number; rebuild: string[] };

// The caching a refreshed segment does before it trains, and the disk that needs.
export type ResumePreparation = {
  items: number;
  stages: ('latent_cache' | 'text_cache')[];
  latent_cache: CacheWork;
  text_cache: CacheWork;
  estimate: {
    media_snapshot: number;
    copied_caches: number;
    new_caches: number;
  };
  volumes: {
    volume: string;
    roots: string[];
    needed: number;
    margin: number;
    free: number | null;
    shortfall: number;
    warning: string;
  }[];
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
  // Whether resuming snapshots the datasets again and caches what changed.
  reprepare: boolean;
  dataset_changes: DatasetChanges | null;
  preparation: ResumePreparation | null;
  // Job review: the Run Start resumes in the Job's current training.
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
  { tier: 'reprepare', title: 'Resume with re-prepare' },
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

function cacheWork(label: string, work: CacheWork) {
  if (work.rebuild.length === 0)
    return `${label}: reuses all ${work.reuse} items’ caches.`;
  const names = work.rebuild.slice(0, 5).join(', ');
  const more =
    work.rebuild.length > 5 ? ` and ${work.rebuild.length - 5} more` : '';
  const count = `${work.rebuild.length} ${work.rebuild.length === 1 ? 'item' : 'items'}`;
  return `${label}: caches ${count} (${names}${more}), reuses ${work.reuse}.`;
}

// The extra preparation and disk use a refreshed segment needs before training continues.
export function ResumePreparationSummary({
  preparation,
}: {
  preparation: ResumePreparation;
}) {
  const { estimate } = preparation;
  return (
    <div role="group" aria-label="Preparation before resuming">
      <h4>Preparation before resuming</h4>
      <ul aria-label="Preparation work">
        <li>
          {preparation.stages.length === 0
            ? 'No caching runs: every item’s caches are copied from this Run.'
            : `Runs ${preparation.stages
                .map((stage) =>
                  stage === 'latent_cache' ? 'latent caching' : 'text caching',
                )
                .join(' and ')} for the changed items only.`}
        </li>
        <li>{cacheWork('Latents', preparation.latent_cache)}</li>
        <li>{cacheWork('Text', preparation.text_cache)}</li>
      </ul>
      <ul aria-label="Extra disk use">
        <li>
          New media snapshot: {sizeLabel(estimate.media_snapshot)} (
          {preparation.items} items)
        </li>
        <li>Copied caches: {sizeLabel(estimate.copied_caches)}</li>
        <li>New caches (estimated): {sizeLabel(estimate.new_caches)}</li>
        {preparation.volumes.map((volume) => (
          <li key={volume.volume}>
            {volume.volume} ({volume.roots.join(', ')}): needs about{' '}
            {sizeLabel(volume.needed + volume.margin)} including a{' '}
            {sizeLabel(volume.margin)} margin,{' '}
            {volume.free === null
              ? 'free space unknown'
              : `${sizeLabel(volume.free)} free`}
          </li>
        ))}
      </ul>
      {preparation.volumes
        .filter((volume) => volume.warning)
        .map((volume) => (
          <p key={volume.volume} className="resume-warning">
            {volume.warning}
          </p>
        ))}
    </div>
  );
}

export function ResumeChanges({ result }: { result: ResumeCompatibility }) {
  // Caption and media edits change no field of the Job, so they are listed beside the re-prepared fields.
  const contents = result.dataset_changes?.changed
    ? result.dataset_changes.summary
    : [];
  return (
    <div className="resume-changes">
      {result.errors.length > 0 && (
        <ul className="error" aria-label="Resume problems">
          {result.errors.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      )}
      {result.changes.length === 0 && contents.length === 0 ? (
        <p>No changes from revision {result.frozen_revision}.</p>
      ) : (
        tiers.map(({ tier, title }) => {
          const changes = result.changes.filter(
            (change) => change.tier === tier,
          );
          const lines = [
            ...changes.map((change) => ({
              key: change.field,
              text: changeText(change),
            })),
            ...(tier === 'reprepare'
              ? contents.map((line) => ({
                  key: line,
                  text: `Dataset contents: ${line}`,
                }))
              : []),
          ];
          return (
            lines.length > 0 && (
              <div key={tier}>
                <h4>{title}</h4>
                <ul aria-label={title}>
                  {lines.map((line) => (
                    <li key={line.key}>{line.text}</li>
                  ))}
                </ul>
                {tier === 'warned' && <ResumeWarnings result={result} />}
                {tier === 'reprepare' && result.preparation && (
                  <ResumePreparationSummary preparation={result.preparation} />
                )}
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
