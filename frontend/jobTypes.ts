import type { ItemView } from './route';
export type JobConfig = {
  recipe: string;
  expert_interval: number;
  components: Record<string, string>;
  width: number;
  height: number;
  frames: number;
  frame_extraction: 'head' | 'chunk' | 'slide' | 'uniform';
  frame_stride: number;
  frame_sample: number;
  batch_size: number;
  repeats: number;
  enable_bucket: boolean;
  bucket_no_upscale: boolean;
  latent_cache_policy: 'reuse' | 'rebuild';
  text_cache_policy: 'reuse' | 'rebuild';
  rank: number;
  alpha: number;
  learning_rate: number;
  optimizer: 'AdamW' | 'AdamW8bit';
  timestep_sampling: 'shift' | 'sigmoid' | 'uniform';
  sigmoid_scale: number;
  discrete_flow_shift: number;
  steps: number;
  gradient_accumulation: number;
  blocks_to_swap: number;
  gradient_checkpointing: boolean;
  fp8_scaled: boolean;
  offload_inactive_dit: boolean;
  block_swap_h2d_only: boolean;
  audio_policy: 'require' | 'allow-missing';
  save_every: number;
  keep_checkpoints: number;
  save_state: boolean;
  sample_enabled: boolean;
  preview_every: number;
  sample_prompt: string;
  sample_steps: number;
  sample_width?: number | null;
  sample_height?: number | null;
  sample_frames?: number | null;
  seed: number;
};
export type JobDataset = {
  dataset_id: string;
  repeats: number | null;
  resolution?: [number, number] | null;
};
export type Job = {
  version: number;
  id: string;
  revision: number;
  name: string;
  family: 'wan' | 'h3';
  datasets: JobDataset[];
  config: JobConfig;
};
export type SetConfig = <K extends keyof JobConfig>(
  key: K,
  value: JobConfig[K],
) => void;
export type TrainingOptions = {
  optimizers: JobConfig['optimizer'][];
  reason: string;
};
export type DatasetOption = {
  id: string;
  name: string;
  counts: { images: number; videos: number };
};
export type Preset = { id: string; name: string; family: Job['family'] };
export type ConversionPreview = {
  draft: unknown;
  changes: { field: string; before: unknown; after: unknown }[];
  message: string;
};
export type ReviewTarget =
  | { field: string }
  | {
      summary: string;
      items: { label: string; dataset_id: string; item_id: string }[];
    }
  | {
      dataset_id: string;
      item_id?: string;
      view?: ItemView;
    };

export type Review = {
  sample: {
    width: number;
    height: number;
    frames: number;
    fps: number;
    duration: number;
  };
  optimizer: JobConfig['optimizer'];
  timestep_sampling: JobConfig['timestep_sampling'];
  timestep_parameters: Record<string, number>;
  timestep_range_policy: 'original' | 'bounded' | 'shared-av';
  job_id: string;
  revision: number;
  error_targets: Record<number, ReviewTarget>;
  warning_targets: Record<number, ReviewTarget>;
  valid: boolean;
  errors: string[];
  warnings: string[];
  effective_count: number;
  datasets: { id: string; name: string }[];
  sections: {
    dataset_id: string;
    dataset: string;
    kind: 'image' | 'video';
    fps: number | null;
    resolution: [number, number];
    items: number;
    repeats: number;
    samples: number;
    share: number;
  }[];
  models: Record<string, string>;
  model_provenance: Record<string, Record<string, string>>;
  paths: Record<string, string>;
  dataset_toml: string;
  cache_policy: {
    latent_cache: 'reuse' | 'rebuild';
    text_cache: 'reuse' | 'rebuild';
  };
  stages: { name: string; args: string[] }[];
  expert_schedule?: {
    interval: number;
    counts: { low: number; high: number };
    phases: { expert: string; start: number; steps: number }[];
  } | null;
  audio_coverage: {
    included: number;
    supervised: number;
    missing: number;
    items: unknown[];
  };
  disk: {
    estimate: {
      media_snapshot: number;
      caches: number;
      checkpoints: number;
      states: number;
      samples: number;
      saves: number;
      retained_saves?: number;
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
  video_windows: {
    total: number;
    items: {
      id: string;
      name: string;
      source_fps: number | null;
      source_frames: number | null;
      resampled_frames: number | null;
      windows: number | null;
    }[];
  };
};
export type PreparedRun = { id: string; status: string; existing: boolean };
