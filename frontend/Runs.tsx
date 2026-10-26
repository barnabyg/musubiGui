import { useEffect, useState } from 'react';

type Run = {
  id: string;
  created: string;
  status: string;
  error: string | null;
  fixture: boolean;
  job: { id: string; name: string; family: string; revision: number };
  manifest: {
    items: { name?: string; path: string; caption: { text: string } }[];
  };
  stages: {
    name: string;
    status: string;
    exit_code?: number;
    reused_from?: string;
  }[];
  directory: string;
};

async function request<T>(url: string, body?: object): Promise<T> {
  const response = await fetch(
    url,
    body
      ? {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  const value: unknown = await response.json();
  if (!response.ok)
    throw new Error(
      typeof value === 'object' && value && 'detail' in value
        ? String(value.detail)
        : 'Run request failed.',
    );
  return value as T;
}

export function Runs({ selectedId }: { selectedId?: string }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState(selectedId ?? '');
  const [logs, setLogs] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [owner, setOwner] = useState<{ run_id: string } | null>(null);
  const [fixture, setFixture] = useState(false);
  useEffect(() => {
    let current = true;
    async function refresh() {
      try {
        const [records, capabilities] = await Promise.all([
          request<Run[]>('/api/runs'),
          request<{ fixture: boolean; gpu_owner: { run_id: string } | null }>(
            '/api/runs/capabilities',
          ),
        ]);
        if (!current) return;
        setRuns(records);
        setOwner(capabilities.gpu_owner);
        setFixture(capabilities.fixture);
        if (selected) {
          const result = await request<{ text: string }>(
            `/api/runs/${selected}/logs`,
          );
          if (current) setLogs(result.text);
        }
      } catch (reason) {
        if (current) setError((reason as Error).message);
      }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [selected]);
  const run = runs.find((record) => record.id === selected);
  async function action(name: string) {
    if (!run) return;
    setBusy(true);
    setError('');
    try {
      const result = await request<Run>(`/api/runs/${run.id}/${name}`, {});
      setSelected(result.id);
      setRuns(await request<Run[]>('/api/runs'));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <section className="title">
        <p className="eyebrow">PREPARATION & EXECUTION</p>
        <h1>Runs</h1>
        <p>
          Each run keeps its reviewed job revision and frozen dataset inputs.
          Closing the browser leaves the worker running.
        </p>
      </section>
      {fixture && (
        <p className="error">
          Synthetic fixture mode — these runs do not establish GPU or model
          support.
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <p role="status">
        {owner ? `GPU owned by run ${owner.run_id}` : 'GPU available'}
      </p>
      <div className="columns">
        <section className="panel">
          <h2>Persistent run history</h2>
          {runs.length === 0 && (
            <p>Save and review a job, then choose Prepare.</p>
          )}
          {runs.map((record) => (
            <p key={record.id}>
              <button
                aria-pressed={selected === record.id}
                onClick={() => setSelected(record.id)}
              >
                {record.job.name} · {record.job.family.toUpperCase()} · r
                {record.job.revision}
              </button>
              <br />
              <small>
                {record.status} · {record.created}
              </small>
            </p>
          ))}
        </section>
        {run && (
          <section className="panel">
            <h2>{run.job.name}</h2>
            <p>Run {run.id}</p>
            <p>
              <strong>State: {run.status}</strong>
            </p>
            {run.error && <p className="error">{run.error}</p>}
            <div className="actions">
              {run.status === 'prepared' && (
                <button
                  disabled={busy}
                  className="primary"
                  onClick={() => void action('start')}
                >
                  Start training
                </button>
              )}
              {['pending_preparation', 'preparing'].includes(run.status) && (
                <button disabled={busy} onClick={() => void action('cancel')}>
                  Cancel preparation
                </button>
              )}
              {['failed', 'cancelled'].includes(run.status) && (
                <button disabled={busy} onClick={() => void action('retry')}>
                  Retry frozen preparation
                </button>
              )}
            </div>
            {run.stages.map((stage) => (
              <p key={stage.name}>
                <strong>{stage.name}</strong>: {stage.status}
                {stage.exit_code !== undefined && ` (exit ${stage.exit_code})`}
                {stage.reused_from && ' · verified cache reused'}
              </p>
            ))}
            <details>
              <summary>
                Frozen inputs ({run.manifest.items.length} included items)
              </summary>
              <pre>{JSON.stringify(run.manifest, null, 2)}</pre>
            </details>
            <p>Output: {run.directory}</p>
          </section>
        )}
      </div>
      {run && (
        <section className="panel">
          <h2>Live stage logs</h2>
          <p>
            Progress appears as reported by upstream. Missing step information
            means progress is unknown.
          </p>
          <pre aria-label="Run logs">{logs || 'Waiting for stage output…'}</pre>
        </section>
      )}
    </>
  );
}
