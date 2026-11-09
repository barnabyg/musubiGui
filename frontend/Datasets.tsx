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
import {
  type Caption,
  type CaptionSession,
  useCaptionSessions,
} from './captionSession';
import { DatasetCreate } from './DatasetCreate';
import { type CaptionEditing, DatasetGallery } from './DatasetGallery';
import { matchingItems } from './datasetItems';
import { DatasetLibrary } from './DatasetLibrary';
import { DatasetPanels } from './DatasetPanels';
import { type DatasetPanel, DatasetTopbar } from './DatasetTopbar';
import { MediaPreview } from './MediaPreview';
import { DatasetJobImpact, loadDatasetUsage } from './datasetUsage';
import { datasetItemRoute, type DatasetRepair, formatRoute } from './route';

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

async function loadDatasets(id?: string, signal?: AbortSignal) {
  const list = await api<Dataset[]>('/api/datasets', 'GET', undefined, signal);
  const requested = performance.now();
  const contents = id
    ? await api<DatasetItem[]>(
        `/api/datasets/${id}/items`,
        'GET',
        undefined,
        signal,
      )
    : [];
  return { list, contents, requested, id };
}

export function Datasets({
  visible = true,
  selectedId,
  repair,
  onSelect,
  defaultFolder = '',
  datasetRoots,
  onEditorChange,
  onCaptionSessions,
  onTrainingJob,
}: {
  visible?: boolean;
  selectedId?: string;
  repair?: DatasetRepair;
  onSelect?: (id?: string, replace?: boolean) => void;
  defaultFolder?: string;
  datasetRoots?: string[];
  onEditorChange?: (editing: boolean) => void;
  onCaptionSessions: (sessions: CaptionSession[]) => void;
  onTrainingJob: (id: string) => void;
}) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [screen, setScreen] = useState<'library' | 'create' | 'edit'>(
    'library',
  );
  const [working, setBusy] = useState(false);
  const [loadedRepair, setLoadedRepair] = useState<DatasetRepair>();
  const loadingRepair = Boolean(repair && loadedRepair !== repair);
  const busy = working || loadingRepair;
  const [loaded, setLoaded] = useState(false);
  const [panel, setPanel] = useState<DatasetPanel | null>(null);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [items, setItems] = useState<DatasetItem[]>([]);
  const [audioBusy, setAudioBusy] = useState(false);
  const [returning, setReturning] = useState(false);
  const [preview, setPreview] = useState<DatasetItem | null>(null);
  const active = repair
    ? (!loadingRepair && items.find((item) => item.id === repair.itemId)) ||
      null
    : preview;
  function setActive(item: DatasetItem | null) {
    if (repair && dataset) {
      window.location.hash = datasetItemRoute(
        dataset.id,
        item?.id,
        'preview',
        repair.jobId,
        repair.frames,
      );
    }
    setPreview(item);
  }
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
    setPreview((value) => (value?.id === id ? { ...value, caption } : value));
    setItems((values) =>
      values.map((item) => (item.id === id ? { ...item, caption } : item)),
    );
  }, []);
  const sessions = useCaptionSessions(dataset?.id ?? '', onCaptionSaved);
  const captions = sessions[0];
  useEffect(() => {
    onCaptionSessions(sessions);
  }, [onCaptionSessions, sessions]);
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
  const currentSelection = useRef(selectedId);
  useEffect(() => {
    currentSelection.current = selectedId;
  }, [selectedId]);
  const applyDatasets = useCallback(
    ({
      list,
      contents,
      requested,
      id,
    }: Awaited<ReturnType<typeof loadDatasets>>) => {
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
    },
    [],
  );
  const refresh = useCallback(
    async (id?: string, signal?: AbortSignal) => {
      const data = await loadDatasets(id, signal);
      signal?.throwIfAborted();
      applyDatasets(data);
      return data.list;
    },
    [applyDatasets],
  );
  useEffect(() => {
    void api<Dataset[]>('/api/datasets')
      .then(setDatasets)
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoaded(true));
  }, []);
  useEffect(() => {
    if (visible && !repair?.itemId) heading.current?.focus();
  }, [screen, dataset?.id, visible, repair?.itemId]);
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
    const origin = window.location.hash;
    if (!(await captions.flushAll()) || window.location.hash !== origin) return;
    setActive(item);
  }
  const followRoute = useEffectEvent((signal: AbortSignal) => {
    if (!loaded || repair || selectedId === dataset?.id) return;
    if (selectedId) void open(selectedId, true, signal);
    else if (screen === 'edit') void back();
  });
  useEffect(() => {
    const request = new AbortController();
    followRoute(request.signal);
    return () => request.abort();
  }, [selectedId, loaded, repair]);
  const showRepair = useEffectEvent(
    (id: string, data: Awaited<ReturnType<typeof loadDatasets>>) => {
      applyDatasets(data);
      if (id !== dataset?.id) showDataset(id, data.list);
      setError('');
    },
  );
  useEffect(() => {
    if (!loaded || !repair || !selectedId) return;
    const request = new AbortController();
    void loadDatasets(selectedId, request.signal)
      .then((data) => {
        if (!request.signal.aborted) showRepair(selectedId, data);
      })
      .catch((reason: Error) => {
        if (!request.signal.aborted) setError(reason.message);
      })
      .finally(() => {
        if (!request.signal.aborted) setLoadedRepair(repair);
      });
    return () => request.abort();
  }, [selectedId, loaded, repair]);
  async function returnToJob() {
    if (!repair) return;
    const origin = window.location.hash;
    setReturning(true);
    try {
      const saved = await captions.flushAll();
      if (window.location.hash !== origin) return;
      if (!saved) {
        reviewFailed();
        return;
      }
      window.location.hash = formatRoute({
        screen: 'Jobs',
        id: repair.jobId,
        review: true,
      });
    } finally {
      setReturning(false);
    }
  }
  async function open(id: string, fromRoute = false, signal?: AbortSignal) {
    if (!(await captions.flushAll())) {
      if (!signal?.aborted) onSelect?.(dataset?.id, true);
      return;
    }
    if (signal?.aborted) return;
    setBusy(true);
    try {
      const list = await refresh(id, signal);
      showDataset(id, list);
      setError('');
      if (!fromRoute) onSelect?.(id);
    } catch (reason) {
      if (signal?.aborted) return;
      setError((reason as Error).message);
      if (!repair) onSelect?.(dataset?.id, true);
    } finally {
      setBusy(false);
    }
  }
  function showDataset(id: string, list: Dataset[]) {
    setPreview(null);
    setSelected(new Set());
    setEditing(null);
    setQuery('');
    setFilter('all');
    setScroll(0);
    setName(list.find((value) => value.id === id)?.name ?? '');
    setScreen('edit');
    setPanel(null);
    setStatus('');
  }
  async function back() {
    if (!(await captions.flushAll())) {
      onSelect?.(dataset?.id, true);
      return;
    }
    setBusy(true);
    try {
      await refresh();
      setPreview(null);
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
    setBusy(true);
    setError('');
    try {
      const jobs = await loadDatasetUsage(value.id);
      const confirmed = await confirmDialog({
        title: `Remove "${value.name}" from the dataset library?`,
        body: (
          <>
            <p>
              Only the library entry is removed. Media files, captions and
              recovery history stay on disk, including uploaded files. The
              drafts below will need another dataset selected.
            </p>
            <DatasetJobImpact jobs={jobs} newTab />
          </>
        ),
        confirmLabel: 'Remove from library',
        danger: true,
      });
      if (!confirmed) return;
      await api(`/api/datasets/${value.id}`, 'DELETE', {});
      await refresh();
      setStatus(
        `Removed "${value.name}" from the library. Files are preserved.`,
      );
      heading.current?.focus();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function create(managed: boolean, destination: string) {
    setBusy(true);
    setError('');
    try {
      const created = await api<Dataset>(
        managed ? '/api/datasets/managed' : '/api/datasets',
        'POST',
        managed ? { name, root: destination } : { name, path: destination },
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
    setBusy(true);
    try {
      if (!(await captions.flushAll())) return;
      await api(
        `/api/datasets/${dataset.id}/scan${repair ? '?wait=true' : ''}`,
        'POST',
        {},
      );
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
    const failed = captions.firstFailed()?.item;
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
      {repair && (
        <div className="dataset-repair">
          <button
            disabled={
              busy ||
              audioBusy ||
              returning ||
              ['pending', 'scanning'].includes(dataset?.scan.state ?? '')
            }
            onClick={() => void returnToJob()}
          >
            <ArrowLeft /> Return to job and review
          </button>
          <span>
            {returning
              ? 'Waiting for dataset changes…'
              : 'Caption edits, audio sources and exclusions apply to every job using this dataset.'}
          </span>
        </div>
      )}
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
          onTrainingJob={onTrainingJob}
        />
      )}
      {repair?.itemId &&
        dataset?.id === selectedId &&
        !busy &&
        !error &&
        !active &&
        screen === 'edit' && (
          <p role="alert">
            The reviewed item is no longer in this dataset. Return to the job to
            review its current inputs.
          </p>
        )}
      {loadingRepair && <p role="status">Loading reviewed dataset…</p>}
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
          datasets={datasets}
          create={create}
        />
      )}
      {screen === 'edit' && dataset && (
        <section className="dataset-content" inert={returning || loadingRepair}>
          {dataset.missing && (
            <p role="alert" className="error dataset-missing">
              Dataset folder is missing. Restore its original location and
              re-scan.
            </p>
          )}
          <DatasetPanels
            visible={visible}
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
            onChanged={async () => {
              // A background repair may finish after navigation. Keep the current URL.
              if (currentSelection.current === dataset.id) {
                setPreview(null);
                const data = await loadDatasets(dataset.id);
                if (currentSelection.current === dataset.id)
                  applyDatasets(data);
              }
            }}
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
                repairView={
                  repair?.itemId === active.id ? repair.view : undefined
                }
                frames={repair?.frames}
                onAudioBusy={setAudioBusy}
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
