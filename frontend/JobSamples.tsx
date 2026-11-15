import { JobSection } from './JobSection';
import type { JobFields } from './jobFields';
import { SampleGeometry } from './SampleGeometry';
import { fieldId } from './jobRules';
import type { Job, SetConfig } from './jobTypes';

export function JobSamples({
  job,
  busy,
  fields,
  config,
}: {
  job: Job;
  busy: boolean;
  fields: JobFields;
  config: SetConfig;
}) {
  const enabled = job.config.sample_enabled || job.config.preview_every > 0;
  return (
    <JobSection
      className="panel"
      id="job-section-samples"
      aria-label="Samples configuration"
    >
      <h2>Samples</h2>
      <p>
        Periodic previews and the final sample share the prompt, seed, inference
        steps and resolution. They can be enabled independently; effective
        durations are shown below.
      </p>
      <p className="hint">
        Standalone previews and final samples select their GPU memory preset
        independently from training, with substantial reserved headroom. Unknown
        hardware or unqualified sizes use the conservative family preset. In-run
        previews retain the training model's residency.
      </p>
      {fields.toggle('Generate a final sample', 'sample_enabled')}
      {fields.number(
        'Preview every N steps',
        'preview_every',
        '0 turns off in-run previews. Counts optimizer updates. Previews pause training while they render.',
      )}
      {job.config.preview_every > 0 && job.family === 'wan' && (
        <p className="hint">
          {job.config.recipe === 'alternating'
            ? 'Paired previews unload training, sample both experts, then reload training state. Extra boundaries restart the shuffled dataset pass. An expert without updates uses base weights.'
            : 'In-run Wan previews use the low-noise expert only. The final sample uses both experts.'}
        </p>
      )}
      {enabled && (
        <>
          <label className="field">
            Generation memory policy
            <select
              aria-label="Generation memory policy"
              disabled={busy}
              value={job.config.sample_memory_policy ?? 'automatic'}
              onChange={(event) =>
                config(
                  'sample_memory_policy',
                  event.target.value === 'conservative'
                    ? 'conservative'
                    : 'automatic',
                )
              }
            >
              <option value="automatic">
                Automatic with reserved headroom
              </option>
              <option value="conservative">Conservative family preset</option>
            </select>
          </label>
          <label className="field">
            Sample prompt
            <textarea
              id={fieldId('sample_prompt')}
              aria-label="Sample prompt"
              {...fields.problemProps('sample_prompt')}
              disabled={busy}
              value={job.config.sample_prompt}
              onChange={(event) => config('sample_prompt', event.target.value)}
            />
            {fields.problemText('sample_prompt')}
          </label>
          {fields.number('Sample inference steps', 'sample_steps')}
          <SampleGeometry
            job={job}
            busy={busy}
            fields={fields}
            config={config}
          />
        </>
      )}
      {fields.number(
        'Seed',
        'seed',
        'Shared by training and sample generation.',
      )}
    </JobSection>
  );
}
