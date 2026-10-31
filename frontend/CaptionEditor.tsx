import { useState } from 'react';
import { apiClient } from './api';
import type { DatasetItem } from './Datasets';
import {
  type Caption,
  type CaptionSession,
  useCaption,
} from './captionSession';

const readCurrent = apiClient(
  'Could not reload the current sidecar. Your text is retained.',
);
const readHistory = apiClient('Caption history could not be read.');

type Revision = {
  id: string;
  created_utc: string;
  state: string;
  before: Caption;
};
export function CaptionEditor({
  session,
  item,
}: {
  session: CaptionSession;
  item: DatasetItem;
}) {
  const state = useCaption(session, item);
  const { text, status } = state;
  const [error, setError] = useState('');
  const [history, setHistory] = useState<{
    revision: string;
    entries: Revision[];
  } | null>(null);
  const [current, setCurrent] = useState<Caption | null>(null);
  const [retained, setRetained] = useState<string | null>(null);
  const url = `/api/datasets/${session.datasetId}/items/${item.id}/caption`;
  const flush = () => session.flush(item.id);
  async function loadCurrent() {
    try {
      setCurrent(await readCurrent<Caption>(url));
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  async function loadHistory() {
    if (!(await flush())) return;
    try {
      const entries = await readHistory<Revision[]>(`${url}/history`);
      setHistory({ revision: session.get(item).caption.revision, entries });
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  async function restore(id: string) {
    if (await session.restore(item, id)) setHistory(null);
  }
  return (
    <div>
      <label className="field">
        Caption
        <textarea
          aria-label="Caption"
          value={text}
          disabled={Boolean(state.caption.error)}
          onChange={(event) => session.change(item, event.target.value)}
          onBlur={() => void flush()}
        />
      </label>
      <p role="status" aria-live="polite">
        {status}
      </p>
      {(error || state.error) && (
        <div role="alert" className="error">
          {error || state.error}
          <div className="actions">
            <button onClick={() => void flush()}>Retry save</button>
            <button onClick={() => void loadCurrent()}>
              Review current sidecar
            </button>
          </div>
        </div>
      )}
      {current && (
        <div className="panel">
          <h2>Current sidecar</h2>
          <pre>{current.text || '(intentionally empty)'}</pre>
          <button
            disabled={state.saving}
            onClick={() => {
              if (!session.replace(item, current)) return;
              setRetained(state.text);
              setCurrent(null);
              setError('');
            }}
          >
            Use current sidecar
          </button>
          <button
            disabled={state.saving}
            onClick={() => {
              if (!session.replace(item, current, true)) return;
              setCurrent(null);
              void flush();
            }}
          >
            Save my text over this revision
          </button>
        </div>
      )}
      {retained !== null && (
        <label className="field">
          Retained unsaved caption
          <textarea
            aria-label="Retained unsaved caption"
            readOnly
            value={retained}
          />
        </label>
      )}
      <button onClick={() => void loadHistory()}>Caption history</button>
      {history && history.revision === state.caption.revision && (
        <div>
          <p>{history.entries.length} recoverable caption revisions</p>
          {history.entries.map((entry) => (
            <div className="caption-revision" key={entry.id}>
              <small>
                {entry.created_utc} · {entry.state}
              </small>
              <pre>
                {entry.before.text ||
                  (entry.before.exists ? '(empty)' : '(missing sidecar)')}
              </pre>
              <button onClick={() => void restore(entry.id)}>
                Restore this caption
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
