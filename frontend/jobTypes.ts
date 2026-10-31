export type JobConfig = {
  recipe: string;
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
  rank: number;
  alpha: number;
  learning_rate: number;
  steps: number;
  gradient_accumulation: number;
  blocks_to_swap: number;
  gradient_checkpointing: boolean;
  fp8_scaled: boolean;
  offload_inactive_dit: boolean;
  block_swap_h2d_only: boolean;
  audio_policy: 'require' | 'allow-missing';
  save_every: number;
  save_state: boolean;
  sample_enabled: boolean;
  sample_prompt: string;
  sample_steps: number;
  seed: number;
};
export type Job = {
  version: number;
  id: string;
  revision: number;
  name: string;
  family: 'wan' | 'h3';
  dataset_id: string;
  config: JobConfig;
};
export type Dataset = { id: string; name: string };
export type Preset = { id: string; name: string; family: Job['family'] };
export type ConversionPreview = {
  draft: unknown;
  changes: { field: string; before: unknown; after: unknown }[];
  message: string;
};
export type Review = {
  valid: boolean;
  errors: string[];
  warnings: string[];
  effective_count: number;
  models: Record<string, string>;
  model_provenance: Record<string, Record<string, string>>;
  paths: Record<string, string>;
  dataset_toml: string;
  stages: { name: string; args: string[] }[];
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
