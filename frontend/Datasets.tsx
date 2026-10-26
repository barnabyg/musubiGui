import { useCallback, useEffect, useRef, useState } from 'react';
import './datasets.css';
import { CaptionEditor, type CaptionEditorHandle } from './CaptionEditor';
import { H3Coverage, H3Inspector } from './H3Inspector';
import { DatasetBulkActions } from './DatasetBulkActions';

export type Dataset = {
  id: string;
  name: string;
  path: string;
  managed: boolean;
  missing: boolean;
  counts: {
    total: number;
    valid: number;
    included: number;
    captioned: number;
    excluded: number;
  };
  scan: { state: string; completed: number; total: number; error?: string };
};
export type DatasetItem = {
  id: string;
  path: string;
  name: string;
  kind: 'image' | 'video';
  valid: boolean;
  excluded: boolean;
  error: string;
  metadata: Record<string, unknown>;
  caption: { text: string; revision: string; exists: boolean; error?: string };
};

async function api<T>(url: string, data?: object): Promise<T> {
  const response = await fetch(
    url,
    data
      ? {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
        }
      : undefined,
  );
  if (!response.ok) {
    const body = (await response.json()) as { detail?: unknown };
    throw new Error(
      typeof body.detail === 'string'
        ? body.detail
        : `Request failed (${response.status}).`,
    );
  }
  return response.json() as Promise<T>;
}

