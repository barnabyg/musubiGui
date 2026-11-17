import { useEffect, useState } from 'react';
import { apiClient } from './api';
import { trainingOf, trainings } from './runChains';
import { useRunHistory } from './runHistory';
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
  modified_ms?: number;
  file_url?: string;
  error?: string | null;
  metadata?: Record<string, unknown>;
  // In a training's merged list: the segment the file came from and its global step.
  segment?: Segment;
  step?: number;
};
type Segment = {
  run_id: string;
  position: number;
  revision: number;
  status: string;
};
type OutputList = {
  run_id: string;
  job: {
    name: string;
    family: string;
    revision: number;
    config?: { sample_prompt?: string; seed?: number };
  };
  status: string;
  outputs: Output[];
  warnings: string[];
  note: string;
  // Listed with `chain`: the training's segments and its latest one.
  segments?: Segment[];
  latest?: string;
};
type Comparison = {
  // The picker choices compared, so a changed choice hides a stale result.
  key: string;
  changes: { path: string; before: unknown; after: unknown }[];
  // Caption and media differences between the two dataset snapshots, counted.
  dataset: string[];
  note: string;
  samples: [OutputList, OutputList];
};

const read = apiClient('Output records could not be read. Refresh and retry.');

function mediaFiles(outputs: Output[]) {
  return outputs.filter((item) =>
    ['image', 'video', 'audio'].includes(item.kind ?? ''),
  );
}

function latestSample(outputs: Output[]) {
  return mediaFiles(outputs).reduce<Output | undefined>(
    (latest, item) =>
      item.status === 'available' &&
      item.file_url &&
      (!latest || (item.modified_ms ?? 0) >= (latest.modified_ms ?? 0))
        ? item
        : latest,
    undefined,
  );
}

// Whether a list merges more than one segment of a training.
function merged(result: OutputList | null | undefined) {
  return (result?.segments?.length ?? 1) > 1;
}

// How a file reads in the comparison's sample list: in a merged list, with the segment it came from.
function outputLabel(item: Output, result: OutputList | null | undefined) {
  const segment =
    merged(result) && item.segment ? ` · segment ${item.segment.position}` : '';
  return `${item.name} · ${item.status}${segment}`;
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

function ComparisonSamples({
  result,
  label,
}: {
  result: OutputList;
  label: string;
}) {
  const samples = mediaFiles(result.outputs);
  const available = samples.filter((item) => item.status === 'available');
  const preview =
    available.find((item) => item.path.startsWith('samples/')) ??
    available.find((item) => item.kind === 'video') ??
    available[0];
  const [selected, setSelected] = useState('');
  const sample =
    samples.find((item) => item.id === selected) ?? preview ?? samples[0];
  return (
    <section className="comparison-samples" aria-label={label}>
      <p>
        <strong>{label}</strong>
      </p>
      <p>
        <strong>{result.job.name}</strong> · {result.job.family.toUpperCase()} ·
        revision {result.job.revision}
      </p>
      <p className="comparison-run-id">
        {merged(result)
          ? `Training of ${result.segments?.length} segments · latest Run ${result.latest}`
          : `Run ${result.run_id}`}
      </p>
      <p>
        Recorded sample prompt (Run settings):{' '}
        {result.job.config?.sample_prompt ?? 'Not recorded'}
      </p>
      <p>Recorded seed: {result.job.config?.seed ?? 'Not recorded'}</p>
      {result.warnings.map((warning) => (
        <p className="error" key={warning}>
          {warning}
        </p>
      ))}
      {samples.length ? (
        <>
          <label className="field">
            Sample
            <select
              aria-label="Sample"
              value={sample?.id ?? ''}
              onChange={(event) => setSelected(event.target.value)}
            >
              {samples.map((item) => (
                <option key={item.id} value={item.id}>
                  {outputLabel(item, result)}
                </option>
              ))}
            </select>
          </label>
          {sample && (
            <>
              <p>{outputLabel(sample, result)}</p>
              {sample.error && <p className="error">{sample.error}</p>}
              <MediaPreview key={sample.id} output={sample} />
            </>
          )}
        </>
      ) : (
        <p>No generated samples recorded for this Run.</p>
      )}
    </section>
  );
}

const settledStates = [
  'completed',
  'failed',
  'stopped',
  'cancelled',
  'interrupted',
];

// Both tabs share the output inventory and the selection for this Run.
export function useRunOutputs(
  runId: string | undefined,
  runState: string,
  chain = false,
) {
  const [result, setResult] = useState<OutputList | null>(null);
  const [selection, setSelection] = useState<{
    runId: string;
    sample?: string;
  }>();
  const history = useRunHistory();
  const [failure, setFailure] = useState<{ runId?: string; message: string }>();
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!runId) return;
    let current = true;
    let controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const active = ['training', 'generating', 'stopping'].some((state) =>
      runState.includes(state),
    );
    const poll = () => {
      controller = new AbortController();
      // A training resumed in segments lists every segment's files; a single Run, its own.
      void read<OutputList>(
        `/api/runs/${runId}/outputs${chain ? '?chain=true' : ''}`,
        'GET',
        undefined,
        controller.signal,
      )
        .then((outputs) => {
          if (!current) return;
          setResult(outputs);
          setFailure(undefined);
        })
        .catch((reason: Error) => {
          if (current) setFailure({ runId, message: reason.message });
        })
        .finally(() => {
          if (current) setBusy(false);
          if (current && active) timer = setTimeout(poll, 5000);
        });
    };
    poll();
    return () => {
      current = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [runId, runState, chain, refresh]);
  const chosen = selection?.runId === runId ? selection : undefined;
  return {
    selected: chosen?.sample,
    select(item: Output) {
      if (!runId) return;
      setSelection({ runId, sample: item.id });
    },
    result: result?.run_id === runId ? result : null,
    runs: history.runs ?? [],
    error: failure?.runId === runId ? failure?.message : '',
    busy,
    reload() {
      setBusy(true);
      setRefresh((value) => value + 1);
    },
  };
}

