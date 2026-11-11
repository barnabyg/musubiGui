// The simple rules, checked while typing. The backend stays the authority: Review repeats them
// along with everything that needs the disk, and these only have to agree with it.
type Family = 'wan' | 'h3';
export type RuleConfig = Record<string, unknown> & {
  width: number;
  height: number;
  frames: number;
  blocks_to_swap: number;
  learning_rate: number;
  batch_size: number;
  frame_extraction: string;
  frame_stride: number;
  frame_sample: number;
  sample_enabled: boolean;
  sample_width?: number | null;
  sample_height?: number | null;
  sample_frames?: number | null;
};

const geometry = { wan: 16, h3: 32 };
const maxBlocks = { wan: 38, h3: 48 };
const positive = [
  'repeats',
  'rank',
  'alpha',
  'steps',
  'gradient_accumulation',
  'save_every',
  'sample_steps',
];
const whole = [
  ...positive,
  'width',
  'height',
  'frames',
  'blocks_to_swap',
  'batch_size',
  'frame_stride',
  'frame_sample',
  'seed',
];

export function fieldProblems(
  family: Family,
  config: RuleConfig,
  hasVideos = true,
) {
  const problems: Record<string, string> = {};
  if (family === 'h3' && config.timestep_sampling !== 'uniform')
    problems.timestep_sampling = 'The pinned H3 trainer supports Uniform only.';
  if (
    family === 'wan' &&
    ['shift', 'sigmoid'].includes(String(config.timestep_sampling))
  ) {
    const scale = config.sigmoid_scale as number;
    if (!Number.isFinite(scale) || scale < 0.001 || scale > 1000)
      problems.sigmoid_scale = 'Enter a finite number from 0.001 to 1000.';
    const shift = config.discrete_flow_shift as number;
    if (
      config.timestep_sampling === 'shift' &&
      (!Number.isFinite(shift) || shift < 0.001 || shift > 1000)
    )
      problems.discrete_flow_shift =
        'Enter a finite number from 0.001 to 1000.';
  }
  const sampling =
    config.sample_enabled || ((config.preview_every ?? 0) as number) > 0;
  const needsFrames = hasVideos;
  if (sampling) {
    for (const key of ['sample_width', 'sample_height'] as const) {
      const value = config[key];
      if (
        value != null &&
        (!Number.isInteger(value) || value < 64 || value % geometry[family])
      )
        problems[key] =
          `Enter at least 64, in whole multiples of ${geometry[family]}.`;
    }
    const frames =
      config.sample_frames ?? (hasVideos ? undefined : config.frames);
    if (
      frames != null &&
      (!Number.isInteger(frames) ||
        (family === 'wan'
          ? frames < 1 || (frames - 1) % 4
          : frames < 124 || frames > 345 || (frames - 5) % 17))
    )
      problems.sample_frames =
        family === 'wan'
          ? 'Choose a duration giving 4*n+1 frames at 16 fps, or an image sample.'
          : 'Choose 124–345 frames in steps of 17 at 24 fps (about 5.167–14.375 seconds).';
  }
  const step = geometry[family];
  for (const key of ['width', 'height'] as const)
    if (config[key] < 64 || config[key] % step)
      problems[key] = `Enter at least 64, in multiples of ${step}.`;
  if (needsFrames && family === 'wan') {
    if (config.frames < 1 || (config.frames - 1) % 4)
      problems.frames = 'Wan needs 4*n+1 frames, such as 33.';
  } else if (
    needsFrames &&
    family === 'h3' &&
    (config.frames < 124 || config.frames > 345 || (config.frames - 5) % 17)
  )
    problems.frames = 'H3 needs 17*n+5 frames between 124 and 345.';
  if (config.blocks_to_swap < 0 || config.blocks_to_swap > maxBlocks[family])
    problems.blocks_to_swap = `Enter 0 to ${maxBlocks[family]}.`;
  if (!(config.learning_rate > 0 && config.learning_rate <= 1))
    problems.learning_rate = 'Enter more than 0, up to 1.';
  if (config.batch_size !== 1)
    problems.batch_size = 'This recipe supports a batch size of one.';
  if (family === 'wan' && config.recipe === 'alternating') {
    const interval = config.expert_interval as number;
    if (!Number.isInteger(interval) || interval < 1)
      problems.expert_interval = 'Enter a positive whole number.';
    else if (Math.ceil((config.steps as number) / interval) > 10_000)
      problems.expert_interval =
        'Increase the interval to stay within 10000 phases.';
    else if ((config.steps as number) <= interval)
      problems.steps =
        'Train both experts: steps must exceed the expert interval.';
    if (!config.save_state)
      problems.save_state =
        'Alternating training requires optimizer/training state.';
  }
  for (const key of positive)
    if ((config[key] as number) < 1) problems[key] = 'Enter 1 or more.';
  if (
    hasVideos &&
    config.frame_extraction === 'slide' &&
    config.frame_stride < 1
  )
    problems.frame_stride = 'Enter 1 or more.';
  if (
    hasVideos &&
    config.frame_extraction === 'uniform' &&
    config.frame_sample < 2
  )
    problems.frame_sample = 'Enter 2 or more.';
  for (const key of whole)
    if (
      (key !== 'frames' || needsFrames) &&
      (hasVideos || !['frame_stride', 'frame_sample'].includes(key)) &&
      !Number.isInteger(config[key])
    )
      problems[key] = 'Enter a whole number.';
  if (
    !Number.isInteger(config.preview_every ?? 0) ||
    ((config.preview_every ?? 0) as number) < 0
  )
    problems.preview_every = 'Enter zero (off) or a positive whole number.';
  if (
    !Number.isInteger(config.keep_checkpoints ?? 0) ||
    (config.keep_checkpoints as number) < 0
  )
    problems.keep_checkpoints =
      'Enter zero (unlimited) or a positive whole number.';
  return problems;
}

