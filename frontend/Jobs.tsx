import {
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { ActionMenu } from './ActionMenu';
import { confirmDialog } from './ConfirmDialog';
import { apiClient } from './api';
import { JobTable, type JobSummary } from './JobTable';
import { componentLabels } from './labels';
import { PathField } from './PathField';
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

const sections = [
  { id: 'job-section-job', label: 'Job & dataset' },
  { id: 'job-section-dataset', label: 'Dataset preparation' },
  { id: 'job-section-training', label: 'LoRA & training' },
  { id: 'job-section-memory', label: 'Memory' },
  { id: 'job-section-checkpoints', label: 'Checkpoints & sample' },
  { id: 'job-section-review', label: 'Review' },
];

type Props = {
  selectedId?: string;
  onSelect?: (id?: string, replace?: boolean) => void;
  onRun?: (id: string) => void;
  onDirtyChange?: (dirty: boolean) => void;
};

const request = apiClient('Check the entered values and retry.');

function plural(count: number, noun: string) {
  return `${count} ${noun}${count === 1 ? '' : 's'}`;
}

function diskVerdict(disk: Review['disk']) {
  const short = disk.volumes.filter((volume) => volume.shortfall > 0);
  if (short.length)
    return `Disk: ${short
      .map(
        (volume) => `${sizeLabel(volume.shortfall)} short on ${volume.volume}`,
      )
      .join(', ')}`;
  return disk.volumes.some((volume) => volume.free === null)
    ? 'Disk: free space unknown'
    : 'Disk: enough free space';
}

export function Jobs({ selectedId, onSelect, onRun, onDirtyChange }: Props) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [archived, setArchived] = useState<Job[]>([]);
  const [summaries, setSummaries] = useState<JobSummary[]>([]);
  const nameField = useRef<HTMLInputElement>(null);
  const focusName = useRef(false);
  const reviewPanel = useRef<HTMLElement>(null);
  const actionBar = useRef<HTMLDivElement>(null);
  const [section, setSection] = useState(sections[0].id);
  // A chosen section stays current until the page scrolls away from where it landed: a short last
  // section can't reach the top of the window.
  const chosen = useRef<{ id: string; at: number } | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [models, setModels] = useState<Record<string, string>>({});
  const [modelRoots, setModelRoots] = useState<string[]>([]);
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
  const [transferOpen, setTransferOpen] = useState(false);
  const [existingRun, setExistingRun] = useState<PreparedRun | null>(null);

  useEffect(() => {
    void Promise.all([
      request<Job[]>('/api/jobs'),
      request<Dataset[]>('/api/datasets'),
      request<{ models: Record<string, string>; model_roots: string[] }>(
        '/api/settings',
      ),
      request<Preset[]>('/api/jobs/presets'),
      request<Job[]>('/api/jobs/archived'),
      request<JobSummary[]>('/api/jobs/summaries'),
    ])
      .then(([items, library, settings, savedPresets, hidden, info]) => {
        setJobs(items);
        setSummaries(info);
        setArchived(hidden);
        setDatasets(library);
        setModels(settings.models);
        setModelRoots(settings.model_roots);
        setPresets(savedPresets);
        setStatus('Jobs loaded');
      })
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setBusy(false));
  }, []);
  // Follows the URL: a link, refresh or browser history can name a job that isn't open here.
  const followRoute = useEffectEvent((items: Job[]) => {
    if (selectedId === job?.id) return;
    const found = items.find((item) => item.id === selectedId);
    if (found) {
      select(found);
      return;
    }
    setJob(null);
    setDirty(false);
    setReview(null);
    if (selectedId) {
      onSelect?.(undefined, true);
      setStatus('That job is no longer in the job list.');
    }
  });
  useEffect(() => {
    let stale = false;
    void request<Job[]>('/api/jobs')
      .then((items) => {
        if (!stale) followRoute(items);
      })
      .catch((reason: Error) => {
        if (!stale) setError(reason.message);
      });
    return () => {
      stale = true;
    };
  }, [selectedId]);
  // A new draft's name is the first thing worth changing; the field is disabled until the request lands.
  useEffect(() => {
    if (focusName.current && !busy && nameField.current) {
      focusName.current = false;
      nameField.current.focus();
      nameField.current.select();
    }
  }, [busy, job?.id]);
  useEffect(() => {
    if (review) reviewPanel.current?.scrollIntoView({ block: 'start' });
  }, [review]);
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  // Keeps focused and scrolled-to content clear of the sticky bar, however far it has wrapped.
  useLayoutEffect(() => {
    const bar = actionBar.current;
    if (!bar) return;
    const root = document.documentElement;
    const observer = new ResizeObserver(() => {
      root.style.scrollPaddingTop = `${bar.offsetHeight + 12}px`;
    });
    observer.observe(bar);
    return () => {
      observer.disconnect();
      root.style.scrollPaddingTop = '';
    };
  }, []);
  const hasJob = !!job;
  useEffect(() => {
    if (!hasJob) return;
    const follow = () => {
      if (chosen.current) {
        if (Math.abs(window.scrollY - chosen.current.at) < 2) return;
        chosen.current = null;
      }
      const line =
        (actionBar.current?.getBoundingClientRect().bottom ?? 0) + 16;
      const root = document.documentElement;
      const atEnd =
        window.scrollY + window.innerHeight >= root.scrollHeight - 2 &&
        window.scrollY > 0;
      let current = sections[0].id;
      for (const { id } of sections)
        if (
          (document.getElementById(id)?.getBoundingClientRect().top ?? 0) <=
          line
        )
          current = id;
      setSection(atEnd ? sections[sections.length - 1].id : current);
    };
    follow();
    window.addEventListener('scroll', follow, { passive: true });
    return () => window.removeEventListener('scroll', follow);
  }, [hasJob]);
  function goToSection(id: string) {
    const target = document.getElementById(id);
    if (!target) return;
    target.scrollIntoView({ block: 'start' });
    target.focus({ preventScroll: true });
    chosen.current = { id, at: window.scrollY };
    setSection(id);
  }
  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (dirty) event.preventDefault();
    };
    window.addEventListener('beforeunload', protect);
    return () => window.removeEventListener('beforeunload', protect);
  }, [dirty]);

  async function reloadJobs() {
    const [items, info] = await Promise.all([
      request<Job[]>('/api/jobs'),
      request<JobSummary[]>('/api/jobs/summaries'),
    ]);
    setJobs(items);
    setSummaries(info);
    return items;
  }
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
  function confirmDiscard(next: string) {
    return confirmDialog({
      title: 'Discard unsaved job edits?',
      body: <p>Your unsaved changes to this job are lost if you {next}.</p>,
      confirmLabel: 'Discard edits',
      cancelLabel: 'Keep editing',
      danger: true,
    });
  }
  async function choose(value: Job) {
    if (dirty && !(await confirmDiscard('open another job'))) return;
    select(value);
  }
  function show(value: Job | null) {
    setJob(value);
    onSelect?.(value?.id);
  }
  function select(value: Job) {
    show(value);
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
      <label className="field number-field">
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
    if (dirty && !(await confirmDiscard('create a new draft'))) return;
    await perform(async () => {
      const taken = new Set(
        [...(await reloadJobs()), ...archived].map((item) => item.name),
      );
      const base = family === 'wan' ? 'New Wan job' : 'New H3 job';
      let name = base;
      for (let number = 2; taken.has(name); number += 1)
        name = `${base} ${number}`;
      const created = await request<Job>('/api/jobs', 'POST', {
        name,
        family,
        config: await request<JobConfig>(`/api/jobs/defaults/${family}`),
      });
      await reloadJobs();
      show(created);
      setDirty(false);
      setReview(null);
      setStatus('Draft created');
      focusName.current = true;
    });
  }
  async function persist(draft: Job) {
    let saved: Job;
    try {
      saved = await request<Job>(`/api/jobs/${draft.id}`, 'PUT', {
        name: draft.name,
        family: draft.family,
        dataset_id: draft.dataset_id,
        config: draft.config,
        revision: draft.revision,
      });
    } catch (reason) {
      setStatus('Save failed — edits retained');
      throw reason;
    }
    show(saved);
    setDirty(false);
    setReview(null);
    setStatus(`Draft saved — revision ${saved.revision}`);
    await reloadJobs();
    return saved;
  }
  async function save() {
    if (!job) return;
    await perform(async () => {
      await persist(job);
    });
  }
  async function inspect() {
    if (!job) return;
    await perform(async () => {
      // A failed save throws here, so the review never describes something other than the edits on screen.
      const current = dirty ? await persist(job) : job;
      const result = await request<Review>(
        `/api/jobs/${current.id}/review`,
        'POST',
        {},
      );
      setReview(result);
      setStatus(
        result.valid
          ? `Review passed for revision ${current.revision}`
          : `Review failed for revision ${current.revision}: ${plural(result.errors.length, 'error')}`,
      );
    });
  }
  async function prepare(start = false) {
    if (!job || !review?.valid || dirty) return;
    await perform(async () => {
      const run = await request<PreparedRun>('/api/runs/prepare', 'POST', {
        job_id: job.id,
        start,
      });
      // Prepare returns the matching unfinished or completed run instead of duplicating it.
      if (run.existing && !(start && run.status !== 'completed')) {
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
      await reloadJobs();
      select(copied);
      setStatus('Independent copy created');
    });
  }
  async function refreshLibrary() {
    await reloadJobs();
    setArchived(await request<Job[]>('/api/jobs/archived'));
  }
  async function archive() {
    if (!job || dirty) return;
    await perform(async () => {
      await request<Job>(`/api/jobs/${job.id}/archive`, 'POST', {});
      await refreshLibrary();
      show(null);
      setReview(null);
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
      const confirmed = await confirmDialog({
        title: `Permanently delete job ${target.name}?`,
        body: (
          <p>
            This removes the job and its revision history, and can't be undone.
            Datasets and presets are not affected.
          </p>
        ),
        confirmLabel: 'Delete job',
        danger: true,
      });
      if (!confirmed) return;
      await request(`/api/jobs/${target.id}/delete`, 'POST', {
        confirmed: true,
      });
      await refreshLibrary();
      if (job?.id === target.id) {
        show(null);
        setReview(null);
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
      await reloadJobs();
      select(updated);
      setStatus('Preset applied as a new revision');
    });
  }
  async function convert() {
    if (!job || dirty) return;
    const send = (confirmed: boolean) =>
      request<Job | ConversionPreview>(`/api/jobs/${job.id}/convert`, 'POST', {
        family: job.family === 'wan' ? 'h3' : 'wan',
        revision: job.revision,
        confirmed,
      });
    await perform(async () => {
      const preview = await send(false);
      if ('id' in preview) return;
      const accepted = await confirmDialog({
        title: 'Confirm model conversion',
        body: (
          <>
            <p>{preview.message}</p>
            <ul>
              {preview.changes.map((change) => (
                <li key={change.field}>
                  {change.field.replace(/_/g, ' ')}:{' '}
                  {JSON.stringify(change.before)} →{' '}
                  {JSON.stringify(change.after)}
                </li>
              ))}
            </ul>
          </>
        ),
        confirmLabel: 'Accept conversion',
        cancelLabel: 'Cancel conversion',
      });
      if (!accepted) return;
      const result = await send(true);
      if ('id' in result) {
        await reloadJobs();
        select(result);
        setStatus('Model conversion saved as a new revision');
      }
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
    if (dirty && !(await confirmDiscard('import a configuration'))) return;
    await perform(async () => {
      const imported = await request<Job>(
        '/api/jobs/import',
        'POST',
        JSON.parse(transfer),
      );
      await reloadJobs();
      show(imported);
      setDirty(false);
      setReview(null);
      setStatus('Configuration imported as an independent draft');
    });
  }
  const prepareReason = dirty
    ? 'Choose Review configuration to save these edits and check them before preparing.'
    : !review
      ? 'Choose Review configuration before preparing.'
      : !review.valid
        ? 'Resolve the review errors before preparing.'
        : '';
  const savePresetReason = dirty
    ? 'Save your edits to save them as a preset.'
    : !presetName.trim()
      ? 'Enter a preset name to save one.'
      : '';
  const applyPresetReason = dirty
    ? 'Save your edits to apply a preset.'
    : !presetId
      ? 'Select a preset to apply it.'
      : '';
  return (
    <div className="jobs-workspace">
      <section className="title">
        <h1>Jobs</h1>
        <p>
          Set up a LoRA training job, review it, then prepare a Run. A Run keeps
          the settings it was prepared with, so later edits don't change it.
        </p>
        <button disabled={busy} onClick={() => void create('wan')}>
          New Wan job
        </button>{' '}
        <button disabled={busy} onClick={() => void create('h3')}>
          New H3 job
        </button>
      </section>
      {jobs.length > 0 && (
        <JobTable
          jobs={jobs}
          summaries={summaries}
          datasets={datasets}
          selectedId={job?.id}
          disabled={busy}
          onSelect={(value) => void choose(value)}
        />
      )}
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
                <button
                  className="danger"
                  disabled={busy}
                  onClick={() => void deleteJob(item)}
                >
                  Delete {item.name}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="job-action-bar" ref={actionBar}>
        <div className="actions">
          <span role="status">{status}</span>
          {job && (
            <>
              <button
                className={dirty ? 'primary' : ''}
                disabled={busy}
                onClick={() => void save()}
              >
                Save draft
              </button>
              <button
                className={!dirty && !review?.valid ? 'primary' : ''}
                disabled={busy}
                onClick={() => void inspect()}
              >
                Review configuration
              </button>
              <button
                disabled={busy || !!prepareReason}
                title={prepareReason || undefined}
                aria-describedby={prepareReason ? 'prepare-reason' : undefined}
                onClick={() => void prepare()}
              >
                Prepare run
              </button>
              <button
                className={prepareReason ? '' : 'primary'}
                disabled={busy || !!prepareReason}
                title={prepareReason || undefined}
                aria-describedby={prepareReason ? 'prepare-reason' : undefined}
                onClick={() => void prepare(true)}
              >
                Prepare and start
              </button>
            </>
          )}
          <ActionMenu
            label="More job actions"
            disabled={busy}
            note={
              dirty
                ? 'Save your edits to use the job actions.'
                : !job
                  ? 'Open a job to use the job actions.'
                  : undefined
            }
            items={[
              {
                label: 'Duplicate job',
                disabled: !job || dirty,
                onSelect: () => void duplicate(),
              },
              {
                label: `Change model to ${job?.family === 'h3' ? 'Wan T2V' : 'H3 T2VA'}`,
                disabled: !job || dirty,
                onSelect: () => void convert(),
              },
              {
                label: 'Archive job',
                disabled: !job || dirty,
                onSelect: () => void archive(),
              },
              {
                label: 'Delete job',
                danger: true,
                disabled: !job || dirty,
                onSelect: () => job && void deleteJob(job),
              },
              {
                label: 'Import / export configuration',
                onSelect: () => setTransferOpen(true),
              },
            ]}
          />
        </div>
        {job && prepareReason && (
          <p id="prepare-reason" className="hint">
            {prepareReason}
          </p>
        )}
        {job && (
          <nav className="job-sections" aria-label="Job sections">
            {sections.map(({ id, label }) => (
              <button
                key={id}
                className="quiet"
                aria-current={section === id ? 'true' : undefined}
                onClick={() => goToSection(id)}
              >
                {label}
              </button>
            ))}
          </nav>
        )}
        {error && (
          <div role="alert" className="error">
            {error}
          </div>
        )}
      </div>
      {transferOpen && (
        <>
          <section
            className="panel"
            aria-label="Import or export configuration"
          >
            <h2>Import / export configuration</h2>
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
            {!transfer && (
              <p className="hint">Choose a file or paste JSON to import it.</p>
            )}
            {(dirty || !job) && (
              <p className="hint">
                {dirty
                  ? 'Save your edits to export them.'
                  : 'Open a job to export it.'}
              </p>
            )}
            <div className="actions">
              <button onClick={() => setTransferOpen(false)}>Close</button>
            </div>
          </section>
        </>
      )}
      {job && (
        <>
          <section className="panel" id="job-section-job" tabIndex={-1}>
            <h2>Job & dataset</h2>
            <label className="field">
              Job name
              <input
                ref={nameField}
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
                    are used for sampling.
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
                <PathField
                  key={key}
                  label={componentLabels[key]}
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
          <section className="panel">
            <h2>Presets</h2>
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
              qualified on the target PC. Smoke tests also set steps,
              checkpoints and the sample, to check that training runs. Saved
              presets replace all settings.
            </p>
            <div className="actions">
              <button
                disabled={busy || !!savePresetReason}
                title={savePresetReason || undefined}
                aria-describedby={
                  savePresetReason ? 'save-preset-reason' : undefined
                }
                onClick={() => void savePreset()}
              >
                Save as preset
              </button>
              <button
                disabled={busy || !!applyPresetReason}
                title={applyPresetReason || undefined}
                aria-describedby={
                  applyPresetReason ? 'apply-preset-reason' : undefined
                }
                onClick={() => void applyPreset()}
              >
                Apply preset
              </button>
            </div>
            {savePresetReason && (
              <p id="save-preset-reason" className="hint">
                {savePresetReason}
              </p>
            )}
            {applyPresetReason && (
              <p id="apply-preset-reason" className="hint">
                {applyPresetReason}
              </p>
            )}
          </section>
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
              </>
            ) : (
              toggle(
                'H2D-only block swapping',
                'block_swap_h2d_only',
                'Required for the guidance-loss restart forward; classic swapping failed in the reference scenario.',
              )
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
          {review && existingRun && (
            <p>
              This revision already has a {existingRun.status} run with the same
              inputs, so no new run was created.{' '}
              <button onClick={() => onRun?.(existingRun.id)}>
                Open existing run
              </button>
            </p>
          )}
          {!review && (
            <section className="panel" id="job-section-review" tabIndex={-1}>
              <h2>Review</h2>
              <p>
                Choose Review configuration to check these settings before
                preparing a Run.
              </p>
            </section>
          )}
          {review && (
            <section
              ref={reviewPanel}
              id="job-section-review"
              tabIndex={-1}
              className="panel job-review"
              aria-label="Configuration review"
            >
              <h2>Configuration review</h2>
              <div
                className={`review-verdict ${review.valid ? 'passed' : 'failed'}`}
              >
                <strong>
                  {review.valid
                    ? 'Review passed. Ready for preparation.'
                    : `Review failed: resolve ${plural(review.errors.length, 'error')} before preparation.`}
                </strong>
                <span>
                  {plural(review.effective_count, 'included item')} ·{' '}
                  {diskVerdict(review.disk)}
                </span>
              </div>
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
              <details data-testid="disk-estimate">
                <summary>Estimated disk use</summary>
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
              </details>
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
              <details>
                <summary>Advanced</summary>
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
                    <pre>
                      {JSON.stringify(review.model_provenance, null, 2)}
                    </pre>
                  </details>
                )}
                <h3>Native dataset TOML</h3>
                <pre data-testid="dataset-toml">{review.dataset_toml}</pre>
                <p>
                  These paths are a preview. Preparing creates the Run's own
                  folder and records the exact arguments it runs with.
                </p>
                {review.stages.map((stage) => (
                  <details key={stage.name}>
                    <summary>{stage.name} arguments</summary>
                    <pre>{JSON.stringify(stage.args, null, 2)}</pre>
                  </details>
                ))}
              </details>
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
