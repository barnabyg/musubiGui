import { useEffect, useState } from 'react';
import { Download } from 'lucide-react';
import { apiClient } from './api';
import { sizeLabel } from './runStatus';
import './outputs.css';

export type Output = {
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

// What a Run's files are for. Weights inside a saved state's folder are not a LoRA.
function grouped(outputs: Output[]) {
  const samples = outputs.filter((item) =>
    ['image', 'video', 'audio'].includes(item.kind ?? ''),
  );
  const stateFiles = outputs.filter((item) => /-state\//.test(item.path));
  const checkpoints = outputs
    .filter((item) => item.kind === 'checkpoint' && !stateFiles.includes(item))
    .map((item) => ({ item, step: checkpointStep(item.name) }));
  const final = checkpoints.find(({ step }) => step === null)?.item;
  const preview =
    samples.find(
      (item) => item.kind === 'video' && item.status === 'available',
    ) ??
    samples.find(
      (item) => item.kind === 'image' && item.status === 'available',
    ) ??
    samples.find((item) => item.status === 'available');
  return { samples, stateFiles, checkpoints, final, preview };
}

function checkpointStep(name: string) {
  const match = /-step(\d+)\.[^.]+$/.exec(name);
  return match ? Number(match[1]) : null;
}

function byteLabel(item: Output) {
  return item.bytes === undefined ? 'Size unknown' : sizeLabel(item.bytes);
}

function MediaPreview({ output }: { output: Output }) {
  if (output.status !== 'available' || !output.file_url) return null;
  if (output.kind === 'image')
    return (
      <img
        className="output-media"
        src={output.file_url}
        alt={`Sample ${output.name}`}
      />
    );
  if (output.kind === 'video')
    return (
      <video
        className="output-media"
        controls
        preload="metadata"
        src={output.file_url}
        aria-label="Output video"
      />
    );
  if (output.kind === 'audio')
    return (
      <audio
        controls
        preload="metadata"
        src={output.file_url}
        aria-label="Output audio"
      />
    );
  return null;
}

// Keeps one list of a Run's files for the result at the top and the inventory below it.
export function useRunOutputs(runId: string | undefined, runState: string) {
  const [result, setResult] = useState<OutputList | null>(null);
  const [runs, setRuns] = useState<RunChoice[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!runId) return;
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
  return {
    result: result?.run_id === runId ? result : null,
    runs,
    error,
    busy,
    reload() {
      setBusy(true);
      setRefresh((value) => value + 1);
    },
  };
}

export function stateFiles(outputs: RunOutputs) {
  return grouped(outputs.result?.outputs ?? []).stateFiles;
}

export type RunOutputs = ReturnType<typeof useRunOutputs>;

// What the Run was for, first: the sample and the final LoRA, plus any warnings about them.
export function RunResult({ outputs }: { outputs: RunOutputs }) {
  const { result } = outputs;
  if (!result) return null;
  const { final, preview } = grouped(result.outputs);
  if (!final && !preview && !result.warnings.length) return null;
  return (
    <section className="panel run-result" aria-label="Run result">
      <h2>Result</h2>
      {result.warnings.map((warning) => (
        <p className="error" key={warning}>
          {warning}
        </p>
      ))}
      {preview && <MediaPreview output={preview} />}
      {final && (
        <div className="run-result-lora">
          <span>
            <strong>Final LoRA</strong> {byteLabel(final)}
          </span>
          {final.file_url &&
          !['missing', 'blocked', 'unreadable'].includes(final.status) ? (
            <a
              className="button-link"
              href={`${final.file_url}?download=true`}
              download
            >
              <Download /> Download final LoRA
            </a>
          ) : (
            <span className="error-text">{final.error}</span>
          )}
        </div>
      )}
    </section>
  );
}

export function Outputs({
  runId,
  outputs,
}: {
  runId: string;
  outputs: RunOutputs;
}) {
  const { result: currentResult, runs, error: loadError, reload } = outputs;
  const [selected, setSelected] = useState('');
  const [other, setOther] = useState('');
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [compareError, setCompareError] = useState('');
  const [comparing, setComparing] = useState(false);
  const busy = outputs.busy || comparing;
  const error = compareError || loadError;
  const output = currentResult?.outputs.find((item) => item.id === selected);
  const groups = grouped(currentResult?.outputs ?? []);
  function chooser(item: Output) {
    return (
      <button
        aria-pressed={item.id === selected}
        onClick={() => setSelected(item.id)}
      >
        {item.name} · {item.status}
      </button>
    );
  }
  async function compare() {
    if (!other) return;
    setComparing(true);
    setCompareError('');
    try {
      setComparison(
        await read<Comparison>(`/api/runs/${runId}/compare/${other}`),
      );
    } catch (reason) {
      setCompareError((reason as Error).message);
    } finally {
      setComparing(false);
    }
  }
  return (
    <section className="panel" aria-label="Run outputs">
      <h2>Run outputs</h2>
      <p>Checkpoints and samples from this Run.</p>
      <button disabled={busy} onClick={reload}>
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
          {currentResult.outputs.length === 0 && (
            <p>
              No supported outputs found yet. They appear here when a training
              or sample stage finishes.
            </p>
          )}
          {groups.samples.length > 0 && (
            <>
              <h3>Samples</h3>
              <div className="job-library" aria-label="Output files">
                {groups.samples.map((item) => (
                  <span key={item.id}>{chooser(item)}</span>
                ))}
              </div>
            </>
          )}
          {groups.checkpoints.length > 0 && (
            <details className="output-checkpoints">
              <summary>LoRA checkpoints ({groups.checkpoints.length})</summary>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Step</th>
                    <th scope="col">Size</th>
                    <th scope="col">File</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.checkpoints.map(({ item, step }) => (
                    <tr key={item.id}>
                      <td>{step ?? 'Final'}</td>
                      <td>{byteLabel(item)}</td>
                      <td>{chooser(item)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
          {output && (
            <div className="output-detail" aria-label="Selected output">
              <h3>{output.name}</h3>
              <p>
                {output.status} · {byteLabel(output)} · from Run{' '}
                {output.run_id.slice(0, 8)}
              </p>
              {output.error && <p className="error">{output.error}</p>}
              {output.id !== groups.preview?.id && (
                <MediaPreview output={output} />
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
                <details>
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
