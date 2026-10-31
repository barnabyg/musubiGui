import type { RefObject } from 'react';
import { componentLabels } from './labels';
import type { JobFields } from './jobFields';
import { fieldId } from './jobRules';
import type { Dataset, Job, JobConfig } from './jobTypes';
import { PathField } from './PathField';

const extractionModes: {
  value: JobConfig['frame_extraction'];
  label: string;
  help: string;
}[] = [
  {
    value: 'head',
    label: 'Head: first window only',
    help: 'One window per clip, from its start. The rest of each clip is not trained.',
  },
  {
    value: 'chunk',
    label: 'Chunk: consecutive windows',
    help: 'Non-overlapping windows: a clip yields floor(length ÷ frames) windows; a shorter remainder is dropped.',
  },
  {
    value: 'slide',
    label: 'Slide: a window every stride',
    help: 'A clip yields floor((length − frames) ÷ stride) + 1 windows; a stride below the frame count overlaps them.',
  },
  {
    value: 'uniform',
    label: 'Uniform: evenly spaced windows',
    help: 'Up to the sample count of windows per long-enough clip, spread evenly from its start to its end. Positions that coincide on a short clip are cached once.',
  },
];

type SetConfig = <K extends keyof JobConfig>(
  key: K,
  value: JobConfig[K],
) => void;

export function JobIdentity({
  job,
  busy,
  fields,
  edit,
  config,
  nameField,
  datasets,
  models,
  modelRoots,
}: {
  job: Job;
  busy: boolean;
  fields: JobFields;
  edit: (next: Job) => void;
  config: SetConfig;
  nameField: RefObject<HTMLInputElement | null>;
  datasets: Dataset[];
  models: Record<string, string>;
  modelRoots: string[];
}) {
  const { problemProps, problemText } = fields;
  return (
    <section className="panel" id="job-section-job" tabIndex={-1}>
      <h2>Job & dataset</h2>
      <label className="field">
        Job name
        <input
          id={fieldId('name')}
          ref={nameField}
          disabled={busy}
          value={job.name}
          onChange={(event) => edit({ ...job, name: event.target.value })}
          {...problemProps('name')}
        />
        {problemText('name')}
      </label>
      <label className="field">
        Dataset
        <select
          id={fieldId('dataset_id')}
          {...problemProps('dataset_id')}
          disabled={busy}
          value={job.dataset_id}
          onChange={(event) => edit({ ...job, dataset_id: event.target.value })}
        >
          <option value="">Select a registered dataset</option>
          {datasets.map((dataset) => (
            <option key={dataset.id} value={dataset.id}>
              {dataset.name}
            </option>
          ))}
        </select>
        {problemText('dataset_id')}
      </label>
      <label className="field">
        Recipe
        <select
          id={fieldId('recipe')}
          {...problemProps('recipe')}
          disabled={busy}
          value={job.config.recipe}
          onChange={(event) => config('recipe', event.target.value)}
        >
          {job.family === 'wan' ? (
            <option value="low-noise">Wan low-noise expert · T2V A14B</option>
          ) : (
            <option value="guidance-loss">H3 guidance loss · T2VA</option>
          )}
        </select>
        {problemText('recipe')}
        <small>
          {job.family === 'wan' ? (
            <>
              Native FP16 experts; one expert is trained and both experts are
              used for sampling.
            </>
          ) : (
            <>
              Released FL2VA base used for the T2VA task, joint video/audio
              losses, unconditional text caching, guidance scale 4 and sigma
              minimum 0.15.
            </>
          )}
        </small>
      </label>
      <details>
        <summary>Model components</summary>
        <p>
          Paths inherit Settings unless overridden here. Overrides must remain
          inside configured model roots.
        </p>
        {(job.family === 'wan'
          ? ['wan_low', 'wan_high', 'wan_vae', 'wan_t5']
          : ['h3_dit', 'h3_video_vae', 'h3_audio_vae', 'h3_text']
        ).map((key) => (
          <PathField
            key={key}
            label={componentLabels[key]}
            inputId={fieldId(key)}
            kind="file"
            roots={modelRoots}
            disabled={busy}
            value={job.config.components[key] ?? models[key] ?? ''}
            onChange={(value) =>
              config('components', {
                ...job.config.components,
                [key]: value,
              })
            }
          />
        ))}
      </details>
    </section>
  );
}

