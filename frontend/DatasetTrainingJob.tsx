import { useId, useState } from 'react';
import { apiClient } from './api';
import type { Dataset, DatasetItem } from './Datasets';
import type { Job, JobConfig } from './jobTypes';

const request = apiClient(
  'The job could not be created. Retry from this dataset.',
);

function unavailable(dataset: Dataset) {
  if (dataset.missing)
    return 'Restore the missing folder and re-scan in Dataset settings.';
  if (['pending', 'scanning'].includes(dataset.scan.state))
    return 'Wait for the scan to finish before creating a training job.';
  if (dataset.scan.state !== 'complete')
    return 'Re-scan in Dataset settings before creating a training job.';
  if (!dataset.counts.included)
    return 'Add valid media or include excluded media, then re-scan if needed.';
  return '';
}

export function DatasetTrainingJob({
  dataset,
  busy,
  beforeCreate,
  onCreated,
}: {
  dataset: Dataset;
  busy: boolean;
  beforeCreate: () => Promise<boolean>;
  onCreated: (id: string) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');
  const reasonId = useId();
  const reason = unavailable(dataset);
  async function create(family: Job['family']) {
    if (creating || busy || reason) return;
    const origin = window.location.hash;
    setCreating(true);
    setError('');
    try {
      if (!(await beforeCreate())) {
        setError('Resolve unsaved captions before creating a training job.');
        return;
      }
      if (window.location.hash !== origin) return;
      const current = await request<Dataset>(`/api/datasets/${dataset.id}`);
      const blocked = unavailable(current);
      if (blocked) throw new Error(blocked);
      const [jobs, archived, config] = await Promise.all([
        request<Job[]>('/api/jobs'),
        request<Job[]>('/api/jobs/archived'),
        request<JobConfig>(`/api/jobs/defaults/${family}`),
      ]);
      // Video aspect ratios must survive the handoff; fixed-size training centre-crops them again.
      if (family === 'h3') {
        const items = await request<DatasetItem[]>(
          `/api/datasets/${dataset.id}/items`,
        );
        if (
          items.some(
            (item) =>
              item.valid &&
              !item.excluded &&
              item.kind === 'video' &&
              Number(item.metadata.width) > 0 &&
              Number(item.metadata.height) > 0 &&
              Math.abs(
                Number(item.metadata.width) /
                  Number(item.metadata.height) /
                  (config.width / config.height) -
                  1,
              ) > 0.01,
          )
        ) {
          config.enable_bucket = true;
          config.bucket_no_upscale = true;
        }
      }
      if (window.location.hash !== origin) return;
      const taken = new Set([...jobs, ...archived].map((job) => job.name));
      const base = `${current.name.slice(0, 175)} · ${family === 'wan' ? 'Wan' : 'H3'}`;
      let name = base;
      for (let number = 2; taken.has(name); number += 1)
        name = `${base} ${number}`;
      const job = await request<Job>('/api/jobs', 'POST', {
        name,
        family,
        config,
        datasets: [{ dataset_id: dataset.id, repeats: null }],
      });
      if (window.location.hash === origin) onCreated(job.id);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setCreating(false);
    }
  }
  return (
    <details className="dataset-training-job">
      <summary>Create training job</summary>
      <div>
        <p>
          A separate draft starts with {dataset.name} selected. Captions save
          first.
        </p>
        <p>
          H3 drafts enable aspect-ratio bucketing when videos differ from the
          default training shape.
        </p>
        {reason && <p id={reasonId}>{reason}</p>}
        <button
          disabled={busy || creating || !!reason}
          aria-describedby={reason ? reasonId : undefined}
          onClick={() => void create('wan')}
        >
          Create Wan job
        </button>{' '}
        <button
          disabled={busy || creating || !!reason}
          aria-describedby={reason ? reasonId : undefined}
          onClick={() => void create('h3')}
        >
          Create H3 job
        </button>
        {creating && <p role="status">Saving captions and creating draft…</p>}
        {error && <p role="alert">{error}</p>}
      </div>
    </details>
  );
}
