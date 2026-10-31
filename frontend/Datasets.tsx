import {
  useCallback,
  useEffect,
  useRef,
  useEffectEvent,
  useState,
  useSyncExternalStore,
} from 'react';
import './datasets.css';
import { apiClient } from './api';
import { confirmDialog } from './ConfirmDialog';
import { CaptionEditor } from './CaptionEditor';
import { InlineCaption } from './InlineCaption';
import { type Caption, useCaptionSession } from './captionSession';
import { H3Coverage, H3Inspector } from './H3Inspector';
import { DatasetBulkActions } from './DatasetBulkActions';
import { DatasetUploads } from './DatasetUploads';

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

const api = apiClient((status) => `Request failed (${status}).`);

export function Datasets({
  visible = true,
  selectedId,
  onSelect,
  defaultFolder = '',
  onEditorChange,
}: {
  visible?: boolean;
  selectedId?: string;
  onSelect?: (id?: string, replace?: boolean) => void;
  defaultFolder?: string;
  onEditorChange?: (editing: boolean) => void;
}) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [screen, setScreen] = useState<'library' | 'create' | 'edit'>(
    'library',
  );
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [panel, setPanel] = useState<'settings' | 'tools' | 'uploads' | null>(
    null,
  );
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [items, setItems] = useState<DatasetItem[]>([]);
  const [active, setActive] = useState<DatasetItem | null>(null);
  const [name, setName] = useState('');
  const [editedFolder, setFolder] = useState<string>();
  const folder = editedFolder ?? defaultFolder;
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [scroll, setScroll] = useState(0);
  const [columns, setColumns] = useState(3);
  const [galleryHeight, setGalleryHeight] = useState(640);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('name');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<{
    id: string;
    order: string[];
  } | null>(null);
  // Captions saved in this tab, with when the save landed, so an item list
  // requested before a save cannot put the older caption back.
  const savedCaptions = useRef(
    new Map<string, { caption: Caption; at: number }>(),
  );
  const onCaptionSaved = useCallback((id: string, caption: Caption) => {
    savedCaptions.current.set(id, { caption, at: performance.now() });
    setActive((value) => (value?.id === id ? { ...value, caption } : value));
    setItems((values) =>
      values.map((item) => (item.id === id ? { ...item, caption } : item)),
    );
  }, []);
  const captions = useCaptionSession(dataset?.id ?? '', onCaptionSaved);
  const captionSummary = useSyncExternalStore(
    captions.subscribeSummary,
    captions.getSummary,
  );
  useEffect(() => {
    captions.sync(items);
  }, [captions, items]);
  const gallery = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const panelButtons = useRef<Record<string, HTMLButtonElement | null>>({});
  function closePanel() {
    if (panel) panelButtons.current[panel]?.focus();
    setPanel(null);
  }
  useEffect(() => {
    onEditorChange?.(screen === 'edit');
  }, [screen, onEditorChange]);
  const refresh = useCallback(async (id?: string) => {
    const list = await api<Dataset[]>('/api/datasets');
    const requested = performance.now();
    const contents = id
      ? await api<DatasetItem[]>(`/api/datasets/${id}/items`)
      : [];
    setDatasets(list);
    if (id) {
      setDataset(list.find((value) => value.id === id) ?? null);
      setItems(
        contents.map((item) => {
          const saved = savedCaptions.current.get(item.id);
          return saved && saved.at > requested
            ? { ...item, caption: saved.caption }
            : item;
        }),
      );
    }
    return list;
  }, []);
  useEffect(() => {
    void api<Dataset[]>('/api/datasets')
      .then(setDatasets)
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoaded(true));
  }, []);
  useEffect(() => {
    if (!gallery.current) return;
    const observer = new ResizeObserver((entries) => {
      setColumns(Math.max(1, Math.floor(entries[0].contentRect.width / 230)));
      setGalleryHeight(entries[0].contentRect.height);
    });
    observer.observe(gallery.current);
    return () => observer.disconnect();
  }, [dataset?.id, visible, screen]);
  useEffect(() => {
    if (visible) heading.current?.focus();
  }, [screen, dataset?.id, visible]);
  useEffect(() => {
    if (gallery.current) gallery.current.scrollTop = 0;
  }, [query, filter, sort, dataset?.id]);
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
    if (!visible) void captions.flushAll();
  }, [visible, captions]);
  useEffect(() => {
    if (
      !visible ||
      screen !== 'library' ||
      !datasets.some((value) =>
        ['pending', 'scanning'].includes(value.scan.state),
      )
    )
      return;
    const timer = window.setInterval(() => {
      void refresh().catch((reason: Error) => setError(reason.message));
    }, 700);
    return () => window.clearInterval(timer);
  }, [visible, screen, datasets, refresh]);
  async function select(item: DatasetItem | null) {
    if (!(await captions.flushAll())) return;
    setActive(item);
  }
  const followRoute = useEffectEvent(() => {
    if (!loaded || selectedId === dataset?.id) return;
    if (selectedId) void open(selectedId);
    else if (screen === 'edit') void back();
  });
  useEffect(() => followRoute(), [selectedId, loaded]);
  async function open(id: string) {
    if (!(await captions.flushAll())) {
      onSelect?.(dataset?.id, true);
      return;
    }
    setBusy(true);
    setActive(null);
    setSelected(new Set());
    setEditing(null);
    setQuery('');
    setFilter('all');
    setScroll(0);
    setError('');
    try {
      const list = await refresh(id);
      setName(list.find((value) => value.id === id)?.name ?? '');
      setScreen('edit');
      setPanel(null);
      setStatus('');
      onSelect?.(id);
    } catch (reason) {
      setError((reason as Error).message);
      onSelect?.(dataset?.id, true);
    } finally {
      setBusy(false);
    }
  }
  async function back() {
    if (!(await captions.flushAll())) {
      onSelect?.(dataset?.id, true);
      return;
    }
    setBusy(true);
    try {
      await refresh();
      setActive(null);
      setDataset(null);
      setEditing(null);
      setScreen('library');
      setError('');
      setStatus('');
      onSelect?.();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove(value: Dataset) {
    const confirmed = await confirmDialog({
      title: `Delete "${value.name}" from the dataset library?`,
      body: (
        <p>
          Only the library entry is removed. Media files, captions and recovery
          history stay on disk, including uploaded files. Jobs using this
          dataset will need a dataset selected again.
        </p>
      ),
      confirmLabel: 'Delete from library',
      danger: true,
    });
    if (!confirmed) return;
    setBusy(true);
    setError('');
    try {
      await api(`/api/datasets/${value.id}`, 'DELETE', {});
      await refresh();
      setStatus(
        `Deleted "${value.name}" from the library. Files are preserved.`,
      );
      heading.current?.focus();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function create(managed: boolean) {
    setBusy(true);
    setError('');
    try {
      const created = await api<Dataset>(
        managed ? '/api/datasets/managed' : '/api/datasets',
        'POST',
        managed ? { name, root: folder } : { name, path: folder },
      );
      await open(created.id);
      setPanel(managed ? 'uploads' : null);
      setStatus(
        managed
          ? 'Managed dataset created. Add media and matching captions to get started.'
          : 'Dataset registered. Source media stays in its original folder.',
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function rename() {
    if (!dataset) return;
    setBusy(true);
    setError('');
    try {
      setDataset(
        await api<Dataset>(`/api/datasets/${dataset.id}`, 'PUT', { name }),
      );
      setStatus('Dataset name saved.');
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function rescan() {
    if (!dataset) return;
    if (!(await captions.flushAll())) return;
    setBusy(true);
    try {
      await api(`/api/datasets/${dataset.id}/scan`, 'POST', {});
      await refresh(dataset.id);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const rowHeight = 370;
  const matchingItems = items
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
  // Keep the focused caption in place while autosave changes search/filter/sort results.
  const itemsById = new Map(items.map((item) => [item.id, item]));
  const visibleItems = editing
    ? editing.order.flatMap((id) => {
        const item = itemsById.get(id);
        return item ? [item] : [];
      })
    : matchingItems;
  const start = Math.max(0, Math.floor(scroll / rowHeight) - 1) * columns;
  const end = Math.min(
    visibleItems.length,
    start + columns * (Math.ceil(galleryHeight / rowHeight) + 2),
  );
  const renderedItems = visibleItems.slice(start, end);
  const focusedItem = visibleItems.find((item) => item.id === editing?.id);
  if (focusedItem && !renderedItems.includes(focusedItem))
    renderedItems.push(focusedItem);
  return (
    <div
      className={`datasets-screen ${screen === 'edit' ? 'editor-screen' : ''}`}
      onKeyDown={(event) => {
        if (screen === 'edit' && panel && event.key === 'Escape') {
          event.stopPropagation();
          closePanel();
        }
      }}
    >
      {screen !== 'edit' && (
        <>
          {screen !== 'library' && (
            <button
              className="dataset-back"
              disabled={busy}
              onClick={() => void back()}
            >
              ← Back to datasets
            </button>
          )}
          <section className="title dataset-heading">
            <div>
              <h1 ref={heading} tabIndex={-1}>
                {screen === 'library' ? 'Datasets' : 'New dataset'}
              </h1>
              <p>
                {screen === 'library'
                  ? 'A home for your training images, videos and captions.'
                  : 'Start with an existing folder or create a dataset for uploads.'}
              </p>
            </div>
            {screen === 'library' && (
              <button
                className="primary"
                disabled={busy}
                onClick={() => {
                  setName('');
                  setFolder(undefined);
                  setError('');
                  setStatus('');
                  setScreen('create');
                }}
              >
                + New dataset
              </button>
            )}
          </section>
        </>
      )}
      {screen === 'edit' && dataset && (
        <div className="dataset-topbar">
          <button
            className="dataset-back-icon"
            aria-label="Back to datasets"
            title="Back to datasets"
            disabled={busy}
            onClick={() => void back()}
          >
            ←
          </button>
          <div className="dataset-identity">
            <h1 ref={heading} tabIndex={-1} title={dataset.name}>
              {dataset.name}
            </h1>
            <small role="status">
              {captionSummary.failed ? (
                <button
                  className="caption-attention"
                  onClick={() => {
                    const failed = captions.firstFailed();
                    if (failed) setActive(itemsById.get(failed.id) ?? failed);
                  }}
                >
                  Review {captionSummary.failed} unsaved{' '}
                  {captionSummary.failed === 1 ? 'caption' : 'captions'}
                </button>
              ) : captionSummary.saving ? (
                'Saving captions…'
              ) : captionSummary.unsaved ? (
                'Unsaved captions'
              ) : (
                <>
                  Scan: {dataset.scan.state} {dataset.scan.completed}/
                  {dataset.scan.total} {dataset.scan.error}
                </>
              )}
            </small>
          </div>
          <div className="dataset-topbar-actions">
            {dataset.managed && (
              <button
                className="primary"
                ref={(element) => {
                  panelButtons.current.uploads = element;
                }}
                aria-expanded={panel === 'uploads'}
                aria-controls="dataset-uploads-panel"
                onClick={() => setPanel(panel === 'uploads' ? null : 'uploads')}
              >
                Add media
              </button>
            )}
            <button
              ref={(element) => {
                panelButtons.current.tools = element;
              }}
              className={
                query || filter !== 'all' || selected.size ? 'has-filters' : ''
              }
              aria-expanded={panel === 'tools'}
              aria-controls="dataset-tools-panel"
              onClick={() => setPanel(panel === 'tools' ? null : 'tools')}
            >
              Search &amp; edit
              {selected.size > 0
                ? ` · ${selected.size}`
                : query || filter !== 'all'
                  ? ' · filtered'
                  : ''}
            </button>
            <button
              ref={(element) => {
                panelButtons.current.settings = element;
              }}
              aria-label="Dataset settings"
              title="Dataset settings"
              aria-expanded={panel === 'settings'}
              aria-controls="dataset-settings-panel"
              onClick={() => setPanel(panel === 'settings' ? null : 'settings')}
            >
              ⚙
            </button>
          </div>
        </div>
      )}
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      {status && (
        <p role="status" className={screen === 'edit' ? 'dataset-notice' : ''}>
          {status}
        </p>
      )}
      {screen === 'library' && (
        <section
          className="dataset-library"
          aria-label="Dataset library"
          aria-busy={busy}
        >
          {!loaded && <p role="status">Loading datasets…</p>}
          {loaded && !datasets.length && (
            <div className="dataset-empty">
              <span aria-hidden="true">▧</span>
              <h2>Your library starts here</h2>
              <p>
                Choose New dataset to register a local folder or start uploading
                media.
              </p>
            </div>
          )}
          {datasets.map((value) => (
            <article className="dataset-library-row" key={value.id}>
              <button
                className="dataset-open"
                disabled={busy}
                onClick={() => void open(value.id)}
              >
                <strong>{value.name}</strong>
                <small>
                  {value.counts.total} media · {value.counts.captioned}{' '}
                  captioned · {value.counts.included} included
                </small>
                <span className="dataset-path">{value.path}</span>
                <span className="dataset-edit-label">Edit dataset →</span>
              </button>
              <div className="dataset-row-actions">
                <span
                  className={`dataset-kind ${value.missing ? 'missing' : ''}`}
                >
                  {value.missing
                    ? 'Folder missing'
                    : value.managed
                      ? 'Managed'
                      : 'Local folder'}
                </span>
                <button
                  className="dataset-delete"
                  aria-label={`Delete ${value.name}`}
                  disabled={
                    busy || ['pending', 'scanning'].includes(value.scan.state)
                  }
                  onClick={() => void remove(value)}
                >
                  Delete
                </button>
                {['pending', 'scanning'].includes(value.scan.state) && (
                  <small>Scan in progress; open to check.</small>
                )}
              </div>
            </article>
          ))}
        </section>
      )}
      {screen === 'create' && (
        <section className="panel dataset-create">
          <h2>Dataset settings</h2>
          <div className="columns">
            <label className="field">
              Dataset name
              <input
                maxLength={200}
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
          <p>
            Use a folder within the dataset roots configured in Settings.
            Register an existing folder to edit its media and captions in place,
            or create an isolated subfolder for uploads.
          </p>
          <div className="actions">
            <button
              className="primary"
              disabled={busy || !name.trim() || !folder.trim()}
              onClick={() => void create(true)}
            >
              Create a new folder for uploads
            </button>
            <button
              disabled={busy || !name.trim() || !folder.trim()}
              onClick={() => void create(false)}
            >
              Use existing folder
            </button>
          </div>
          {busy && <p role="status">Opening dataset…</p>}
        </section>
      )}
      {screen === 'edit' && dataset && (
        <section className="dataset-content">
          {dataset.missing && (
            <p role="alert" className="error dataset-missing">
              Dataset folder is missing. Restore its original location and
              re-scan.
            </p>
          )}
          <section
            id="dataset-settings-panel"
            className="dataset-panel"
            aria-label="Dataset settings"
            hidden={panel !== 'settings'}
          >
            <div className="dataset-panel-heading">
              <h2>Dataset settings</h2>
              <button aria-label="Close settings" onClick={closePanel}>
                ×
              </button>
            </div>
            <label className="field">
              Dataset name
              <input
                maxLength={200}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <button
              disabled={busy || !name.trim() || name.trim() === dataset.name}
              onClick={() => void rename()}
            >
              Save dataset name
            </button>
            <p className="dataset-path">{dataset.path}</p>
            <p>
              {dataset.managed
                ? 'Managed dataset · uploads are stored in this folder.'
                : 'Registered local folder · add media in this folder, then re-scan.'}
            </p>
            <H3Coverage key={dataset.id} datasetId={dataset.id} />
            <button disabled={busy} onClick={() => void rescan()}>
              Re-scan
            </button>
            <p>
              {dataset.counts.included} included · {dataset.counts.excluded}{' '}
              excluded · {dataset.counts.total - dataset.counts.valid} invalid
            </p>
          </section>
          {dataset.managed && (
            <section
              id="dataset-uploads-panel"
              className="dataset-panel"
              aria-label="Add media"
              hidden={panel !== 'uploads'}
            >
              <div className="dataset-panel-heading">
                <h2>Add media</h2>
                <button aria-label="Close uploads" onClick={closePanel}>
                  ×
                </button>
              </div>
              <DatasetUploads
                key={dataset.id}
                datasetId={dataset.id}
                beforeAction={async () => captions.flushAll()}
                onChanged={async () => {
                  setActive(null);
                  await refresh(dataset.id);
                }}
              />
            </section>
          )}
          <section
            id="dataset-tools-panel"
            className="dataset-panel"
            aria-label="Dataset tools"
            hidden={panel !== 'tools'}
          >
            <div className="dataset-panel-heading">
              <h2>Search and selection</h2>
              <button aria-label="Close tools" onClick={closePanel}>
                ×
              </button>
            </div>
            <div className="columns dataset-filters">
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
            <div className="dataset-selection">
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
            </div>
            <DatasetBulkActions
              key={dataset.id}
              datasetId={dataset.id}
              selected={[...selected]}
              beforeAction={async () => captions.flushAll()}
              onChanged={async () => {
                setActive(null);
                await refresh(dataset.id);
              }}
            />
          </section>
          <div className={`dataset-workspace ${active ? 'has-viewer' : ''}`}>
            <div className="dataset-gallery-area">
              {!visibleItems.length && (
                <div className="dataset-empty">
                  <span aria-hidden="true">▧</span>
                  <h2>
                    {items.length
                      ? 'No matching media'
                      : 'Ready for your media'}
                  </h2>
                  <p>
                    {items.length
                      ? 'Try a different search or filter to find your media.'
                      : dataset.managed
                        ? 'Open Add media to upload images, videos and matching captions.'
                        : 'Add images and videos to the dataset folder, then choose Re-scan.'}
                  </p>
                  {items.length > 0 && (
                    <button
                      onClick={() => {
                        setQuery('');
                        setFilter('all');
                        setScroll(0);
                      }}
                    >
                      Clear search and filters
                    </button>
                  )}
                </div>
              )}
              <div
                className="dataset-gallery"
                hidden={!visibleItems.length}
                ref={gallery}
                tabIndex={0}
                role="region"
                onScroll={(event) => setScroll(event.currentTarget.scrollTop)}
                onKeyDown={(event) => {
                  if (event.target !== event.currentTarget) return;
                  if (['ArrowDown', 'ArrowUp'].includes(event.key)) {
                    event.preventDefault();
                    event.currentTarget.scrollBy(
                      0,
                      event.key === 'ArrowDown' ? rowHeight : -rowHeight,
                    );
                  }
                }}
                aria-label="Dataset gallery"
              >
                <div
                  style={{
                    height:
                      Math.ceil(visibleItems.length / columns) * rowHeight,
                    position: 'relative',
                  }}
                >
                  {renderedItems.map((item) => {
                    const index = visibleItems.indexOf(item);
                    return (
                      <div
                        className={`media-card ${selected.has(item.id) ? 'picked' : ''} ${active?.id === item.id ? 'active' : ''}`}
                        key={item.id}
                        onBlur={(event) => {
                          if (
                            !event.currentTarget.contains(event.relatedTarget)
                          ) {
                            setEditing((value) =>
                              value?.id === item.id ? null : value,
                            );
                          }
                        }}
                        style={{
                          position: 'absolute',
                          top: Math.floor(index / columns) * rowHeight,
                          left: `${((index % columns) * 100) / columns}%`,
                          width: `${100 / columns}%`,
                          height: rowHeight - 8,
                        }}
                      >
                        <div className="media-card-content">
                          <button
                            className={`media-tile ${active?.id === item.id ? 'selected' : ''}`}
                            aria-label={`${item.name} ${item.kind} · ${item.caption.exists ? 'caption' : 'missing caption'}${item.excluded ? ' · excluded' : ''}`}
                            aria-pressed={active?.id === item.id}
                            onClick={() => void select(item)}
                          >
                            {item.valid ? (
                              <img
                                loading="lazy"
                                src={`/api/datasets/${dataset.id}/items/${item.id}/thumbnail`}
                                alt={item.name}
                              />
                            ) : (
                              <span className="invalid-media">
                                Cannot preview
                              </span>
                            )}
                            <span className="media-name" title={item.name}>
                              {item.name}
                            </span>
                            <small>
                              {item.kind}
                              {item.caption.exists
                                ? ' · caption'
                                : ' · missing caption'}
                              {item.excluded ? ' · excluded' : ''}
                            </small>
                          </button>
                          <InlineCaption
                            session={captions}
                            item={item}
                            onFocus={() =>
                              setEditing({
                                id: item.id,
                                order: visibleItems.map((value) => value.id),
                              })
                            }
                            onReview={() =>
                              setActive(
                                items.find((value) => value.id === item.id) ??
                                  item,
                              )
                            }
                          />
                        </div>
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
                <div className="actions viewer-heading">
                  <h2>{active.name}</h2>
                  <button onClick={() => void select(null)}>
                    Close preview
                  </button>
                </div>
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
                <CaptionEditor session={captions} item={active} />
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
    </div>
  );
}