export function JobControls({
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
  const { problems, problemProps, problemText, number, toggle } = fields;
  return (
    <>
      <section className="panel" id="job-section-dataset" tabIndex={-1}>
        <h2>Dataset preparation</h2>
        <div className="number-row">
          {number('Width', 'width')}
          {number('Height', 'height')}
          {number('Frames', 'frames')}
          {number('Repeats', 'repeats')}
          {number('Dataset batch size', 'batch_size')}
        </div>
        <p className="hint">
          {job.family === 'wan'
            ? 'Wan requires multiples of 16 and 4*n+1 frames; 33 frames is measured on the target PC.'
            : 'H3 requires multiples of 32 and 17*n+5 frames between 124 and 345; 124 frames is measured on the target PC.'}{' '}
          Keep the batch size at one for the target-PC recipe.
        </p>
        <div className="columns">
          <label className="field">
            Frame extraction
            <select
              id={fieldId('frame_extraction')}
              {...problemProps('frame_extraction')}
              value={job.config.frame_extraction}
              disabled={busy}
              onChange={(event) =>
                config(
                  'frame_extraction',
                  event.target.value as JobConfig['frame_extraction'],
                )
              }
            >
              {extractionModes.map((mode) => (
                <option
                  key={mode.value}
                  value={mode.value}
                  disabled={job.family === 'h3' && mode.value !== 'head'}
                >
                  {mode.label}
                </option>
              ))}
            </select>
            {problemText('frame_extraction')}
          </label>
          {job.config.frame_extraction === 'slide' &&
            number('Slide stride', 'frame_stride')}
          {job.config.frame_extraction === 'uniform' &&
            number('Windows per clip', 'frame_sample')}
        </div>
        <p className="hint">
          {
            extractionModes.find(
              (mode) => mode.value === job.config.frame_extraction,
            )?.help
          }{' '}
          {job.family === 'wan'
            ? 'Clip length is counted after resampling to 16 fps. Review lists each clip’s cached windows; more windows mean a larger cache and longer epochs.'
            : 'H3 uses head only: audio alignment is inspected for each clip’s first window, so other modes are not qualified.'}
        </p>
        {toggle('Enable bucketing', 'enable_bucket')}
        {toggle('Do not upscale buckets', 'bucket_no_upscale')}
        {job.family === 'h3' && (
          <label className="field">
            Audio policy
            <select
              value={job.config.audio_policy}
              disabled={busy}
              onChange={(event) =>
                config(
                  'audio_policy',
                  event.target.value as JobConfig['audio_policy'],
                )
              }
            >
              <option value="require">
                Require valid supervised audio for every included video
              </option>
              <option value="allow-missing">
                Allow absent audio; exclude missing audio from supervision
              </option>
            </select>
            <small>
              Malformed supplied audio always blocks preparation. Sources
              resolve explicit → same-stem sidecar → embedded track. Original
              media is never repaired.
            </small>
          </label>
        )}
      </section>
      <section className="panel" id="job-section-training" tabIndex={-1}>
        <h2>LoRA & training</h2>
        <div className="number-row">
          {number('LoRA rank', 'rank')}
          {number('LoRA alpha', 'alpha')}
          {number('Learning rate', 'learning_rate')}
          {number('Training steps', 'steps')}
          {number('Gradient accumulation', 'gradient_accumulation')}
          {number('Seed', 'seed')}
        </div>
      </section>
      <section className="panel" id="job-section-memory" tabIndex={-1}>
        <h2>Memory</h2>
        {number(
          'Blocks to swap',
          'blocks_to_swap',
          job.family === 'wan'
            ? '0–38 of 40 blocks; 36 is the reference default.'
            : '0–48 of 50 blocks; 48 is the reference default and upstream rejects 49. H2D-only swapping is required.',
        )}
        {toggle('Gradient checkpointing', 'gradient_checkpointing')}
        {job.family === 'wan' ? (
          <>
            {toggle(
              'Runtime FP8 scaling',
              'fp8_scaled',
              'Converts native FP16 sources at load time. Saved scaled-FP8 artifacts are incompatible.',
            )}
            {toggle(
              'Offload inactive DiT',
              'offload_inactive_dit',
              'Not supported by this single-expert recipe; review will explain conflicts.',
            )}
            {job.config.block_swap_h2d_only &&
              toggle('H2D-only block swapping', 'block_swap_h2d_only')}
          </>
        ) : (
          <>
            {toggle(
              'H2D-only block swapping',
              'block_swap_h2d_only',
              'Required for the guidance-loss restart forward; classic swapping failed in the reference scenario.',
            )}
            {job.config.fp8_scaled &&
              toggle('Runtime FP8 scaling', 'fp8_scaled')}
            {job.config.offload_inactive_dit &&
              toggle('Offload inactive DiT', 'offload_inactive_dit')}
          </>
        )}
      </section>
      <section className="panel" id="job-section-checkpoints" tabIndex={-1}>
        <h2>Checkpoints & final sample</h2>
        <div className="number-row">
          {number('Save every N steps', 'save_every')}
        </div>
        {toggle(
          'Save optimizer/training state',
          'save_state',
          'A LoRA checkpoint alone is not resumable optimizer state.',
        )}
        {toggle('Generate a final sample', 'sample_enabled')}
        {job.config.sample_enabled && (
          <>
            <label className="field">
              Sample prompt
              <textarea
                id={fieldId('sample_prompt')}
                {...problemProps('sample_prompt')}
                disabled={busy}
                value={job.config.sample_prompt}
                onChange={(event) =>
                  config('sample_prompt', event.target.value)
                }
              />
              {problemText('sample_prompt')}
            </label>
            {number('Sample inference steps', 'sample_steps')}
          </>
        )}
        {!job.config.sample_enabled &&
          problems.sample_steps &&
          number('Sample inference steps', 'sample_steps')}
      </section>
    </>
  );
}
