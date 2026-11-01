import {
  useCallback,
  useEffect,
  useRef,
  useEffectEvent,
  useState,
} from 'react';
import { ArrowLeft } from 'lucide-react';
import './datasets.css';
import { apiClient } from './api';
import { confirmDialog } from './ConfirmDialog';
import { type Caption, useCaptionSession } from './captionSession';
import { DatasetCreate } from './DatasetCreate';
import { type CaptionEditing, DatasetGallery } from './DatasetGallery';
import { matchingItems } from './datasetItems';
import { DatasetLibrary } from './DatasetLibrary';
import { DatasetPanels } from './DatasetPanels';
import { type DatasetPanel, DatasetTopbar } from './DatasetTopbar';
import { MediaPreview } from './MediaPreview';

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
    images: number;
    videos: number;
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
  datasetRoots,
  onEditorChange,
}: {
  visible?: boolean;
  selectedId?: string;
  onSelect?: (id?: string, replace?: boolean) => void;
  defaultFolder?: string;
  datasetRoots?: string[];
  onEditorChange?: (editing: boolean) => void;
}) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [screen, setScreen] = useState<'library' | 'create' | 'edit'>(
    'library',
  );
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [panel, setPanel] = useState<DatasetPanel | null>(null);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [items, setItems] = useState<DatasetItem[]>([]);
  const [active, setActive] = useState<DatasetItem | null>(null);
  const [name, setName] = useState('');
  const [editedFolder, setFolder] = useState<string>();
  const folder = editedFolder ?? defaultFolder;
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [scroll, setScroll] = useState(0);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState('name');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<CaptionEditing | null>(null);
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
  useEffect(() => {
    captions.sync(items);
  }, [captions, items]);
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
    if (visible) heading.current?.focus();
  }, [screen, dataset?.id, visible]);
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
          ? 'Upload folder created. Add media and matching captions to get started.'
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
  // Keep the focused caption in place while autosave changes search/filter/sort results.
  const itemsById = new Map(items.map((item) => [item.id, item]));
  const visibleItems = editing
    ? editing.order.flatMap((id) => {
        const item = itemsById.get(id);
        return item ? [item] : [];
      })
    : matchingItems(items, query, filter, sort, selected);
  function reviewFailed() {
    const failed = captions.firstFailed();
    if (failed) setActive(itemsById.get(failed.id) ?? failed);
  }
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
              className="dataset-back quiet"
              disabled={busy}
              onClick={() => void back()}
            >
              <ArrowLeft /> Back to datasets
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
        <DatasetTopbar
          dataset={dataset}
          items={items}
          busy={busy}
          heading={heading}
          back={back}
          captions={captions}
          reviewFailed={reviewFailed}
          query={query}
          setQuery={setQuery}
          filter={filter}
          setFilter={setFilter}
          sort={sort}
          setSort={setSort}
          setScroll={setScroll}
          panel={panel}
          setPanel={setPanel}
          setPanelButton={(name, element) => {
            panelButtons.current[name] = element;
          }}
          selected={selected}
        />
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
        <DatasetLibrary
          busy={busy}
          loaded={loaded}
          datasets={datasets}
          open={open}
          remove={remove}
        />
      )}
      {screen === 'create' && (
        <DatasetCreate
          busy={busy}
          name={name}
          setName={setName}
          folder={folder}
          setFolder={setFolder}
          datasetRoots={datasetRoots}
          create={create}
        />
      )}
      {screen === 'edit' && dataset && (
        <section className="dataset-content">
          {dataset.missing && (
            <p role="alert" className="error dataset-missing">
              Dataset folder is missing. Restore its original location and
              re-scan.
            </p>
          )}
          <DatasetPanels
            dataset={dataset}
            panel={panel}
            closePanel={closePanel}
            busy={busy}
            name={name}
            setName={setName}
            rename={rename}
            rescan={rescan}
            captions={captions}
            visibleItems={visibleItems}
            selected={selected}
            setSelected={setSelected}
            setActive={setActive}
            refresh={refresh}
          />
          <div className={`dataset-workspace ${active ? 'has-viewer' : ''}`}>
            <DatasetGallery
              dataset={dataset}
              items={items}
              visibleItems={visibleItems}
              visible={visible}
              query={query}
              setQuery={setQuery}
              filter={filter}
              setFilter={setFilter}
              sort={sort}
              scroll={scroll}
              setScroll={setScroll}
              active={active}
              setActive={setActive}
              select={select}
              selected={selected}
              setSelected={setSelected}
              editing={editing}
              setEditing={setEditing}
              captions={captions}
            />
            {active && (
              <MediaPreview
                key={active.id}
                dataset={dataset}
                active={active}
                visibleItems={visibleItems}
                select={select}
                captions={captions}
              />
            )}
          </div>
        </section>
      )}
    </div>
  );
}
