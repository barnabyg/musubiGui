import type { Dispatch, SetStateAction } from 'react';
import { X } from 'lucide-react';
import type { CaptionSession } from './captionSession';
import { DatasetBulkActions } from './DatasetBulkActions';
import type { Dataset, DatasetItem } from './Datasets';
import type { DatasetPanel } from './DatasetTopbar';
import { DatasetUploads } from './DatasetUploads';
import { H3Coverage } from './H3Inspector';
import { H3Preparation } from './H3Preparation';
import { DatasetJobImpact, useDatasetUsage } from './datasetUsage';

export function DatasetPanels({
  dataset,
  visible,
  panel,
  closePanel,
  busy,
  name,
  setName,
  rename,
  rescan,
  captions,
  visibleItems,
  selected,
  setSelected,
  onChanged,
  roots,
}: {
  dataset: Dataset;
  visible: boolean;
  panel: DatasetPanel | null;
  closePanel: () => void;
  busy: boolean;
  name: string;
  setName: (value: string) => void;
  rename: () => Promise<void>;
  rescan: () => Promise<void>;
  captions: CaptionSession;
  visibleItems: DatasetItem[];
  selected: Set<string>;
  setSelected: Dispatch<SetStateAction<Set<string>>>;
  onChanged: () => Promise<void>;
  roots: string[];
}) {
  const usage = useDatasetUsage(
    dataset.id,
    visible && (panel === 'settings' || panel === 'tools'),
  );
  return (
    <>
      <section
        id="dataset-settings-panel"
        className="dataset-panel"
        aria-label="Dataset settings"
        hidden={panel !== 'settings'}
      >
        <div className="dataset-panel-heading">
          <h2>Dataset settings</h2>
          <button
            className="quiet"
            aria-label="Close settings"
            onClick={closePanel}
          >
            <X />
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
            ? 'Upload folder · uploads are stored in this folder.'
            : 'Registered local folder · add media in this folder, then re-scan.'}
        </p>
        <H3Coverage key={dataset.id} datasetId={dataset.id} />
        {visible && panel === 'settings' && (
          <H3Preparation
            key={`prepare-${dataset.id}`}
            dataset={dataset}
            roots={roots}
            afterCaptionFlush={(action) => captions.afterCaptionFlush(action)}
          />
        )}
        {panel === 'settings' && <DatasetJobImpact {...usage} />}
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
            <button
              className="quiet"
              aria-label="Close uploads"
              onClick={closePanel}
            >
              <X />
            </button>
          </div>
          <DatasetUploads
            key={dataset.id}
            datasetId={dataset.id}
            afterCaptionFlush={(action) => captions.afterCaptionFlush(action)}
            onChanged={onChanged}
          />
        </section>
      )}
      <section
        id="dataset-tools-panel"
        className="dataset-panel"
        aria-label="Bulk actions"
        hidden={panel !== 'tools'}
      >
        <div className="dataset-panel-heading">
          <h2>Bulk actions</h2>
          <button
            className="quiet"
            aria-label="Close bulk actions"
            onClick={closePanel}
          >
            <X />
          </button>
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
        {panel === 'tools' && <DatasetJobImpact {...usage} />}
        <DatasetBulkActions
          key={dataset.id}
          datasetId={dataset.id}
          selected={[...selected]}
          afterCaptionFlush={(action) => captions.afterCaptionFlush(action)}
          onChanged={onChanged}
          impactReady={!!usage.jobs && !usage.error}
        />
      </section>
    </>
  );
}