export function Datasets({ visible = true }: { visible?: boolean }) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [items, setItems] = useState<DatasetItem[]>([]);
  const [active, setActive] = useState<DatasetItem | null>(null);
  const [name, setName] = useState('');
  const [folder, setFolder] = useState('');
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [scroll, setScroll] = useState(0);
  const [columns, setColumns] = useState(3);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('name');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const gallery = useRef<HTMLDivElement>(null);
  const editor = useRef<CaptionEditorHandle>(null);
  const refresh = useCallback(async (id?: string) => {
    const list = await api<Dataset[]>('/api/datasets');
    setDatasets(list);
    if (id) {
      setDataset(list.find((value) => value.id === id) ?? null);
      setItems(await api<DatasetItem[]>(`/api/datasets/${id}/items`));
    }
  }, []);
  useEffect(() => {
    void api<Dataset[]>('/api/datasets')
      .then(setDatasets)
      .catch((reason: Error) => setError(reason.message));
  }, []);
  useEffect(() => {
    if (!gallery.current) return;
    const observer = new ResizeObserver((entries) =>
      setColumns(Math.max(1, Math.floor(entries[0].contentRect.width / 170))),
    );
    observer.observe(gallery.current);
    return () => observer.disconnect();
  }, [dataset?.id, visible]);
  useEffect(() => {
    if (!dataset || !['pending', 'scanning'].includes(dataset.scan.state))
      return;
    const timer = window.setInterval(() => {
      void refresh(dataset.id).catch((reason: Error) =>
        setError(reason.message),
      );
    }, 700);
    return () => window.clearInterval(timer);
  }, [dataset, refresh]);
  useEffect(() => {
    if (!visible) void editor.current?.flush();
  }, [visible]);
  async function select(item: DatasetItem | null) {
    if (editor.current && !(await editor.current.flush())) return;
    setActive(item);
  }
  async function open(id: string) {
    if (editor.current && !(await editor.current.flush())) return;
    setActive(null);
    setSelected(new Set());
    setQuery('');
    setFilter('all');
    setScroll(0);
    setError('');
    try {
      await refresh(id);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  async function register() {
    setError('');
    try {
      const created = await api<Dataset>('/api/datasets', {
        name,
        path: folder,
      });
      await open(created.id);
      setStatus(
        'Dataset registered. Source media stays in its original folder.',
      );
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  async function rescan() {
    if (!dataset) return;
    try {
      await api(`/api/datasets/${dataset.id}/scan`, {});
      await refresh(dataset.id);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  const rowHeight = 170;
  const visibleItems = items
    .filter((item) => {
      const matches = (item.name + '\n' + item.caption.text)
        .toLowerCase()
        .includes(query.toLowerCase());
      return (
        matches &&
        (filter === 'all' ||
          (filter === 'missing' && !item.caption.text.trim()) ||
          (filter === 'invalid' && !item.valid) ||
          (filter === 'excluded' && item.excluded) ||
          (filter === 'included' && item.valid && !item.excluded) ||
          (filter === 'selected' && selected.has(item.id)))
      );
    })
    .sort((a, b) =>
      sort === 'name-desc'
        ? b.name.localeCompare(a.name)
        : sort === 'caption'
          ? a.caption.text.localeCompare(b.caption.text) ||
            a.name.localeCompare(b.name)
          : a.name.localeCompare(b.name),
    );
  const start = Math.max(0, Math.floor(scroll / rowHeight) - 1) * columns;
  const end = Math.min(visibleItems.length, start + columns * 5);
  return (
    <>
      <section className="title">
        <p className="eyebrow">YOUR MEDIA, YOUR CAPTIONS</p>
        <h1>Datasets</h1>
        <p>
          Register a local folder within the dataset roots configured in
          Settings.
        </p>
      </section>
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      <section className="panel">
        <h2>Dataset library</h2>
        <div className="columns">
          <label className="field">
            Dataset name
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="field">
            Dataset folder
            <input
              value={folder}
              onChange={(event) => setFolder(event.target.value)}
              placeholder="C:\datasets\…"
            />
          </label>
        </div>
        <button onClick={() => void register()} disabled={!name || !folder}>
          Register folder
        </button>
        <div className="dataset-library">
          {datasets.map((value) => (
            <button
              key={value.id}
              className={dataset?.id === value.id ? 'primary' : ''}
              onClick={() => void open(value.id)}
            >
              {value.name}
              <small>
                {value.counts.total} media · {value.counts.captioned} captioned
                · {value.counts.included} included
                {value.missing ? ' · folder missing' : ''}
              </small>
            </button>
          ))}
        </div>
      </section>
      <p role="status">{status}</p>
      {dataset && (
        <section className="panel">
          <div className="actions">
            <h2>{dataset.name}</h2>
            <button onClick={() => void rescan()}>Re-scan</button>
          </div>
          <p>{dataset.path}</p>
          <p role="status">
            Scan: {dataset.scan.state} {dataset.scan.completed}/
            {dataset.scan.total} {dataset.scan.error}
          </p>
          <p>
            {dataset.counts.included} included · {dataset.counts.excluded}{' '}
            excluded · {dataset.counts.total - dataset.counts.valid} invalid
          </p>
          <H3Coverage key={dataset.id} datasetId={dataset.id} />
          <div className="columns">
            <label className="field">
              Search media and captions
              <input
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setScroll(0);
                }}
              />
            </label>
            <label className="field">
              Filter media
              <select
                aria-label="Filter media"
                value={filter}
                onChange={(event) => {
                  setFilter(event.target.value);
                  setScroll(0);
                }}
              >
                <option value="all">All media</option>
                <option value="missing">Missing captions</option>
                <option value="invalid">Invalid media</option>
                <option value="excluded">Excluded</option>
                <option value="included">Included</option>
                <option value="selected">Selected</option>
              </select>
            </label>
            <label className="field">
              Sort media
              <select
                aria-label="Sort media"
                value={sort}
                onChange={(event) => {
                  setSort(event.target.value);
                  setScroll(0);
                }}
              >
                <option value="name">Name A–Z</option>
                <option value="name-desc">Name Z–A</option>
                <option value="caption">Caption</option>
              </select>
            </label>
          </div>
          <p>
            {visibleItems.length} matching · {selected.size} selected (
            {
              [...selected].filter(
                (id) => !visibleItems.some((item) => item.id === id),
              ).length
            }{' '}
            hidden by filters)
          </p>
          <div className="actions">
            <button
              onClick={() =>
                setSelected(
                  (values) =>
                    new Set([
                      ...values,
                      ...visibleItems.map((item) => item.id),
                    ]),
                )
              }
            >
              Select visible matches
            </button>
            <button onClick={() => setSelected(new Set())}>
              Clear selection
            </button>
          </div>
          <DatasetBulkActions
            key={dataset.id}
            datasetId={dataset.id}
            selected={[...selected]}
            beforeAction={async () =>
              editor.current ? editor.current.flush() : true
            }
            onChanged={async () => {
              setActive(null);
              await refresh(dataset.id);
            }}
          />
          <div className="dataset-workspace">
            <div
              className="dataset-gallery"
              ref={gallery}
              onScroll={(event) => setScroll(event.currentTarget.scrollTop)}
              aria-label="Dataset gallery"
            >
              <div
                style={{
                  height: Math.ceil(visibleItems.length / columns) * rowHeight,
                  position: 'relative',
                }}
              >
                {visibleItems.slice(start, end).map((item, offset) => {
                  const index = start + offset;
                  return (
                    <div
                      className={`media-card ${selected.has(item.id) ? 'picked' : ''}`}
                      key={item.id}
                      style={{
                        position: 'absolute',
                        top: Math.floor(index / columns) * rowHeight,
                        left: `${((index % columns) * 100) / columns}%`,
                        width: `${100 / columns}%`,
                        height: rowHeight - 8,
                      }}
                    >
                      <button
                        className={`media-tile ${active?.id === item.id ? 'selected' : ''}`}
                        onClick={() => void select(item)}
                      >
                        {item.valid ? (
                          <img
                            loading="lazy"
                            src={`/api/datasets/${dataset.id}/items/${item.id}/thumbnail`}
                            alt={item.name}
                          />
                        ) : (
                          <span className="invalid-media">Cannot preview</span>
                        )}
                        <span>{item.name}</span>
                        <small>
                          {item.kind}
                          {item.caption.exists
                            ? ' · caption'
                            : ' · missing caption'}
                          {item.excluded ? ' · excluded' : ''}
                        </small>
                      </button>
                      <input
                        className="select-item"
                        aria-label={`Select ${item.name}`}
                        type="checkbox"
                        checked={selected.has(item.id)}
                        onChange={(event) =>
                          setSelected((values) => {
                            const next = new Set(values);
                            if (event.target.checked) next.add(item.id);
                            else next.delete(item.id);
                            return next;
                          })
                        }
                      />
                    </div>
                  );
                })}
              </div>
            </div>
            {active && (
              <div
                className="media-viewer"
                key={active.id}
                tabIndex={0}
                onKeyDown={(event) => {
                  if (
                    event.altKey &&
                    ['ArrowLeft', 'ArrowRight'].includes(event.key)
                  ) {
                    event.preventDefault();
                    const index =
                      visibleItems.findIndex((item) => item.id === active.id) +
                      (event.key === 'ArrowRight' ? 1 : -1);
                    if (visibleItems[index]) void select(visibleItems[index]);
                  }
                }}
              >
                <h2>{active.name}</h2>
                {active.valid ? (
                  active.kind === 'image' ? (
                    <img
                      src={`/api/datasets/${dataset.id}/items/${active.id}/media`}
                      alt={`Preview ${active.name}`}
                    />
                  ) : (
                    <video
                      controls
                      preload="metadata"
                      src={`/api/datasets/${dataset.id}/items/${active.id}/media`}
                    />
                  )
                ) : (
                  <p role="alert">{active.error}</p>
                )}
                <div className="actions">
                  <button
                    disabled={
                      visibleItems.findIndex((item) => item.id === active.id) <=
                      0
                    }
                    onClick={() =>
                      void select(
                        visibleItems[
                          visibleItems.findIndex(
                            (item) => item.id === active.id,
                          ) - 1
                        ],
                      )
                    }
                  >
                    Previous
                  </button>
                  <button
                    disabled={
                      visibleItems.findIndex((item) => item.id === active.id) >=
                      visibleItems.length - 1
                    }
                    onClick={() =>
                      void select(
                        visibleItems[
                          visibleItems.findIndex(
                            (item) => item.id === active.id,
                          ) + 1
                        ],
                      )
                    }
                  >
                    Next
                  </button>
                </div>
                <CaptionEditor
                  ref={editor}
                  datasetId={dataset.id}
                  item={active}
                  onSaved={(caption) => {
                    setActive((value) =>
                      value?.id === active.id ? { ...value, caption } : value,
                    );
                    setItems((values) =>
                      values.map((item) =>
                        item.id === active.id ? { ...item, caption } : item,
                      ),
                    );
                  }}
                />
                {active.kind === 'video' && (
                  <H3Inspector datasetId={dataset.id} itemId={active.id} />
                )}
                <details>
                  <summary>Media metadata</summary>
                  <pre>{JSON.stringify(active.metadata, null, 2)}</pre>
                </details>
              </div>
            )}
          </div>
        </section>
      )}
    </>
  );
}
