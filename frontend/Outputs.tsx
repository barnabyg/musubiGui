import { useEffect, useState } from 'react';
import { apiClient } from './api';
import './outputs.css';

type Output = {
  id: string;
  run_id: string;
  path: string;
  name: string;
  kind?: 'checkpoint' | 'image' | 'video' | 'audio';
  status: string;
  bytes?: number;
  file_url?: string;
  error?: string | null;
  metadata?: Record<string, unknown>;
};
type OutputList = {
  run_id: string;
  job: { name: string; family: string; revision: number };
  status: string;
  outputs: Output[];
  warnings: string[];
  note: string;
};
type RunChoice = {
  id: string;
  job: { name: string; family: string; revision: number };
  created: string;
  status: string;
};
type Comparison = {
  before: string;
  after: string;
  changes: { path: string; before: unknown; after: unknown }[];
  note: string;
};

const read = apiClient('Output records could not be read. Refresh and retry.');

export function Outputs({
  runId,
  runState,
}: {
  runId: string;
  // Changes whenever the Run or one of its stages does, so finished work appears without a click.
  runState: string;
}) {
  const [result, setResult] = useState<OutputList | null>(null);
  const [runs, setRuns] = useState<RunChoice[]>([]);
  const [selected, setSelected] = useState('');
  const [other, setOther] = useState('');
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let current = true;
    void Promise.all([
      read<OutputList>(`/api/runs/${runId}/outputs`),
      read<RunChoice[]>('/api/runs'),
    ])
      .then(([outputs, records]) => {
        if (!current) return;
        setResult(outputs);
        setRuns(records);
        setError('');
      })
      .catch((reason: Error) => {
        if (current) setError(reason.message);
      })
      .finally(() => {
        if (current) setBusy(false);
      });
    return () => {
      current = false;
    };
  }, [runId, runState, refresh]);
  const currentResult = result?.run_id === runId ? result : null;
  const output = currentResult?.outputs.find((item) => item.id === selected);
  async function compare() {
    if (!other) return;
    setBusy(true);
    setError('');
    try {
      setComparison(
        await read<Comparison>(`/api/runs/${runId}/compare/${other}`),
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel" aria-label="Run outputs">
      <h2>Run outputs</h2>
      <p>Checkpoints and samples from Run {runId}.</p>
      <button
        disabled={busy}
        onClick={() => {
          setBusy(true);
          setRefresh((value) => value + 1);
        }}
      >
        Refresh outputs
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {currentResult && (
        <>
          <p>
            {currentResult.job.name} · {currentResult.job.family.toUpperCase()}{' '}
            · revision {currentResult.job.revision}
          </p>
          {currentResult.warnings.map((warning) => (
            <p className="error" key={warning}>
              {warning}
            </p>
          ))}
          {currentResult.outputs.length === 0 && (
            <p>
              No supported outputs found yet. They appear here when a training
              or sample stage finishes.
            </p>
          )}
          <div className="job-library" aria-label="Output files">
            {currentResult.outputs.map((item) => (
              <button
                key={item.id}
                aria-pressed={item.id === selected}
                onClick={() => setSelected(item.id)}
              >
                {item.path} · {item.status}
              </button>
            ))}
          </div>
          {output && (
            <div className="output-detail" aria-label="Selected output">
              <h3>{output.name}</h3>
              <p>
                Originating run: {output.run_id} · {output.status}
                {output.bytes === undefined
                  ? ''
                  : ` · ${output.bytes.toLocaleString()} bytes`}
              </p>
              {output.error && <p className="error">{output.error}</p>}
              {output.status === 'available' && output.file_url && (
                <>
                  {output.kind === 'image' && (
                    <img
                      src={output.file_url}
                      alt={`Sample ${output.name}`}
                      style={{ maxWidth: '100%', maxHeight: 480 }}
                    />
                  )}
                  {output.kind === 'video' && (
                    <video
                      controls
                      preload="metadata"
                      src={output.file_url}
                      aria-label="Output video"
                      style={{ width: '100%', maxHeight: 480 }}
                    />
                  )}
                  {output.kind === 'audio' && (
                    <audio
                      controls
                      preload="metadata"
                      src={output.file_url}
                      aria-label="Output audio"
                    />
                  )}
                </>
              )}
              {output.file_url &&
                !['missing', 'blocked', 'unreadable'].includes(
                  output.status,
                ) && (
                  <p>
                    {output.status === 'available' &&
                      output.kind !== 'checkpoint' && (
                        <>
                          <a
                            href={output.file_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Open output in browser
                          </a>
                          {' · '}
                        </>
                      )}
                    <a href={`${output.file_url}?download=true`} download>
                      Download output
                    </a>
                  </p>
                )}
              {output.metadata && (
                <details open>
                  <summary>Output metadata</summary>
                  <pre>{JSON.stringify(output.metadata, null, 2)}</pre>
                </details>
              )}
            </div>
          )}
          <p>{currentResult.note}</p>
        </>
      )}
      <h3>Compare with another Run</h3>
      <label className="field">
        Compare with run
        <select
          aria-label="Compare with run"
          value={other}
          disabled={busy}
          onChange={(event) => {
            setOther(event.target.value);
            setComparison(null);
          }}
        >
          <option value="">Select another run</option>
          {runs
            .filter((run) => run.id !== runId && run.status !== 'deleting')
            .map((run) => (
              <option key={run.id} value={run.id}>
                {run.job.name} · {run.job.family.toUpperCase()} · r
                {run.job.revision} · {run.id.slice(0, 8)}
              </option>
            ))}
        </select>
      </label>
      <button disabled={busy || !other} onClick={() => void compare()}>
        Compare inputs
      </button>
      {comparison && comparison.before === runId && (
        <div className="output-comparison" aria-label="Run comparison">
          <p>{comparison.note}</p>
          {comparison.changes.length === 0 ? (
            <p>No differences in the recorded inputs.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Recorded field</th>
                  <th>Current run</th>
                  <th>Compared run</th>
                </tr>
              </thead>
              <tbody>
                {comparison.changes.map((change) => (
                  <tr key={change.path}>
                    <th>{change.path}</th>
                    <td>
                      <pre>{JSON.stringify(change.before, null, 2)}</pre>
                    </td>
                    <td>
                      <pre>{JSON.stringify(change.after, null, 2)}</pre>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}
