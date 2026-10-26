import { useEffect, useState } from 'react';
import './jobs.css';

type JobConfig = {
  recipe: string;
  components: Record<string, string>;
  width: number;
  height: number;
  frames: number;
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
  family: 'wan';
  dataset_id: string;
  config: JobConfig;
};
type Dataset = { id: string; name: string };
type Review = {
  valid: boolean;
  errors: string[];
  warnings: string[];
  effective_count: number;
  models: Record<string, string>;
  paths: Record<string, string>;
  dataset_toml: string;
  stages: { name: string; args: string[] }[];
};
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
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [models, setModels] = useState<Record<string, string>>({});
  const [job, setJob] = useState<Job | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('Loading jobs…');
  const [error, setError] = useState('');
  const [review, setReview] = useState<Review | null>(null);

  useEffect(() => {
    void Promise.all([
      request<Job[]>('/api/jobs'),
      request<Dataset[]>('/api/datasets'),
      request<{ models: Record<string, string> }>('/api/settings'),
    ])
      .then(([items, library, settings]) => {
        setJobs(items);
        setDatasets(library);
        setModels(settings.models);
        setStatus('Jobs loaded');
      })
      .catch((reason: Error) => setError(reason.message));
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
    setStatus(`Opened revision ${value.revision}`);
  }
  function edit(next: Job) {
    setJob(next);
    setDirty(true);
    setReview(null);
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
  async function create() {
    if (
      dirty &&
      !window.confirm('Discard unsaved job edits and create a new draft?')
    )
      return;
    await perform(async () => {
      const created = await request<Job>('/api/jobs', 'POST', {
        name: 'New Wan job',
        family: 'wan',
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
      const run = await request<{ id: string }>('/api/runs/prepare', 'POST', {
        job_id: job.id,
      });
      setStatus('Preparation submitted');
      onRun?.(run.id);
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
        <button disabled={busy} onClick={() => void create()}>
          New Wan job
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
      {job && (
        <>
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
                <option value="low-noise">
                  Wan low-noise expert · T2V A14B
                </option>
              </select>
              <small>
                Native FP16 experts; one expert is trained and both experts are
                used for sampling. Target-PC support is awaiting reference
                proof.
              </small>
            </label>
            <details>
              <summary>Model components</summary>
              <p>
                Paths inherit Settings unless overridden here. Overrides must
                remain inside configured model roots.
              </p>
              {['wan_low', 'wan_high', 'wan_vae', 'wan_t5'].map((key) => (
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
              {number('Width', 'width', 'Wan requires multiples of 16.')}
              {number('Height', 'height')}
              {number(
                'Frames',
                'frames',
                '4*n+1 frames; 17 is the smoke default.',
              )}
              {number('Repeats', 'repeats')}
              {number(
                'Dataset batch size',
                'batch_size',
                'Keep at one for the target-PC recipe.',
              )}
            </div>
            {toggle('Enable bucketing', 'enable_bucket')}
            {toggle('Do not upscale buckets', 'bucket_no_upscale')}
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
              '36 of 40 blocks is the reference default.',
            )}
            {toggle('Gradient checkpointing', 'gradient_checkpointing')}
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
              <h3>Resolved paths</h3>
              <pre>
                {JSON.stringify(
                  { ...review.paths, models: review.models },
                  null,
                  2,
                )}
              </pre>
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
