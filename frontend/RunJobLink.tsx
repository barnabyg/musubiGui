import { useEffect, useState } from 'react';
import { apiClient } from './api';
import { formatRoute } from './route';

type JobIdentity = {
  id: string;
  name: string;
  family: string;
  revision: number;
};

const request = apiClient('The current job could not be loaded.');

export function RunJobLink({ frozen }: { frozen: JobIdentity }) {
  const [current, setCurrent] = useState<{
    job?: JobIdentity;
    archived: boolean;
  } | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      request<JobIdentity[]>('/api/jobs', 'GET', undefined, controller.signal),
      request<JobIdentity[]>(
        '/api/jobs/archived',
        'GET',
        undefined,
        controller.signal,
      ),
    ])
      .then(([active, archived]) => {
        if (controller.signal.aborted) return;
        const job = active.find(({ id }) => id === frozen.id);
        setCurrent(
          job
            ? { job, archived: false }
            : {
                job: archived.find(({ id }) => id === frozen.id),
                archived: true,
              },
        );
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [frozen.id, attempt]);
  const job = current?.job;
  return (
    <section aria-label="Originating job">
      <h3>Originating job</h3>
      <p>
        Frozen for this Run: {frozen.name} · {frozen.family.toUpperCase()} · r
        {frozen.revision}. Its saved inputs and outputs stay with this revision.
      </p>
      {error ? (
        <p>
          {error} This Run and its outputs remain available.{' '}
          <button
            onClick={() => {
              setError('');
              setCurrent(null);
              setAttempt((value) => value + 1);
            }}
          >
            Retry job lookup
          </button>
        </p>
      ) : !current ? (
        <p>Loading current job…</p>
      ) : !job ? (
        <p>
          This job is no longer available. This Run and its outputs remain
          available.
        </p>
      ) : current.archived ? (
        <p>
          Archived job: {job.name} · {job.family.toUpperCase()} · r
          {job.revision}.{' '}
          <a href={formatRoute({ screen: 'Jobs' })}>Restore in Archived jobs</a>
          .
        </p>
      ) : (
        <>
          <p>
            Current draft:{' '}
            <a
              href={formatRoute({ screen: 'Jobs', id: job.id })}
              aria-label={`Open current job ${job.name} · ${job.family.toUpperCase()} · r${job.revision}`}
            >
              {job.name} · {job.family.toUpperCase()} · r{job.revision}
            </a>
          </p>
          {job.revision !== frozen.revision && (
            <p>The editable job has changed since this Run was prepared.</p>
          )}
        </>
      )}
    </section>
  );
}