export type RunOutputs = ReturnType<typeof useRunOutputs>;

// Artifact warnings, and sample errors once nothing is still writing the files.
export function RunWarnings({ outputs }: { outputs: RunOutputs }) {
  const { result } = outputs;
  if (!result) return null;
  const problems = settledStates.includes(result.status)
    ? result.outputs.filter((item) => item.error)
    : [];
  return (
    <>
      {result.warnings.map((warning) => (
        <p className="error" key={warning}>
          {warning}
        </p>
      ))}
      {problems.map((item) => (
        <p className="error" key={item.id}>
          {item.name}: {item.error}
        </p>
      ))}
    </>
  );
}

function Chooser({ item, outputs }: { item: Output; outputs: RunOutputs }) {
  return (
    <button
      aria-pressed={item.id === outputs.selected}
      onClick={() => outputs.select(item)}
    >
      {item.name} · {item.status}
    </button>
  );
}

function OutputDetail({ output, label }: { output: Output; label: string }) {
  return (
    <div className="output-detail" aria-label={label}>
      <p>
        <strong>{output.name}</strong>
      </p>
      <p>
        {output.status} · {byteLabel(output)} · from Run{' '}
        {output.run_id.slice(0, 8)}
        {output.segment && output.step !== undefined && (
          <>
            {' '}
            · segment {output.segment.position} · step {output.step}
          </>
        )}
      </p>
      {output.error && <p className="error">{output.error}</p>}
      <MediaPreview output={output} />
      {output.file_url &&
        !['missing', 'blocked', 'unreadable'].includes(output.status) && (
          <p>
            {output.status === 'available' && output.kind !== 'checkpoint' && (
              <>
                <a href={output.file_url} target="_blank" rel="noreferrer">
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
  );
}

function LoadError({ outputs }: { outputs: RunOutputs }) {
  return outputs.error ? (
    <p className="error" role="alert">
      {outputs.error}
    </p>
  ) : null;
}

// A folder handoff lets the user collect multiple files together in Explorer.
export function RunOutputActions({
  directory,
  outputs,
  completed,
  samplingDisabled,
  openSamples,
}: {
  directory: string;
  outputs: RunOutputs;
  completed: boolean;
  samplingDisabled: boolean;
  openSamples: () => void;
}) {
  const [copy, setCopy] = useState<'idle' | 'copied' | 'manual'>('idle');
  const { result } = outputs;
  const latest = latestSample(
    result?.outputs.filter((item) => item.run_id === result.run_id) ?? [],
  );
  async function copyFolder() {
    try {
      await navigator.clipboard.writeText(directory);
      setCopy('copied');
    } catch {
      setCopy('manual');
    }
  }
  return (
    <>
      <div className="actions">
        <button title={directory} onClick={() => void copyFolder()}>
          Copy output folder
        </button>
        {latest && (
          <button
            onClick={() => {
              outputs.select(latest);
              openSamples();
            }}
          >
            View sample
          </button>
        )}
        {copy === 'copied' && (
          <span role="status">
            Folder path copied. Open its checkpoints subfolder in File Explorer.
          </span>
        )}
      </div>
      {copy === 'manual' && (
        <label className="field">
          Copy this folder path for File Explorer
          <input
            readOnly
            value={directory}
            onFocus={(event) => event.target.select()}
          />
        </label>
      )}
      {completed && outputs.result && !latest && (
        <p>
          {samplingDisabled
            ? 'Sampling was disabled for this Run.'
            : 'No playable sample is available.'}
        </p>
      )}
      <LoadError outputs={outputs} />
    </>
  );
}

// The Samples tab: generated media and nothing else.
export function RunSamples({ outputs }: { outputs: RunOutputs }) {
  const { result } = outputs;
  const samples = mediaFiles(result?.outputs ?? []);
  const output = samples.find((item) => item.id === outputs.selected);
  return (
    <section className="panel" aria-label="Samples">
      <h2>Samples</h2>
      {merged(result) && (
        <p>
          Samples from all {result?.segments?.length} segments of this training,
          in step order.
        </p>
      )}
      <LoadError outputs={outputs} />
      {result && samples.length === 0 && (
        <p>
          No generated samples recorded for this Run. Samples appear here when
          available.
        </p>
      )}
      {samples.length > 0 && (
        <div className="job-library" aria-label="Output files">
          {samples.map((item) => (
            <span key={item.id}>
              <Chooser item={item} outputs={outputs} />
              {merged(result) && item.segment && (
                <small> Segment {item.segment.position}</small>
              )}
            </span>
          ))}
        </div>
      )}
      {output && <OutputDetail output={output} label="Selected sample" />}
    </section>
  );
}

export function RunComparison({
  runId,
  outputs,
}: {
  runId: string;
  outputs: RunOutputs;
}) {
  const [other, setOther] = useState('');
  const [side, setSide] = useState<'training' | 'segment'>('training');
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [error, setError] = useState('');
  const [comparing, setComparing] = useState(false);
  const busy = outputs.busy || comparing;
  const own = trainingOf(outputs.runs, runId);
  const ownTraining = own && own.segments.length > 1 ? own : undefined;
  const fromTraining = Boolean(ownTraining) && side === 'training';
  // A training compares as its latest segment's inputs, with samples from every segment.
  const others = trainings(outputs.runs).filter(
    (training) =>
      training.segments.length > 1 &&
      training.id !== own?.id &&
      training.latest.status !== 'deleting',
  );
  const key = `${runId}|${fromTraining}|${other}`;
  function resolve(choice: string) {
    const training = others.find((item) => `chain:${item.id}` === choice);
    return training
      ? { id: training.latest.id, chain: true }
      : { id: choice, chain: false };
  }
  async function compare() {
    if (!other) return;
    setComparing(true);
    setError('');
    const before = fromTraining
      ? { id: ownTraining!.latest.id, chain: true }
      : { id: runId, chain: false };
    const after = resolve(other);
    const list = (side: { id: string; chain: boolean }) =>
      read<OutputList>(
        `/api/runs/${side.id}/outputs${side.chain ? '?chain=true' : ''}`,
      );
    try {
      const [changes, first, second] = await Promise.all([
        read<Omit<Comparison, 'samples' | 'key'>>(
          `/api/runs/${before.id}/compare/${after.id}`,
        ),
        list(before),
        list(after),
      ]);
      setComparison({ ...changes, key, samples: [first, second] });
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setComparing(false);
    }
  }
  return (
    <section className="panel" aria-label="Compare with another Run">
      <h2>Compare with another Run</h2>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {ownTraining && (
        <label className="field">
          Compare from
          <select
            aria-label="Compare from"
            value={side}
            disabled={busy}
            onChange={(event) => {
              setSide(event.target.value as 'training' | 'segment');
              setComparison(null);
            }}
          >
            <option value="training">
              This training ({ownTraining.segments.length} segments)
            </option>
            <option value="segment">This segment only</option>
          </select>
        </label>
      )}
      <div className="run-compare-picker">
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
            {others.map((training) => (
              <option key={training.id} value={`chain:${training.id}`}>
                Training: {training.latest.job.name} ·{' '}
                {training.latest.job.family.toUpperCase()} · r
                {training.latest.job.revision} · {training.segments.length}{' '}
                segments
              </option>
            ))}
            {outputs.runs
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
      </div>
      {comparison && comparison.key === key && (
        <div className="output-comparison" aria-label="Run comparison">
          <p>{comparison.note}</p>
          {comparison.dataset.length > 0 && (
            <p>Dataset differences: {comparison.dataset.join(', ')}.</p>
          )}
          <div className="comparison-media">
            {comparison.samples.map((result, index) => (
              <ComparisonSamples
                key={`${index}-${result.run_id}`}
                result={result}
                label={
                  index === 0 ? 'Current Run samples' : 'Compared Run samples'
                }
              />
            ))}
          </div>
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

// The latest playable sample across this Job's training segments.
export function LatestOutputs({ outputs }: { outputs: RunOutputs }) {
  const sample = latestSample(outputs.result?.outputs ?? []);
  return (
    <div role="group" aria-label="Latest outputs" className="latest-outputs">
      <LoadError outputs={outputs} />
      {outputs.result && !sample && <p>No previews yet.</p>}
      {sample && (
        <figure>
          <MediaPreview output={sample} />
          <figcaption>
            Latest preview: {sample.name}
            {sample.step === undefined ? '' : ` · step ${sample.step}`}
          </figcaption>
        </figure>
      )}
    </div>
  );
}