// Which field a Review error is about, from the label the backend starts each message with.
const errorFields: [RegExp, string][] = [
  [/^Sample width:/, 'sample_width'],
  [/^Sample height:/, 'sample_height'],
  [/^Sample duration:/, 'sample_frames'],
  [/^Name:/, 'name'],
  [/^Dataset:/, 'dataset_id'],
  [/^Recipe:/, 'recipe'],
  [/^Expert interval:/, 'expert_interval'],
  [/^Training state:/, 'save_state'],
  [/^Geometry:/, 'width'],
  [/^Frames:/, 'frames'],
  [/^Block swapping:/, 'blocks_to_swap'],
  [/^Frame extraction: enter a slide stride/, 'frame_stride'],
  [/^Frame extraction: uniform/, 'frame_sample'],
  [/^Frame extraction:/, 'frame_extraction'],
  [/^Batch size:/, 'batch_size'],
  [/^Repeats:/, 'repeats'],
  [/^Rank:/, 'rank'],
  [/^Alpha:/, 'alpha'],
  [/^Steps:/, 'steps'],
  [/^Gradient accumulation:/, 'gradient_accumulation'],
  [/^Checkpoint interval:/, 'save_every'],
  [/^Checkpoint retention:/, 'keep_checkpoints'],
  [/^Sample steps:/, 'sample_steps'],
  [/^Preview interval:/, 'preview_every'],
  [/^Learning rate:/, 'learning_rate'],
  [/^Timestep strategy:/, 'timestep_sampling'],
  [/^Sigmoid scale:/, 'sigmoid_scale'],
  [/^Flow shift:/, 'discrete_flow_shift'],
  [/^Memory:.*FP8/, 'fp8_scaled'],
  [/^Memory:.*H2D/, 'block_swap_h2d_only'],
  [/^Memory:.*gradient checkpointing/, 'gradient_checkpointing'],
  [/^Memory:.*offload/, 'offload_inactive_dit'],
  [/^Sampling:/, 'sample_prompt'],
];

const component =
  /\b(wan_low|wan_high|wan_vae|wan_t5|h3_dit|h3_video_vae|h3_audio_vae|h3_text)\b/;

export function componentError(message: string) {
  return component.exec(message)?.[1];
}

export function errorField(message: string) {
  return (
    componentError(message) ??
    errorFields.find(([pattern]) => pattern.test(message))?.[1]
  );
}

export const fieldId = (key: string) => `job-field-${key}`;

export const datasetField = (id: string) => `dataset-${id}`;
