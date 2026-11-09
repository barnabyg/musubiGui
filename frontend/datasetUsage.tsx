import { useEffect, useState } from 'react';
import { apiClient } from './api';
import type { Job } from './jobTypes';
import { formatRoute } from './route';

const request = apiClient(
  'Affected jobs could not be loaded. Retry before changing inclusion or removing the dataset.',
);
export type DatasetUsage = { job: Job; archived: boolean }[];

export async function loadDatasetUsage(
  datasetId: string,
  signal?: AbortSignal,
): Promise<DatasetUsage> {
  const [active, archived] = await Promise.all([
    request<Job[]>('/api/jobs', 'GET', undefined, signal),
    request<Job[]>('/api/jobs/archived', 'GET', undefined, signal),
  ]);
  return [
    ...active.map((job) => ({ job, archived: false })),
    ...archived.map((job) => ({ job, archived: true })),
  ].filter(({ job }) =>
    job.datasets.some(({ dataset_id }) => dataset_id === datasetId),
  );
}

export function useDatasetUsage(datasetId: string, enabled: boolean) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{
    key: string;
    jobs?: DatasetUsage;
    error?: string;
  }>();
  const key = `${datasetId}/${attempt}`;
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    void loadDatasetUsage(datasetId, controller.signal)
      .then((jobs) => {
        if (!controller.signal.aborted) setResult({ key, jobs });
      })
      .catch((failure: Error) => {
        if (!controller.signal.aborted)
          setResult({ key, error: failure.message });
      });
    return () => controller.abort();
  }, [datasetId, enabled, key]);
  return {
    jobs: result?.key === key ? result.jobs : undefined,
    error: result?.key === key ? result.error : undefined,
    retry: () => setAttempt((value) => value + 1),
  };
}

export function DatasetJobImpact({
  jobs,
  error,
  retry,
  newTab = false,
}: {
  jobs?: DatasetUsage;
  error?: string;
  retry?: () => void;
  newTab?: boolean;
}) {
  return (
    <section
      aria-label="Jobs using this dataset"
      className="dataset-job-impact"
    >
      <h3>Jobs using this dataset</h3>
      {error ? (
        <p role="alert">
          {error}{' '}
          {retry && <button onClick={retry}>Retry affected jobs</button>}
        </p>
      ) : !jobs ? (
        <p role="status">Loading affected jobs…</p>
      ) : !jobs.length ? (
        <p>No saved jobs reference this dataset.</p>
      ) : (
        <ul>
          {jobs.map(({ job, archived }) => (
            <li key={job.id}>
              <a
                href={formatRoute({
                  screen: 'Jobs',
                  id: archived ? undefined : job.id,
                })}
                target={newTab ? '_blank' : undefined}
                rel={newTab ? 'noopener' : undefined}
              >
                {job.name} · {job.family.toUpperCase()} · r{job.revision}
              </a>{' '}
              (
              {archived ? 'Archived; restore in Archived jobs' : 'Active draft'}
              )
            </li>
          ))}
        </ul>
      )}
      <p>
        Exclusions and caption changes affect the next review and preparation of
        these drafts. Prepared Runs retain their snapshot and outputs.
      </p>
      {newTab && <p>Job links open in a new tab.</p>}
    </section>
  );
}
