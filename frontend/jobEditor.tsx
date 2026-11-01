import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { confirmDialog } from './ConfirmDialog';
import { apiClient } from './api';
import { jobFields } from './jobFields';
import type {
  ConversionPreview,
  DatasetOption,
  Job,
  JobConfig,
  PreparedRun,
  Preset,
  Review,
} from './jobTypes';
import type { JobSummary } from './JobTable';
import { plural } from './labels';

export type JobsProps = {
  selectedId?: string;
  onSelect?: (id?: string, replace?: boolean) => void;
  onRun?: (id: string) => void;
  onDirtyChange?: (dirty: boolean) => void;
};

const request = apiClient('Check the entered values and retry.');

// All of the Jobs screen's state, and everything it does to a job.
export function useJobEditor({
  selectedId,
  onSelect,
  onRun,
  onDirtyChange,
}: JobsProps) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [archived, setArchived] = useState<Job[]>([]);
  const [summaries, setSummaries] = useState<JobSummary[]>([]);
  const nameField = useRef<HTMLInputElement>(null);
  const focusName = useRef(false);
  const [datasets, setDatasets] = useState<DatasetOption[]>([]);
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
      request<DatasetOption[]>('/api/datasets'),
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
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
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
  const fields = jobFields(job, busy, review, config);
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
        datasets: draft.datasets,
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
  return {
    jobs,
    archived,
    summaries,
    nameField,
    datasets,
    models,
    modelRoots,
    job,
    dirty,
    busy,
    status,
    error,
    setError,
    review,
    presets,
    presetName,
    setPresetName,
    presetId,
    setPresetId,
    transfer,
    setTransfer,
    transferOpen,
    setTransferOpen,
    existingRun,
    fields,
    edit,
    config,
    choose,
    create,
    save,
    inspect,
    prepare,
    duplicate,
    archive,
    restore,
    deleteJob,
    savePreset,
    applyPreset,
    convert,
    exportConfiguration,
    importConfiguration,
    prepareReason,
  };
}
