import { focusField, type JobFields } from './jobFields';
import { fieldId } from './jobRules';
import type { Job, SetConfig } from './jobTypes';

export function JobEssentials({
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
  const choices: { field: string; message: string }[] = [];
  if (!job.name.trim())
    choices.push({ field: 'name', message: 'Enter a job name' });
  if (!job.datasets.length)
    choices.push({
      field: 'dataset_id',
      message: 'Select at least one dataset',
    });
  if (fields.problems.steps)
    choices.push({
      field: 'steps',
      message: 'Enter a valid training step count',
    });
  if (job.config.sample_enabled && !job.config.sample_prompt.trim())
    choices.push({
      field: 'sample_prompt',
      message: 'Enter a sample prompt or turn off the final sample',
    });
  return (
    <section className="panel" id="job-section-essentials" tabIndex={-1}>
      <h2>Training & sample</h2>
      <section aria-label="Required choices">
        {choices.length ? (
          <>
            <h3>Before review</h3>
            <ul>
              {choices.map(({ field, message }) => (
                <li key={field}>
                  <button
                    className="error-link"
                    disabled={busy}
                    onClick={() => focusField(field)}
                  >
                    {message}
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p>Required choices are complete.</p>
        )}
        <p className="hint">
          Review checks model files, media and all saved settings before
          preparation.
        </p>
      </section>
      <p className="hint">
        Current recipe settings: {job.config.width} × {job.config.height} ·{' '}
        {job.config.frames} frames · LoRA rank {job.config.rank}. New jobs start
        from the measured target-PC defaults.
      </p>
      {fields.number(
        'Training steps',
        'steps',
        job.config.recipe === 'alternating'
          ? 'Total optimizer updates across both experts, not steps per expert.'
          : undefined,
      )}
      {fields.toggle('Generate a final sample', 'sample_enabled')}
      {job.config.sample_enabled && (
        <label className="field">
          Sample prompt
          <textarea
            id={fieldId('sample_prompt')}
            {...fields.problemProps('sample_prompt')}
            disabled={busy}
            value={job.config.sample_prompt}
            onChange={(event) => config('sample_prompt', event.target.value)}
          />
          {fields.problemText('sample_prompt')}
        </label>
      )}
    </section>
  );
}
