import { focusField, type JobFields } from './jobFields';
import type { Job } from './jobTypes';
import type { JobMedia } from './jobMedia';

export function JobEssentials({
  job,
  busy,
  fields,
  media,
}: {
  job: Job;
  busy: boolean;
  fields: JobFields;
  media: JobMedia;
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
  if (
    (job.config.sample_enabled || job.config.preview_every > 0) &&
    !job.config.sample_prompt.trim()
  )
    choices.push({
      field: 'sample_prompt',
      message:
        job.config.preview_every > 0
          ? 'Enter a sample prompt or turn off sampling and previews'
          : 'Enter a sample prompt or turn off the final sample',
    });
  return (
    <section className="panel" id="job-section-essentials" tabIndex={-1}>
      <h2>Training</h2>
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
        {media.imageOnly
          ? '1 frame per training image'
          : `${job.config.frames} frames per video window`}{' '}
        · LoRA rank {job.config.rank}. New jobs start from the measured
        target-PC defaults.
      </p>
      {media.imageOnly && (
        <p className="hint">
          Images train as single frames.{' '}
          {job.family === 'h3'
            ? 'H3 image training is experimental; using the result for video is unvalidated.'
            : 'You can add a video dataset to train on images and videos together.'}
        </p>
      )}
      {fields.number(
        'Training steps',
        'steps',
        job.config.recipe === 'alternating'
          ? 'Total optimizer updates across both experts, not steps per expert.'
          : undefined,
      )}
    </section>
  );
}
