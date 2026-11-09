import { useEffect, useRef } from 'react';
import type { ItemView } from './route';
import { CaptionEditor } from './CaptionEditor';
import type { CaptionSession } from './captionSession';
import type { Dataset, DatasetItem } from './Datasets';
import { H3Inspector } from './H3Inspector';

export function MediaPreview({
  dataset,
  active,
  visibleItems,
  select,
  captions,
  repairView,
  frames,
  onAudioBusy,
}: {
  dataset: Dataset;
  active: DatasetItem;
  visibleItems: DatasetItem[];
  select: (item: DatasetItem | null) => Promise<boolean>;
  captions: CaptionSession;
  repairView?: ItemView;
  frames?: number;
  onAudioBusy: (busy: boolean) => void;
}) {
  const viewer = useRef<HTMLDivElement>(null);
  function focusCaption() {
    const caption = viewer.current?.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="Caption"]:not(:disabled)',
    );
    (caption ?? viewer.current)?.focus();
  }
  async function navigate(item: DatasetItem | null) {
    if (!(await select(item))) focusCaption();
  }
  useEffect(() => {
    if (repairView === 'preview') viewer.current?.focus();
    else if (repairView !== 'audio') focusCaption();
  }, [active.id, repairView]);
  return (
    <div
      ref={viewer}
      className="media-viewer"
      role="region"
      aria-label={`Media preview: ${active.name}`}
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.altKey && ['ArrowLeft', 'ArrowRight'].includes(event.key)) {
          event.preventDefault();
          const index =
            visibleItems.findIndex((item) => item.id === active.id) +
            (event.key === 'ArrowRight' ? 1 : -1);
          if (visibleItems[index]) void navigate(visibleItems[index]);
        }
      }}
    >
      <div className="actions viewer-heading">
        <h2>{active.name}</h2>
        <button className="quiet" onClick={() => void navigate(null)}>
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
          aria-keyshortcuts="Alt+ArrowLeft"
          disabled={
            visibleItems.findIndex((item) => item.id === active.id) <= 0
          }
          onClick={() =>
            void navigate(
              visibleItems[
                visibleItems.findIndex((item) => item.id === active.id) - 1
              ],
            )
          }
        >
          Previous
        </button>
        <button
          aria-keyshortcuts="Alt+ArrowRight"
          disabled={
            visibleItems.findIndex((item) => item.id === active.id) >=
            visibleItems.length - 1
          }
          onClick={() =>
            void navigate(
              visibleItems[
                visibleItems.findIndex((item) => item.id === active.id) + 1
              ],
            )
          }
        >
          Next
        </button>
      </div>
      <p className="viewer-shortcuts">
        Alt+Left / Alt+Right: previous / next caption
      </p>
      <CaptionEditor
        session={captions}
        item={active}
        focus={repairView === 'caption'}
      />
      {active.kind === 'video' && (
        <H3Inspector
          key={frames}
          frames={frames}
          datasetId={dataset.id}
          itemId={active.id}
          open={repairView === 'audio'}
          onBusy={onAudioBusy}
        />
      )}
      <details>
        <summary>Media metadata</summary>
        <pre>{JSON.stringify(active.metadata, null, 2)}</pre>
      </details>
    </div>
  );
}
