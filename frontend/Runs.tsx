import { useEffect, useState } from 'react';
import { Outputs } from './Outputs';

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
  order: number;
  deletion?: {
    error: string | null;
    remaining: { path: string; bytes?: number }[];
  };
};
type DeletePreview = {
  refusal: string | null;
  bytes?: number;
  shared_cache?: string | null;
  dependents?: { id: string; status: string }[];
};
type DeleteResult = { deleted: boolean; freed_bytes?: number };

function sizeLabel(bytes: number) {
  if (bytes >= 100_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
  return bytes >= 1_000_000
    ? `${(bytes / 1_000_000).toFixed(0)} MB`
    : `${Math.ceil(bytes / 1_000)} KB`;
}

function deleteQuestion(run: Run, preview: DeletePreview) {
  const lines = [
    `Permanently delete run ${run.id.slice(0, 8)} of ${run.job.name} (r${run.job.revision})?`,
    `This frees ${sizeLabel(preview.bytes ?? 0)} and can't be undone. Its checkpoints, samples, saved states, logs and frozen inputs are removed, so download any LoRA you want to keep first.`,
  ];
  if (preview.shared_cache)
    lines.push(
      'Its cache stays until the other run using it is deleted, and is not counted above.',
    );
  if (preview.dependents?.length)
    lines.push(
      `${preview.dependents.length} later run(s) came from this one. They stay browsable, but can no longer be retried or restored from.`,
    );
  return lines.join('\n\n');
}

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

function SavedStates({
  runId,
  onRestore,
}: {
  runId: string;
  onRestore: (stateId: string, steps: number) => Promise<void>;
}) {
  const [states, setStates] = useState<
    {
      id: string;
      name: string;
      valid: boolean;
      error: string;
      optimizer_step?: number;
    }[]
  >([]);
  const [steps, setSteps] = useState(1);
  const [error, setError] = useState('');
  const [inspecting, setInspecting] = useState(false);
  async function inspect() {
    // Inspection hashes every state file, which takes seconds for large folders.
    setInspecting(true);
    try {
      setStates(await request(`/api/runs/${runId}/states`));
      setError('');
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setInspecting(false);
    }
  }
  return (
    <section className="panel">
      <h2>Saved optimizer state</h2>
      <p>
        Restoration starts a new segment with saved optimizer, scheduler and RNG
        state. Upstream resets its displayed step counter and data position. A
        LoRA checkpoint alone cannot restore training state. Stopping does not
        guarantee a checkpoint or state save.
      </p>
      <button disabled={inspecting} onClick={() => void inspect()}>
        Inspect saved states
      </button>
      {inspecting && <p role="status">Checking saved states…</p>}
      {error && <p role="alert">{error}</p>}
      <label className="field">
        Additional segment steps
        <input
          type="number"
          min={1}
          value={steps}
          onChange={(event) => setSteps(Number(event.target.value))}
        />
      </label>
      {states.map((state) => (
        <p key={state.id}>
          {state.name} ·{' '}
          {state.valid ? `optimizer step ${state.optimizer_step}` : state.error}
          <button
            disabled={!state.valid || !Number.isInteger(steps) || steps < 1}
            onClick={() => {
              if (
                window.confirm(
                  'Restore this optimizer state as a new segment? The displayed step counter and data position restart. Original run inputs are preserved.',
                )
              )
                void onRestore(state.id, steps);
            }}
          >
            Restore as new segment
          </button>
        </p>
      ))}
    </section>
  );
}

export function Runs({ selectedId }: { selectedId?: string }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState(selectedId ?? '');
  const [logs, setLogs] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [deleting, setDeleting] = useState('');
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
        if (selected && !records.some((record) => record.id === selected)) {
          // Deleted, perhaps from another tab: there is nothing left to show.
          setSelected('');
          setLogs('');
        } else if (selected) {
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
  const showFiles = run && run.status !== 'deleting' && run.id !== deleting;
  async function action(name: string, body: object = {}) {
    if (!run) return;
    setBusy(true);
    setError('');
    try {
      const result = await request<Run>(`/api/runs/${run.id}/${name}`, body);
      setSelected(result.id);
      setRuns(await request<Run[]>('/api/runs'));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function deleteRun() {
    if (!run) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const preview = await request<DeletePreview>(
        `/api/runs/${run.id}/delete-preview`,
      );
      if (preview.refusal) throw new Error(preview.refusal);
      if (!window.confirm(deleteQuestion(run, preview))) return;
      setNotice('Deleting run…');
      // Unmounting the outputs closes any preview stream holding one of the files open.
      setDeleting(run.id);
      const result = await request<DeleteResult>(`/api/runs/${run.id}/delete`, {
        confirmed: true,
      });
      setNotice(
        result.deleted
          ? `Run deleted. Freed ${sizeLabel(result.freed_bytes ?? 0)}.`
          : '',
      );
      if (result.deleted) {
        setSelected('');
        setLogs('');
      }
      setRuns(await request<Run[]>('/api/runs'));
    } catch (reason) {
      setNotice('');
      setError((reason as Error).message);
    } finally {
      setDeleting('');
      setBusy(false);
    }
  }
  async function moveEarlier(id: string) {
    const pending = runs
      .filter((record) =>
        ['pending_preparation', 'pending_training'].includes(record.status),
      )
      .sort((a, b) => a.order - b.order);
    const index = pending.findIndex((record) => record.id === id);
    if (index < 1) return;
    [pending[index - 1], pending[index]] = [pending[index], pending[index - 1]];
    try {
      await request('/api/runs/queue/order', {
        run_ids: pending.map((record) => record.id),
      });
      setRuns(await request<Run[]>('/api/runs'));
    } catch (reason) {
      setError((reason as Error).message);
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
      {notice && <p role="status">{notice}</p>}
      <div className="columns">
        <section className="panel">
          <h2>Pending queue</h2>
          {runs
            .filter((record) =>
              ['pending_preparation', 'pending_training'].includes(
                record.status,
              ),
            )
            .sort((a, b) => a.order - b.order)
            .map((record, index) => (
              <p key={record.id}>
                {index + 1}. {record.job.name} · {record.status}{' '}
                <button
                  disabled={index === 0}
                  onClick={() => void moveEarlier(record.id)}
                >
                  Move earlier
                </button>
              </p>
            ))}
        </section>
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
            {run.status === 'deleting' && (
              <div aria-label="Unfinished delete">
                <p className="error">
                  {run.deletion?.error ??
                    'This run is being deleted, or its delete was interrupted. If this stays, choose Delete again.'}
                </p>
                {!!run.deletion?.remaining.length && (
                  <>
                    <p>Still on disk:</p>
                    <ul>
                      {run.deletion.remaining.map((item) => (
                        <li key={item.path}>
                          {item.path}
                          {item.bytes !== undefined &&
                            ` · ${sizeLabel(item.bytes)}`}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            )}
            <div className="actions">
              {['orphaned', 'uncertain', 'interrupted'].includes(
                run.status,
              ) && (
                <>
                  <button
                    disabled={busy}
                    onClick={() => void action('reconcile')}
                  >
                    Reconcile processes
                  </button>
                  {run.status !== 'interrupted' && (
                    <button
                      disabled={busy}
                      onClick={() => {
                        if (
                          window.confirm(
                            'Terminate the recovered processes belonging to this run? Unsaved work may be lost.',
                          )
                        )
                          void action('terminate-orphan', { confirmed: true });
                      }}
                    >
                      Terminate recovered work
                    </button>
                  )}
                </>
              )}
              {['training', 'generating', 'stopping'].includes(run.status) && (
                <>
                  <button
                    disabled={busy || run.status === 'stopping'}
                    onClick={() => void action('stop', { force: false })}
                  >
                    Request graceful stop
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => {
                      if (
                        window.confirm(
                          'Force terminate this run process tree? Unsaved checkpoints and optimizer state may be lost.',
                        )
                      )
                        void action('stop', { force: true, confirmed: true });
                    }}
                  >
                    Force terminate
                  </button>
                </>
              )}
              {run.status === 'prepared' && (
                <button
                  disabled={busy}
                  className="primary"
                  onClick={() => void action('start')}
                >
                  Start training
                </button>
              )}
              {run.status === 'prepared' && (
                <button disabled={busy} onClick={() => void action('queue')}>
                  Add to queue
                </button>
              )}
              {[
                'pending_preparation',
                'pending_training',
                'preparing',
              ].includes(run.status) && (
                <button disabled={busy} onClick={() => void action('cancel')}>
                  Cancel preparation
                </button>
              )}
              {['failed', 'cancelled', 'interrupted'].includes(run.status) && (
                <button disabled={busy} onClick={() => void action('retry')}>
                  Retry frozen preparation
                </button>
              )}
              <button disabled={busy} onClick={() => void deleteRun()}>
                {run.status === 'deleting' ? 'Delete again' : 'Delete run'}
              </button>
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
      {showFiles && (
        <SavedStates
          key={`states-${run.id}`}
          runId={run.id}
          onRestore={(stateId, steps) =>
            action('restore-state', {
              state_id: stateId,
              additional_steps: steps,
              confirmed: true,
            })
          }
        />
      )}
      {showFiles && <Outputs key={`outputs-${run.id}`} runId={run.id} />}
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
