import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import type { DatasetItem } from './Datasets';

type Caption = DatasetItem['caption'];
type Revision = {
  id: string;
  created_utc: string;
  state: string;
  before: Caption;
};
export type CaptionEditorHandle = { flush: () => Promise<boolean> };

export const CaptionEditor = forwardRef<
  CaptionEditorHandle,
  { datasetId: string; item: DatasetItem; onSaved: (caption: Caption) => void }
>(function CaptionEditor({ datasetId, item, onSaved }, ref) {
  const [text, setText] = useState(item.caption.text);
  const [status, setStatus] = useState(item.caption.error ?? 'Caption loaded');
  const [error, setError] = useState('');
  const [history, setHistory] = useState<Revision[] | null>(null);
  const [current, setCurrent] = useState<Caption | null>(null);
  const [retained, setRetained] = useState<string | null>(null);
  const draft = useRef(item.caption.text);
  const saved = useRef(item.caption.text);
  const revision = useRef(item.caption.revision);
  const pending = useRef<Promise<boolean> | null>(null);
  const savedCallback = useRef(onSaved);
  const url = `/api/datasets/${datasetId}/items/${item.id}/caption`;
  useEffect(() => {
    savedCallback.current = onSaved;
  }, [onSaved]);
  const flush = useCallback(async (): Promise<boolean> => {
    if (pending.current && !(await pending.current)) return false;
    if (draft.current === saved.current) return true;
    const requested = draft.current;
    setStatus('Saving…');
    setError('');
    const write = async () => {
      try {
        const response = await fetch(url, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: requested, revision: revision.current }),
        });
        const value = (await response.json()) as Caption & { detail?: string };
        if (!response.ok)
          throw new Error(
            value.detail ??
              'Caption save failed. Retry when access is restored.',
          );
        revision.current = value.revision;
        saved.current = requested;
        setStatus(draft.current === requested ? 'Saved' : 'Unsaved changes');
        savedCallback.current(value);
        setHistory(null);
        return true;
      } catch (reason) {
        setStatus('Save failed — text retained');
        setError((reason as Error).message);
        return false;
      } finally {
        pending.current = null;
      }
    };
    pending.current = write();
    return pending.current;
  }, [url]);
  useImperativeHandle(ref, () => ({ flush }), [flush]);
  useEffect(() => {
    if (text === saved.current) return;
    const timer = window.setTimeout(() => {
      void flush();
    }, 350);
    return () => window.clearTimeout(timer);
  }, [text, flush]);
  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (draft.current !== saved.current) event.preventDefault();
    };
    window.addEventListener('beforeunload', protect);
    return () => window.removeEventListener('beforeunload', protect);
  }, []);
  async function loadCurrent() {
    try {
      const response = await fetch(url);
      if (!response.ok)
        throw new Error(
          'Could not reload the current sidecar. Your text is retained.',
        );
      setCurrent((await response.json()) as Caption);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  async function loadHistory() {
    try {
      const response = await fetch(`${url}/history`);
      if (!response.ok) throw new Error('Caption history could not be read.');
      setHistory((await response.json()) as Revision[]);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  async function restore(id: string) {
    if (!(await flush())) return;
    try {
      const response = await fetch(`${url}/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ history_id: id, revision: revision.current }),
      });
      const value = (await response.json()) as Caption & { detail?: string };
      if (!response.ok)
        throw new Error(
          value.detail ?? 'Restore failed. Current text is retained.',
        );
      draft.current = saved.current = value.text;
      revision.current = value.revision;
      setText(value.text);
      savedCallback.current(value);
      setStatus('Saved');
      setHistory(null);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  return (
    <div>
      <label className="field">
        Caption
        <textarea
          aria-label="Caption"
          value={text}
          disabled={Boolean(item.caption.error)}
          onChange={(event) => {
            draft.current = event.target.value;
            setText(event.target.value);
            setStatus('Unsaved changes');
          }}
        />
      </label>
      <p role="status" aria-live="polite">
        {status}
      </p>
      {error && (
        <div role="alert" className="error">
          {error}
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
            onClick={() => {
              setRetained(draft.current);
              draft.current = saved.current = current.text;
              revision.current = current.revision;
              setText(current.text);
              savedCallback.current(current);
              setCurrent(null);
              setError('');
              setStatus('Caption reloaded — previous draft retained below');
            }}
          >
            Use current sidecar
          </button>
          <button
            onClick={() => {
              revision.current = current.revision;
              saved.current = current.text;
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
      {history && (
        <div>
          <p>{history.length} recoverable caption revisions</p>
          {history.map((entry) => (
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
});
