import { useEffect, useState } from 'react';
import {
  MediaPreview,
  useOutputPreviews,
  type OutputPreviews,
} from './OutputPreview';
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
const read = apiClient('Output records could not be read. Refresh and retry.');

function isSample(item: Output) {
  return (
    item.kind === 'image' || item.kind === 'video' || item.kind === 'audio'
  );
}

// Whether a list merges more than one segment of a training.
function merged(result: OutputList | null | undefined) {
  return (result?.segments?.length ?? 1) > 1;
}

function byteLabel(item: Output) {
  return item.bytes === undefined ? 'Size unknown' : sizeLabel(item.bytes);
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
    sample: string;
  }>();
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
  const previews = useOutputPreviews();
  const chosen = selection?.runId === runId ? selection : undefined;
  const current = result?.run_id === runId ? result : null;
  return {
    previews,
    selected: chosen?.sample,
    select(item: Output) {
      if (!runId) return;
      setSelection({ runId, sample: item.id });
    },
    result: current,
    error: failure?.runId === runId ? failure?.message : '',
    busy,
    reload() {
      previews.retry();
      setBusy(true);
      setRefresh((value) => value + 1);
    },
  };
}

export type RunOutputs = ReturnType<typeof useRunOutputs>;

// Run-wide output warnings and settled checkpoint errors.
export function RunWarnings({ outputs }: { outputs: RunOutputs }) {
  const { result } = outputs;
  if (!result) return null;
  const problems = settledStates.includes(result.status)
    ? result.outputs.filter((item) => item.error && !isSample(item))
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

function OutputDetail({
  output,
  previews,
}: {
  output: Output;
  previews: OutputPreviews;
}) {
  return (
    <div className="output-detail" aria-label="Selected sample">
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
      <MediaPreview output={output} previews={previews} />
      {output.file_url &&
        !['missing', 'blocked', 'unreadable'].includes(output.status) && (
          <p>
            {previews.playable(output) && (
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

// The Samples tab: generated media and nothing else.
export function RunSamples({ outputs }: { outputs: RunOutputs }) {
  const { result } = outputs;
  const samples = (result?.outputs ?? []).filter(isSample);
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
      {output && (
        <OutputDetail
          key={output.id}
          output={output}
          previews={outputs.previews}
        />
      )}
    </section>
  );
}

// The latest playable sample across this Job's training segments.
export function LatestOutputs({ outputs }: { outputs: RunOutputs }) {
  const sample = outputs.previews.latest(outputs.result?.outputs ?? []);
  return (
    <div role="group" aria-label="Latest outputs" className="latest-outputs">
      <LoadError outputs={outputs} />
      {outputs.result && !sample && <p>No previews yet.</p>}
      {sample && (
        <figure>
          <MediaPreview output={sample} previews={outputs.previews} />
          <figcaption>
            Latest preview: {sample.name}
            {sample.step === undefined ? '' : ` · step ${sample.step}`}
          </figcaption>
        </figure>
      )}
    </div>
  );
}
