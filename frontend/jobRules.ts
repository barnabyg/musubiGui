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

export function fieldProblems(family: Family, config: RuleConfig) {
  const problems: Record<string, string> = {};
  const step = geometry[family];
  for (const key of ['width', 'height'] as const)
    if (config[key] < 64 || config[key] % step)
      problems[key] = `Enter at least 64, in multiples of ${step}.`;
  if (family === 'wan') {
    if (config.frames < 1 || (config.frames - 1) % 4)
      problems.frames = 'Wan needs 4*n+1 frames, such as 33.';
  } else if (
    config.frames < 124 ||
    config.frames > 345 ||
    (config.frames - 5) % 17
  )
    problems.frames = 'H3 needs 17*n+5 frames between 124 and 345.';
  if (config.blocks_to_swap < 0 || config.blocks_to_swap > maxBlocks[family])
    problems.blocks_to_swap = `Enter 0 to ${maxBlocks[family]}.`;
  if (!(config.learning_rate > 0 && config.learning_rate <= 1))
    problems.learning_rate = 'Enter more than 0, up to 1.';
  if (config.batch_size !== 1)
    problems.batch_size = 'This recipe supports a batch size of one.';
  for (const key of positive)
    if ((config[key] as number) < 1) problems[key] = 'Enter 1 or more.';
  if (config.frame_extraction === 'slide' && config.frame_stride < 1)
    problems.frame_stride = 'Enter 1 or more.';
  if (config.frame_extraction === 'uniform' && config.frame_sample < 2)
    problems.frame_sample = 'Enter 2 or more.';
  for (const key of whole)
    if (!Number.isInteger(config[key])) problems[key] = 'Enter a whole number.';
  return problems;
}

// Which field a Review error is about, from the label the backend starts each message with.
const errorFields: [RegExp, string][] = [
  [/^Name:/, 'name'],
  [/^Dataset:/, 'dataset_id'],
  [/^Recipe:/, 'recipe'],
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
  [/^Sample steps:/, 'sample_steps'],
  [/^Learning rate:/, 'learning_rate'],
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
