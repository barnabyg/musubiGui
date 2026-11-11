import type { RefObject } from 'react';
import { JobDatasets } from './JobDatasets';
import { componentLabels } from './labels';
import type { JobFields } from './jobFields';
import { fieldId } from './jobRules';
import type {
  DatasetOption,
  Job,
  JobConfig,
  SetConfig,
  TrainingOptions,
} from './jobTypes';
import { PathField } from './PathField';
import type { JobMedia } from './jobMedia';

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
  datasets: DatasetOption[];
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
      <JobDatasets
        job={job}
        busy={busy}
        fields={fields}
        edit={edit}
        datasets={datasets}
      />
      <label className="field">
        Recipe
        <select
          id={fieldId('recipe')}
          {...problemProps('recipe')}
          disabled={busy}
          value={job.config.recipe}
          onChange={(event) =>
            edit({
              ...job,
              config: {
                ...job.config,
                recipe: event.target.value,
                save_state:
                  event.target.value === 'alternating'
                    ? true
                    : job.config.save_state,
              },
            })
          }
        >
          {job.family === 'wan' ? (
            <>
              {job.config.recipe === 'low-noise' && (
                <option value="low-noise" disabled>
                  Choose alternating high/low training
                </option>
              )}
              <option value="alternating">
                Wan alternating low/high experts · T2V A14B
              </option>
            </>
          ) : (
            <option value="guidance-loss">H3 guidance loss · T2VA</option>
          )}
        </select>
        {problemText('recipe')}
        <small>
          {job.family === 'wan' ? (
            job.config.recipe === 'alternating' ? (
              <>
                Both experts train in alternating phases and both trained LoRAs
                are used for the final sample. Each switch restores that
                expert’s optimizer, scheduler and RNG; Musubi starts a fresh
                dataset pass.
              </>
            ) : (
              <>
                Select alternating low/high experts to train both models.
                Optimizer state saving will be enabled.
              </>
            )
          ) : (
            <>
              Released FL2VA base used for the T2VA task, joint video/audio
              losses, unconditional text caching, guidance scale 4 and sigma
              minimum 0.15.
            </>
          )}
        </small>
      </label>
      {job.family === 'wan' &&
        job.config.recipe === 'alternating' &&
        fields.number(
          'Steps per expert before switching',
          'expert_interval',
          'Optimizer updates, after gradient accumulation. Starts with low-noise. Loading each expert again adds overhead. A fresh dataset pass can repeat examples before others are seen.',
        )}
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
  media,
  trainingOptions,
}: {
  job: Job;
  busy: boolean;
  fields: JobFields;
  config: SetConfig;
  media: JobMedia;
  trainingOptions: TrainingOptions;
}) {
  const { problemProps, problemText, number, toggle } = fields;
  return (
    <>
      <section className="panel" id="job-section-dataset" tabIndex={-1}>
        <h2>Dataset preparation</h2>
        <div className="number-row">
          {number('Width', 'width')}
          {number('Height', 'height')}
          {!media.imageOnly &&
            number(
              'Frames',
              'frames',
              'Frames per training video window and generated sample. Images each use one frame.',
            )}
          {number(
            'Repeats',
            'repeats',
            'The default for datasets that leave theirs blank.',
          )}
          {number('Dataset batch size', 'batch_size')}
        </div>
        <p className="hint">
          {job.family === 'wan'
            ? 'Wan width and height require multiples of 16.'
            : 'H3 width and height require multiples of 32.'}{' '}
          {(!media.imageOnly ||
            job.config.sample_enabled ||
            job.config.preview_every > 0) &&
            (job.family === 'wan'
              ? 'Videos and samples require 4*n+1 frames; 33 frames is measured on the target PC. '
              : 'Videos and samples require 17*n+5 frames between 124 and 345; 124 frames is measured on the target PC. ')}
          Keep the batch size at one for the target-PC recipe.
        </p>
        {!media.imageOnly && (
          <>
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
          </>
        )}
        {toggle('Enable bucketing', 'enable_bucket')}
        {toggle('Do not upscale buckets', 'bucket_no_upscale')}
        <h3>Caching</h3>
        <p className="hint">
          Latents and text embeddings are required. Compatible caches are copied
          into each Run’s private storage. Rebuilding leaves other Runs
          unchanged.
        </p>
        <div className="columns">
          {(
            [
              ['Latent caching', 'latent_cache_policy'],
              ['Text embedding caching', 'text_cache_policy'],
            ] as const
          ).map(([label, key]) => (
            <label className="field" key={key}>
              {label}
              <select
                value={job.config[key]}
                disabled={busy}
                onChange={(event) =>
                  config(key, event.target.value as 'reuse' | 'rebuild')
                }
              >
                <option value="reuse">Reuse compatible cache</option>
                <option value="rebuild">Rebuild cache</option>
              </select>
            </label>
          ))}
        </div>
        {job.family === 'h3' && !media.imageOnly && (
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
        <label className="field">
          Optimizer
          <select
            id={fieldId('optimizer')}
            aria-label="Optimizer"
            value={job.config.optimizer}
            disabled={busy}
            {...problemProps('optimizer')}
            onChange={(event) =>
              config('optimizer', event.target.value as JobConfig['optimizer'])
            }
          >
            {trainingOptions.optimizers.map((optimizer) => (
              <option key={optimizer} value={optimizer}>
                {optimizer}
              </option>
            ))}
            {!trainingOptions.optimizers.includes(job.config.optimizer) && (
              <option value={job.config.optimizer} disabled>
                {job.config.optimizer} (unavailable)
              </option>
            )}
          </select>
          {problemText('optimizer')}
          <small>{trainingOptions.reason}</small>
        </label>
        <label className="field">
          Timestep strategy
          <select
            id={fieldId('timestep_sampling')}
            aria-label="Timestep strategy"
            value={job.config.timestep_sampling}
            disabled={
              busy ||
              (job.family === 'h3' &&
                job.config.timestep_sampling === 'uniform')
            }
            {...problemProps('timestep_sampling')}
            onChange={(event) =>
              config(
                'timestep_sampling',
                event.target.value as JobConfig['timestep_sampling'],
              )
            }
          >
            {job.family === 'wan' && <option value="shift">Shift</option>}
            {job.family === 'wan' && <option value="sigmoid">Sigmoid</option>}
            <option value="uniform">Uniform</option>
            {job.family === 'h3' &&
              job.config.timestep_sampling !== 'uniform' && (
                <option value={job.config.timestep_sampling} disabled>
                  Unsupported: {job.config.timestep_sampling}
                </option>
              )}
          </select>
          {problemText('timestep_sampling')}
          <small>
            {job.family === 'h3'
              ? 'The pinned H3 trainer supports Uniform only, sharing one base draw between video and audio.'
              : job.config.timestep_sampling === 'uniform'
                ? 'Equal probability across the active expert’s timestep range.'
                : job.config.timestep_sampling === 'sigmoid'
                  ? 'Concentrates draws around the middle of the active expert’s range. Scale controls the spread.'
                  : 'Shifts a sigmoid distribution toward noisier timesteps. The default shift 3 and scale 1 preserve the original recipe.'}
          </small>
        </label>
        {job.family === 'wan' && job.config.timestep_sampling !== 'uniform' && (
          <div className="number-row">
            {number(
              'Sigmoid scale',
              'sigmoid_scale',
              '0.001–1000. Larger values spread draws toward the range edges.',
            )}
            {job.config.timestep_sampling === 'shift' &&
              number(
                'Flow shift',
                'discrete_flow_shift',
                '0.001–1000. Values above one favour noisier timesteps.',
              )}
          </div>
        )}
        {job.family === 'wan' && (
          <p className="hint">
            {job.config.timestep_sampling === 'shift' &&
            job.config.discrete_flow_shift === 3 &&
            job.config.sigmoid_scale === 1
              ? 'Original recipe: preserve the distribution within each expert’s range.'
              : 'The selected distribution is scaled into each expert’s range, keeping every draw inside it.'}
          </p>
        )}
        <div className="number-row">
          {number('LoRA rank', 'rank')}
          {number('LoRA alpha', 'alpha')}
          {number('Learning rate', 'learning_rate')}
          {number('Gradient accumulation', 'gradient_accumulation')}
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
              'Not supported by this recipe; experts load one at a time. Review will explain conflicts.',
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
        <h2>Checkpoints</h2>
        <div className="number-row">
          {number('Save every N steps', 'save_every')}
          {number(
            'Maximum checkpoint saves to keep',
            'keep_checkpoints',
            '0 keeps all saves. Applies per expert; final results and states needed for restoration are protected and may exceed the limit.',
          )}
        </div>
        {toggle(
          'Save optimizer/training state',
          'save_state',
          'A LoRA checkpoint alone is not resumable optimizer state.',
        )}
      </section>
    </>
  );
}
