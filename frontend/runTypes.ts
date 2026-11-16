import type { PreviewMemoryChoice } from './PreviewMemory';

export type Run = {
  id: string;
  created: string;
  finished?: string;
  execution_started?: string | null;
  execution_end_unknown?: boolean;
  status: string;
  error: string | null;
  retention?: {
    removed?: string[];
    pending?: string[];
    errors?: string[];
    protected?: string[];
  };
  fixture: boolean;
  job: {
    id: string;
    name: string;
    family: string;
    revision: number;
    config?: {
      recipe?: string;
      save_state?: boolean;
      save_every?: number;
      steps?: number;
      preview_every?: number;
    };
  };
  manifest: {
    items: { name?: string; path: string; caption: { text: string } }[];
  };
  stages: {
    name: string;
    status: string;
    exit_code?: number;
    reused_from?: string;
    cache_reason?: string;
    started?: string;
    finished?: string;
    preview_memory?: PreviewMemoryChoice;
  }[];
  directory: string;
  order: number;
  start_after_prepare?: boolean;
  resume_of?: string;
  // Save now, Sample now or Save and stop, waiting for the next training step.
  control_request?: { id: string; action: ControlAction; requested: string };
  controls?: {
    id: string;
    action: ControlAction;
    step?: number;
    handled?: string;
    dropped?: string;
    error?: string;
  }[];
  deletion?: {
    error: string | null;
    remaining: { path: string; bytes?: number }[];
  };
};
export type ControlAction = 'save' | 'sample' | 'save_and_stop';
export type DeletePreview = {
  refusal: string | null;
  bytes?: number;
  shared_cache?: string | null;
  dependents?: { id: string; status: string }[];
  // Deleting a whole training: its segments in creation order.
  chain?: string;
  segments?: {
    id: string;
    status: string;
    job: { name: string; revision: number };
    position: number;
    steps: { start: number; target: number; end: number | null };
  }[];
};
export type DeleteResult = {
  deleted: boolean;
  freed_bytes?: number;
  deleted_ids?: string[];
};
export type Progress = {
  step: number;
  total: number;
  elapsed: string | null;
  eta: string | null;
  eta_scope?: 'phase';
  estimated_remaining_seconds?: number | null;
  seconds_per_step?: number | null;
  loss: string | null;
  expert?: string;
  phase_step?: number;
  phase_total?: number;
  expert_steps?: { low: number; high: number };
  // Updates of the training stage since its latest save, which Stop now loses.
  unsaved_steps?: number | null;
};
export type CompletionEstimate = {
  remaining_seconds: number | null;
  finish_at: string | null;
  accounted_remaining_seconds?: number | null;
  accounted_finish_at?: string | null;
  outstanding_previews: number;
  completed_previews: number;
  outstanding_final_samples: number;
  preview_remaining_seconds: number | null;
  unknown_components: string[];
};
