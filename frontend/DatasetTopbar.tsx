import { useSyncExternalStore, type RefObject } from 'react';
import { ArrowLeft, Settings as SettingsIcon } from 'lucide-react';
import type { CaptionSession } from './captionSession';
import { hasCaption } from './datasetItems';
import type { Dataset, DatasetItem } from './Datasets';

export type DatasetPanel = 'settings' | 'tools' | 'uploads';

export function DatasetTopbar({
  dataset,
  items,
  busy,
  heading,
  back,
  captions,
  reviewFailed,
  query,
  setQuery,
  filter,
  setFilter,
  sort,
  setSort,
  setScroll,
  panel,
  setPanel,
  setPanelButton,
  selected,
}: {
  dataset: Dataset;
  items: DatasetItem[];
  busy: boolean;
  heading: RefObject<HTMLHeadingElement | null>;
  back: () => Promise<void>;
  captions: CaptionSession;
  reviewFailed: () => void;
  query: string;
  setQuery: (value: string) => void;
  filter: string;
  setFilter: (value: string) => void;
  sort: string;
  setSort: (value: string) => void;
  setScroll: (value: number) => void;
  panel: DatasetPanel | null;
  setPanel: (value: DatasetPanel | null) => void;
  setPanelButton: (
    name: DatasetPanel,
    element: HTMLButtonElement | null,
  ) => void;
  selected: Set<string>;
}) {
  const captionSummary = useSyncExternalStore(
    captions.subscribeSummary,
    captions.getSummary,
  );
  return (
    <div className="dataset-topbar">
      <button
        className="dataset-back-icon quiet"
        aria-label="Back to datasets"
        title="Back to datasets"
        disabled={busy}
        onClick={() => void back()}
      >
        <ArrowLeft />
      </button>
      <div className="dataset-identity">
        <h1 ref={heading} tabIndex={-1} title={dataset.name}>
          {dataset.name}
        </h1>
        <small role="status">
          {captionSummary.failed ? (
            <button className="caption-attention" onClick={reviewFailed}>
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
      <div className="dataset-filters">
        <input
          aria-label="Search media and captions"
          placeholder="Search media and captions"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setScroll(0);
          }}
        />
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
        <button
          className="quiet"
          aria-pressed={filter === 'missing'}
          title="Show only media with no caption"
          onClick={() => {
            setFilter(filter === 'missing' ? 'all' : 'missing');
            setScroll(0);
          }}
        >
          {items.filter(hasCaption).length} of {items.length} captioned
        </button>
      </div>
      <div className="dataset-topbar-actions">
        {dataset.managed && (
          <button
            className={items.length ? '' : 'primary'}
            ref={(element) => {
              setPanelButton('uploads', element);
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
            setPanelButton('tools', element);
          }}
          className={selected.size ? 'has-filters' : ''}
          aria-expanded={panel === 'tools'}
          aria-controls="dataset-tools-panel"
          onClick={() => setPanel(panel === 'tools' ? null : 'tools')}
        >
          Bulk actions
          {selected.size > 0 ? ` · ${selected.size}` : ''}
        </button>
        <button
          ref={(element) => {
            setPanelButton('settings', element);
          }}
          aria-label="Dataset settings"
          title="Dataset settings"
          aria-expanded={panel === 'settings'}
          aria-controls="dataset-settings-panel"
          onClick={() => setPanel(panel === 'settings' ? null : 'settings')}
        >
          <SettingsIcon />
        </button>
      </div>
    </div>
  );
}
