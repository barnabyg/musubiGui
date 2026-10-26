import { useState } from 'react';

type Preview = {
  id: string;
  items: { id: string; name: string; before: string; after: string }[];
};
type Operation = {
  id: string;
  items: { id: string; name: string; status: string; error?: string }[];
};
type Props = {
  datasetId: string;
  selected: string[];
  beforeAction: () => Promise<boolean>;
  onChanged: () => Promise<void>;
};

export function DatasetBulkActions({
  datasetId,
  selected,
  beforeAction,
  onChanged,
}: Props) {
  const [fields, setFields] = useState({
    prefix: '',
    suffix: '',
    find: '',
    replace: '',
  });
  const [preview, setPreview] = useState<Preview | null>(null);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const url = `/api/datasets/${datasetId}`;
  async function request<T>(path: string, data: object): Promise<T> {
    const response = await fetch(url + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const value = (await response.json()) as T & { detail?: string };
    if (!response.ok)
      throw new Error(
        value.detail ??
          'Batch action failed. No unreported changes should be assumed; inspect the per-item results and caption history.',
      );
    return value;
  }
  async function action(work: () => Promise<void>) {
    if (!(await beforeAction())) return;
    setBusy(true);
    setError('');
    try {
      await work();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function field(label: string, key: keyof typeof fields) {
    return (
      <label className="field">
        {label}
        <input
          value={fields[key]}
          onChange={(event) => {
            setFields({ ...fields, [key]: event.target.value });
            setPreview(null);
          }}
        />
      </label>
    );
  }
  const sameSelection =
    preview &&
    [...selected].sort().join() ===
      preview.items
        .map((item) => item.id)
        .sort()
        .join();
  return (
    <details>
      <summary>Selection and bulk caption actions</summary>
      <div className="actions">
        <button
          disabled={busy || !selected.length}
          onClick={() =>
            void action(async () => {
              await request('/exclusions', {
                item_ids: selected,
                excluded: true,
              });
              await onChanged();
            })
          }
        >
          Exclude selection
        </button>
        <button
          disabled={busy || !selected.length}
          onClick={() =>
            void action(async () => {
              await request('/exclusions', {
                item_ids: selected,
                excluded: false,
              });
              await onChanged();
            })
          }
        >
          Include selection
        </button>
      </div>
      <div className="columns">
        {field('Caption prefix', 'prefix')}
        {field('Caption suffix', 'suffix')}
        {field('Find caption text', 'find')}
        {field('Replacement caption text', 'replace')}
      </div>
      <button
        disabled={busy || !selected.length}
        onClick={() =>
          void action(async () => {
            setPreview(
              await request<Preview>('/bulk/preview', {
                item_ids: selected,
                ...fields,
              }),
            );
          })
        }
      >
        Preview caption changes
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {preview && (
        <div aria-label="Bulk caption preview">
          <p>
            Exact preview for {preview.items.length} items. This preview has not
            changed any sidecars.
          </p>
          {preview.items.map((item) => (
            <div className="caption-revision" key={item.id}>
              <h3>{item.name}</h3>
              <p>Before</p>
              <pre>{item.before || '(empty or missing)'}</pre>
              <p>After</p>
              <pre>{item.after || '(intentionally empty)'}</pre>
            </div>
          ))}
          {!sameSelection && (
            <p>Selection changed. Preview the new selection before applying.</p>
          )}
          <button
            className="primary"
            disabled={busy || !sameSelection}
            onClick={() =>
              void action(async () => {
                const result = await request<Operation>('/bulk/apply', {
                  preview_id: preview.id,
                });
                setOperation(result);
                setPreview(null);
                await onChanged();
              })
            }
          >
            Apply previewed captions
          </button>
        </div>
      )}
      {operation && (
        <div aria-label="Bulk caption results">
          <p>Per-item results</p>
          {operation.items.map((item) => (
            <p key={item.id}>
              {item.name}: {item.status}
              {item.error ? ` — ${item.error}` : ''}
            </p>
          ))}
          <button
            disabled={busy}
            onClick={() =>
              void action(async () => {
                setOperation(
                  await request<Operation>('/bulk/restore', {
                    operation_id: operation.id,
                  }),
                );
                await onChanged();
              })
            }
          >
            Restore successful captions
          </button>
        </div>
      )}
    </details>
  );
}
