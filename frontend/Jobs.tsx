import { useEffect, useState } from 'react';
import './jobs.css';

type JobConfig = {
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
type Job = {
  version: number;
  id: string;
  revision: number;
  name: string;
  family: 'wan' | 'h3';
  dataset_id: string;
  config: JobConfig;
};
type Dataset = { id: string; name: string };
type Preset = { id: string; name: string; family: Job['family'] };
type ConversionPreview = {
  draft: unknown;
  changes: { field: string; before: unknown; after: unknown }[];
  message: string;
};
type Review = {
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
function rateLabel(fps: number | null) {
  return fps === null ? 'unknown fps' : `${Number(fps.toPrecision(6))} fps`;
}
function sizeLabel(bytes: number) {
  return bytes >= 100_000_000
    ? `${(bytes / 1_000_000_000).toFixed(1)} GB`
    : `${(bytes / 1_000_000).toFixed(0)} MB`;
}
type PreparedRun = { id: string; status: string; existing: boolean };

type Props = {
  onRun?: (id: string) => void;
  onDirtyChange?: (dirty: boolean) => void;
};

async function request<T>(
  url: string,
  method = 'GET',
  value?: unknown,
): Promise<T> {
  const response = await fetch(url, {
    method,
    headers:
      value === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: value === undefined ? undefined : JSON.stringify(value),
  });
  const body: unknown = await response.json();
  if (!response.ok) {
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(
      typeof detail === 'string'
        ? detail
        : 'Check the entered values and retry.',
    );
  }
  return body as T;
}

export function Jobs({ onRun, onDirtyChange }: Props) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [archived, setArchived] = useState<Job[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [models, setModels] = useState<Record<string, string>>({});
  const [job, setJob] = useState<Job | null>(null);
  const [dirty, setDirty] = useState(false);
  // Busy until the initial load lands, so it cannot replace drafts created meanwhile.
  const [busy, setBusy] = useState(true);
  const [status, setStatus] = useState('Loading jobs…');
  const [error, setError] = useState('');
  const [review, setReview] = useState<Review | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetName, setPresetName] = useState('');
  const [presetId, setPresetId] = useState('');
  const [transfer, setTransfer] = useState('');
  const [conversion, setConversion] = useState<ConversionPreview | null>(null);
  const [existingRun, setExistingRun] = useState<PreparedRun | null>(null);

  useEffect(() => {
    void Promise.all([
      request<Job[]>('/api/jobs'),
      request<Dataset[]>('/api/datasets'),
      request<{ models: Record<string, string> }>('/api/settings'),
      request<Preset[]>('/api/jobs/presets'),
      request<Job[]>('/api/jobs/archived'),
    ])
      .then(([items, library, settings, savedPresets, hidden]) => {
        setJobs(items);
        setArchived(hidden);
        setDatasets(library);
        setModels(settings.models);
        setPresets(savedPresets);
        setStatus('Jobs loaded');
      })
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setBusy(false));
  }, []);
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (dirty) event.preventDefault();
    };
    window.addEventListener('beforeunload', protect);
    return () => window.removeEventListener('beforeunload', protect);
  }, [dirty]);

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    setExistingRun(null);
    try {
      await action();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function select(value: Job) {
    if (
      dirty &&
      !window.confirm('Discard unsaved job edits and open another draft?')
    )
      return;
    setJob(value);
    setDirty(false);
    setReview(null);
    setConversion(null);
    setStatus(`Opened revision ${value.revision}`);
  }
  function edit(next: Job) {
    setJob(next);
    setDirty(true);
    setReview(null);
    setConversion(null);
    setStatus('Unsaved draft');
  }
  function config<K extends keyof JobConfig>(key: K, value: JobConfig[K]) {
    if (job) edit({ ...job, config: { ...job.config, [key]: value } });
  }
  function number(label: string, key: keyof JobConfig, help?: string) {
    return (
      <label className="field">
        {label}
        <input
          type="number"
          step="any"
          value={String(job?.config[key] ?? '')}
          disabled={busy}
          onChange={(event) => config(key, Number(event.target.value))}
        />
        {help && <small>{help}</small>}
      </label>
    );
  }
  function toggle(label: string, key: keyof JobConfig, help?: string) {
    return (
      <label className="job-toggle">
        <input
          type="checkbox"
          checked={Boolean(job?.config[key])}
          disabled={busy}
          onChange={(event) => config(key, event.target.checked)}
        />
        <span>
          {label}
          {help && <small>{help}</small>}
        </span>
      </label>
    );
  }
  async function create(family: 'wan' | 'h3') {
    if (
      dirty &&
      !window.confirm('Discard unsaved job edits and create a new draft?')
    )
      return;
    await perform(async () => {
      const created = await request<Job>('/api/jobs', 'POST', {
        name: family === 'wan' ? 'New Wan job' : 'New H3 job',
        family,
      });
      setJobs(await request<Job[]>('/api/jobs'));
      setJob(created);
      setDirty(false);
      setReview(null);
      setStatus('Draft created');
    });
  }
  async function save() {
    if (!job) return;
    await perform(async () => {
      const saved = await request<Job>(`/api/jobs/${job.id}`, 'PUT', {
        name: job.name,
        family: job.family,
        dataset_id: job.dataset_id,
        config: job.config,
        revision: job.revision,
      });
      setJob(saved);
      setJobs(await request<Job[]>('/api/jobs'));
      setDirty(false);
      setReview(null);
      setStatus(`Draft saved — revision ${saved.revision}`);
    });
  }
  async function inspect() {
    if (!job) return;
    await perform(async () => {
      setReview(
        await request<Review>(`/api/jobs/${job.id}/review`, 'POST', {}),
      );
      setStatus(`Reviewed revision ${job.revision}`);
    });
  }
  async function prepare() {
    if (!job || !review?.valid || dirty) return;
    await perform(async () => {
      const run = await request<PreparedRun>('/api/runs/prepare', 'POST', {
        job_id: job.id,
      });
      // Prepare returns the matching unfinished or completed run instead of duplicating it.
      if (run.existing) {
        setExistingRun(run);
        setStatus('Existing run returned');
        return;
      }
      setStatus('Preparation submitted');
      onRun?.(run.id);
    });
  }
  async function duplicate() {
    if (!job || dirty) return;
    await perform(async () => {
      const copied = await request<Job>(
        `/api/jobs/${job.id}/duplicate`,
        'POST',
        {},
      );
      setJobs(await request<Job[]>('/api/jobs'));
      select(copied);
      setStatus('Independent copy created');
    });
  }
  async function refreshLibrary() {
    setJobs(await request<Job[]>('/api/jobs'));
    setArchived(await request<Job[]>('/api/jobs/archived'));
  }
  async function archive() {
    if (!job || dirty) return;
    await perform(async () => {
      await request<Job>(`/api/jobs/${job.id}/archive`, 'POST', {});
      await refreshLibrary();
      setJob(null);
      setReview(null);
      setConversion(null);
      setStatus('Job archived');
    });
  }
  async function restore(id: string) {
    await perform(async () => {
      await request<Job>(`/api/jobs/${id}/restore`, 'POST', {});
      await refreshLibrary();
      setStatus('Job restored');
    });
  }
  async function deleteJob(target: Job) {
    await perform(async () => {
      const preview = await request<{ refusal: string | null }>(
        `/api/jobs/${target.id}/delete-preview`,
      );
      if (preview.refusal) throw new Error(preview.refusal);
      if (
        !window.confirm(
          `Permanently delete job ${target.name} and its revision history? This can't be undone. Datasets and presets are not affected.`,
        )
      )
        return;
      await request(`/api/jobs/${target.id}/delete`, 'POST', {
        confirmed: true,
      });
      await refreshLibrary();
      if (job?.id === target.id) {
        setJob(null);
        setReview(null);
        setConversion(null);
      }
      setStatus('Job deleted');
    });
  }
  async function savePreset() {
    if (!job || dirty) return;
    await perform(async () => {
      await request(`/api/jobs/${job.id}/presets`, 'POST', {
        name: presetName,
      });
      setPresets(await request<Preset[]>('/api/jobs/presets'));
      setPresetName('');
      setStatus('Preset saved');
    });
  }
  async function applyPreset() {
    if (!job || dirty) return;
    await perform(async () => {
      const updated = await request<Job>(
        `/api/jobs/${job.id}/apply-preset`,
        'POST',
        { preset_id: presetId, revision: job.revision },
      );
      setJobs(await request<Job[]>('/api/jobs'));
      select(updated);
      setStatus('Preset applied as a new revision');
    });
  }
  async function convert(confirmed: boolean) {
    if (!job || dirty) return;
    await perform(async () => {
      const result = await request<Job | ConversionPreview>(
        `/api/jobs/${job.id}/convert`,
        'POST',
        {
          family: job.family === 'wan' ? 'h3' : 'wan',
          revision: job.revision,
          confirmed,
        },
      );
      if ('id' in result) {
        setJobs(await request<Job[]>('/api/jobs'));
        select(result);
        setStatus('Model conversion saved as a new revision');
      } else setConversion(result);
    });
  }
  async function exportConfiguration() {
    if (!job || dirty) return;
    await perform(async () => {
      const exported = await request<unknown>(`/api/jobs/${job.id}/export`);
      const text = JSON.stringify(exported, null, 2);
      setTransfer(text);
      const objectUrl = URL.createObjectURL(
        new Blob([text], { type: 'application/json' }),
      );
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = `musubi-job-${job.id}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
      setStatus(
        'Versioned configuration exported; recognised credentials redacted',
      );
    });
  }
  async function importConfiguration() {
    if (
      dirty &&
      !window.confirm('Discard unsaved edits and import an independent draft?')
    )
      return;
    await perform(async () => {
      const imported = await request<Job>(
        '/api/jobs/import',
        'POST',
        JSON.parse(transfer),
      );
      setJobs(await request<Job[]>('/api/jobs'));
      setJob(imported);
      setDirty(false);
      setReview(null);
      setConversion(null);
      setStatus('Configuration imported as an independent draft');
    });
  }
  return (
    <div className="jobs-workspace">
      <section className="title">
        <p className="eyebrow">IMMUTABLE RUNS, EDITABLE DRAFTS</p>
        <h1>Jobs</h1>
        <p>
          Create a LoRA draft, review its resolved inputs, then prepare a frozen
          run.
        </p>
        <button disabled={busy} onClick={() => void create('wan')}>
          New Wan job
        </button>{' '}
        <button disabled={busy} onClick={() => void create('h3')}>
          New H3 job
        </button>
      </section>
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      <div className="job-library" aria-label="Saved jobs">
        {jobs.map((item) => (
          <button
            key={item.id}
            aria-pressed={job?.id === item.id}
            disabled={busy}
            onClick={() => select(item)}
          >
            {item.name} · {item.family.toUpperCase()} · r{item.revision}
          </button>
        ))}
      </div>
      {archived.length > 0 && (
        <details className="panel">
          <summary>Archived jobs ({archived.length})</summary>
          <p>
            Archived jobs are hidden from the list above. Their Runs, outputs
            and saved states stay in Runs.
          </p>
          <ul aria-label="Archived jobs">
            {archived.map((item) => (
              <li key={item.id}>
                {item.name} · {item.family.toUpperCase()} · r{item.revision}{' '}
                <button disabled={busy} onClick={() => void restore(item.id)}>
                  Restore {item.name}
                </button>{' '}
                <button disabled={busy} onClick={() => void deleteJob(item)}>
                  Delete {item.name}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
      <details className="panel">
        <summary>Import / export configuration</summary>
        <p>
          Version 1 JSON preserves supported values. Unsupported formats and
          unknown fields are rejected. Recognised credentials are redacted;
          review local paths before sharing.
        </p>
        <label className="field">
          Configuration file
          <input
            type="file"
            accept="application/json,.json"
            disabled={busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file)
                void file
                  .text()
                  .then(setTransfer)
                  .catch((reason: Error) => setError(reason.message));
            }}
          />
        </label>
        <label className="field">
          Configuration JSON
          <textarea
            value={transfer}
            disabled={busy}
            onChange={(event) => setTransfer(event.target.value)}
          />
        </label>
        <div className="actions">
          <button
            disabled={busy || !transfer}
            onClick={() => void importConfiguration()}
          >
            Import configuration
          </button>
          <button
            disabled={busy || dirty || !job}
            onClick={() => void exportConfiguration()}
          >
            Export configuration
          </button>
        </div>
      </details>
      {job && (
        <>
          <section className="panel">
            <h2>Reuse configuration</h2>
            <div className="actions">
              <button disabled={busy || dirty} onClick={() => void duplicate()}>
                Duplicate job
              </button>
              <button
                disabled={busy || dirty}
                onClick={() => void convert(false)}
              >
                Change model to {job.family === 'wan' ? 'H3 T2VA' : 'Wan T2V'}
              </button>
              <button disabled={busy || dirty} onClick={() => void archive()}>
                Archive job
              </button>
              <button
                disabled={busy || dirty}
                onClick={() => void deleteJob(job)}
              >
                Delete job
              </button>
            </div>
            {conversion && (
              <div role="dialog" aria-label="Confirm model conversion">
                <p>{conversion.message}</p>
                <ul>
                  {conversion.changes.map((change) => (
                    <li key={change.field}>
                      {change.field}: {JSON.stringify(change.before)} →{' '}
                      {JSON.stringify(change.after)}
                    </li>
                  ))}
                </ul>
                <div className="actions">
                  <button disabled={busy} onClick={() => setConversion(null)}>
                    Cancel conversion
                  </button>
                  <button disabled={busy} onClick={() => void convert(true)}>
                    Accept conversion
                  </button>
                </div>
              </div>
            )}
            <div className="columns">
              <label className="field">
                Preset name
                <input
                  disabled={busy}
                  value={presetName}
                  onChange={(event) => setPresetName(event.target.value)}
                />
              </label>
              <label className="field">
                Compatible preset
                <select
                  disabled={busy}
                  value={presetId}
                  onChange={(event) => setPresetId(event.target.value)}
                >
                  <option value="">Select a preset</option>
                  {presets
                    .filter((preset) => preset.family === job.family)
                    .map((preset) => (
                      <option key={preset.id} value={preset.id}>
                        {preset.name}
                      </option>
                    ))}
                </select>
              </label>
            </div>
            <p className="hint">
              Measured presets set only size, frames, rank and memory controls
              qualified on the target PC; saved presets replace all settings.
            </p>
            <div className="actions">
              <button
                disabled={busy || dirty || !presetName.trim()}
                onClick={() => void savePreset()}
              >
                Save as preset
              </button>
              <button
                disabled={busy || dirty || !presetId}
                onClick={() => void applyPreset()}
              >
                Apply preset
              </button>
            </div>
          </section>
          <section className="panel">
            <h2>Job & dataset</h2>
            <label className="field">
              Job name
              <input
                disabled={busy}
                value={job.name}
                onChange={(event) => edit({ ...job, name: event.target.value })}
              />
            </label>
            <label className="field">
              Dataset
              <select
                disabled={busy}
                value={job.dataset_id}
                onChange={(event) =>
                  edit({ ...job, dataset_id: event.target.value })
                }
              >
                <option value="">Select a registered dataset</option>
                {datasets.map((dataset) => (
                  <option key={dataset.id} value={dataset.id}>
                    {dataset.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Recipe
              <select
                disabled={busy}
                value={job.config.recipe}
                onChange={(event) => config('recipe', event.target.value)}
              >
                {job.family === 'wan' ? (
                  <option value="low-noise">
                    Wan low-noise expert · T2V A14B
                  </option>
                ) : (
                  <option value="guidance-loss">H3 guidance loss · T2VA</option>
                )}
              </select>
              <small>
                {job.family === 'wan' ? (
                  <>
                    Native FP16 experts; one expert is trained and both experts
                    are used for sampling. The documented converted-weight smoke
                    recipe has target-PC execution evidence.
                  </>
                ) : (
                  <>
                    Released FL2VA base used for the T2VA task, joint
                    video/audio losses, unconditional text caching, guidance
                    scale 4 and sigma minimum 0.15.
                  </>
                )}
              </small>
            </label>
            <details>
              <summary>Model components</summary>
              <p>
                Paths inherit Settings unless overridden here. Overrides must
                remain inside configured model roots.
              </p>
              {(job.family === 'wan'
                ? ['wan_low', 'wan_high', 'wan_vae', 'wan_t5']
                : ['h3_dit', 'h3_video_vae', 'h3_audio_vae', 'h3_text']
              ).map((key) => (
                <label className="field" key={key}>
                  {key}
                  <input
                    disabled={busy}
                    value={job.config.components[key] ?? models[key] ?? ''}
                    onChange={(event) =>
                      config('components', {
                        ...job.config.components,
                        [key]: event.target.value,
                      })
                    }
                  />
                </label>
              ))}
            </details>
          </section>
          <section className="panel">
            <h2>Dataset preparation</h2>
            <div className="columns">
              {number(
                'Width',
                'width',
                job.family === 'wan'
                  ? 'Wan requires multiples of 16.'
                  : 'H3 requires multiples of 32.',
              )}
              {number('Height', 'height')}
              {number(
                'Frames',
                'frames',
                job.family === 'wan'
                  ? '4*n+1 frames; 17 is the smoke default.'
                  : '17*n+5 frames between 124 and 345; smoke default 124.',
              )}
              {number('Repeats', 'repeats')}
              {number(
                'Dataset batch size',
                'batch_size',
                'Keep at one for the target-PC recipe.',
              )}
            </div>
            <div className="columns">
              <label className="field">
                Frame extraction
                <select
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
                  resolve explicit → same-stem sidecar → embedded track.
                  Original media is never repaired.
                </small>
              </label>
            )}
          </section>
          <section className="panel">
            <h2>LoRA & training</h2>
            <div className="columns">
              {number('LoRA rank', 'rank')}
              {number('LoRA alpha', 'alpha')}
              {number('Learning rate', 'learning_rate')}
              {number('Training steps', 'steps')}
              {number('Gradient accumulation', 'gradient_accumulation')}
              {number('Seed', 'seed')}
            </div>
          </section>
          <section className="panel">
            <h2>Memory</h2>
            {number(
              'Blocks to swap',
              'blocks_to_swap',
              job.family === 'wan'
                ? '0–38 of 40 blocks; 36 is the reference default. 39 crashes in the upstream FP8 path.'
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
              </>
            ) : (
              toggle(
                'H2D-only block swapping',
                'block_swap_h2d_only',
                'Required for the guidance-loss restart forward; classic swapping failed in the reference scenario.',
              )
            )}
          </section>
          <section className="panel">
            <h2>Checkpoints & final sample</h2>
            {number('Save every N steps', 'save_every')}
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
                    disabled={busy}
                    value={job.config.sample_prompt}
                    onChange={(event) =>
                      config('sample_prompt', event.target.value)
                    }
                  />
                </label>
                {number('Sample inference steps', 'sample_steps')}
              </>
            )}
          </section>
          <div className="actions">
            <span role="status">{status}</span>
            <button
              className="primary"
              disabled={busy}
              onClick={() => void save()}
            >
              Save draft
            </button>
            <button disabled={busy || dirty} onClick={() => void inspect()}>
              Review configuration
            </button>
            <button
              disabled={busy || dirty || !review?.valid}
              onClick={() => void prepare()}
            >
              Prepare run
            </button>
          </div>
          {dirty && <p>Save the draft before reviewing or preparing it.</p>}
          {review && existingRun && (
            <p>
              This revision already has a {existingRun.status} run with the same
              inputs, so no new run was created.{' '}
              <button onClick={() => onRun?.(existingRun.id)}>
                Open existing run
              </button>
            </p>
          )}
          {review && (
            <section className="panel" aria-label="Configuration review">
              <h2>Configuration review</h2>
              <p>
                {review.valid
                  ? 'Ready for preparation'
                  : 'Resolve the following errors before preparation'}{' '}
                · {review.effective_count} included items
              </p>
              {review.errors.length > 0 && (
                <ul className="error">
                  {review.errors.map((message, index) => (
                    <li key={index}>{message}</li>
                  ))}
                </ul>
              )}
              <ul>
                {review.warnings.map((message, index) => (
                  <li key={index}>{message}</li>
                ))}
              </ul>
              <div data-testid="disk-estimate">
                <h3>Estimated disk use</h3>
                <ul>
                  <li>
                    Media snapshot:{' '}
                    {sizeLabel(review.disk.estimate.media_snapshot)}
                  </li>
                  <li>Caches: {sizeLabel(review.disk.estimate.caches)}</li>
                  <li>
                    LoRA checkpoints:{' '}
                    {sizeLabel(review.disk.estimate.checkpoints)} (
                    {review.disk.estimate.saves} saves)
                  </li>
                  <li>
                    Optimizer states: {sizeLabel(review.disk.estimate.states)}
                  </li>
                  <li>Samples: {sizeLabel(review.disk.estimate.samples)}</li>
                </ul>
                <ul>
                  {review.disk.volumes.map((volume) => (
                    <li key={volume.volume}>
                      {volume.volume} ({volume.roots.join(', ')}): needs about{' '}
                      {sizeLabel(volume.needed + volume.margin)} including a{' '}
                      {sizeLabel(volume.margin)} margin,{' '}
                      {volume.free === null
                        ? 'free space unknown'
                        : `${sizeLabel(volume.free)} free`}
                      {volume.shortfall > 0 &&
                        `, ${sizeLabel(volume.shortfall)} short`}
                    </li>
                  ))}
                </ul>
              </div>
              {review.video_windows.items.length > 0 && (
                <div data-testid="video-windows">
                  <h3>Cached video windows</h3>
                  <p>
                    {review.video_windows.total} cached video windows from{' '}
                    {review.video_windows.items.length} videos (
                    {job.config.frame_extraction}).
                  </p>
                  <ul>
                    {review.video_windows.items.map((item) => (
                      <li key={item.id}>
                        {item.name}: {rateLabel(item.source_fps)},{' '}
                        {item.source_frames ?? '?'}
                        {item.resampled_frames !== item.source_frames &&
                          ` → ${item.resampled_frames ?? '?'}`}{' '}
                        frames · {item.windows ?? '?'} windows
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {job.family === 'h3' && (
                <>
                  <h3>Audio coverage</h3>
                  <p>
                    {review.audio_coverage.supervised} supervised /{' '}
                    {review.audio_coverage.included} included;{' '}
                    {review.audio_coverage.missing} without audio.
                  </p>
                  <details>
                    <summary>Per-item audio diagnostics</summary>
                    <pre>
                      {JSON.stringify(review.audio_coverage.items, null, 2)}
                    </pre>
                  </details>
                </>
              )}
              <h3>Resolved paths</h3>
              <pre>
                {JSON.stringify(
                  { ...review.paths, models: review.models },
                  null,
                  2,
                )}
              </pre>
              {Object.keys(review.model_provenance).length > 0 && (
                <details>
                  <summary>Model precision and conversion provenance</summary>
                  <pre>{JSON.stringify(review.model_provenance, null, 2)}</pre>
                </details>
              )}
              <h3>Native dataset TOML</h3>
              <pre data-testid="dataset-toml">{review.dataset_toml}</pre>
              <p>
                Paths below preview a frozen revision. Preparation allocates its
                own immutable run directory and records the exact executed
                arguments.
              </p>
              {review.stages.map((stage) => (
                <details key={stage.name}>
                  <summary>{stage.name} arguments</summary>
                  <pre>{JSON.stringify(stage.args, null, 2)}</pre>
                </details>
              ))}
            </section>
          )}
        </>
      )}
      {!job && (
        <p>
          Select a saved job or create a draft to begin. Incomplete drafts can
          be saved.
        </p>
      )}
    </div>
  );
}
