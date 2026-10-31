import {
  useEffect,
  useEffectEvent,
  useLayoutEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react';
import { Images } from 'lucide-react';
import type { CaptionSession } from './captionSession';
import { hasCaption } from './datasetItems';
import type { Dataset, DatasetItem } from './Datasets';
import { InlineCaption } from './InlineCaption';
import { StatusBadge } from './StatusBadge';

const rowHeight = 370;

// A panel beside the gallery narrows the tiles but leaves the column count alone, so they keep
// their rows and columns. It takes width from the preview too, so with both open the count comes
// from the width the gallery would have without the panel, using the layout values
// `.dataset-workspace` sets in datasets.css. On narrow windows the panel overlays.
function galleryColumns(gallery: HTMLElement, width: number) {
  const screen = gallery.closest('.editor-screen');
  const panel = screen?.querySelector<HTMLElement>(
    '.dataset-panel:not([hidden])',
  );
  let available = width;
  if (panel && getComputedStyle(panel).position !== 'absolute') {
    const workspace = gallery.closest('.dataset-workspace') as HTMLElement;
    const style = getComputedStyle(workspace);
    const inner =
      workspace.clientWidth -
      parseFloat(style.paddingLeft) -
      parseFloat(style.paddingRight) +
      panel.offsetWidth;
    const viewer = workspace.querySelector('.media-viewer');
    const preview = Math.max(
      parseFloat(style.getPropertyValue('--preview-min')),
      (inner * parseFloat(style.getPropertyValue('--preview-share'))) / 100,
    );
    available = viewer ? inner - preview - parseFloat(style.columnGap) : inner;
  }
  return Math.max(1, Math.floor(available / 230));
}

export type CaptionEditing = { id: string; order: string[] };

export function DatasetGallery({
  dataset,
  items,
  visibleItems,
  visible,
  query,
  setQuery,
  filter,
  setFilter,
  sort,
  scroll,
  setScroll,
  active,
  setActive,
  select,
  selected,
  setSelected,
  editing,
  setEditing,
  captions,
}: {
  dataset: Dataset;
  items: DatasetItem[];
  visibleItems: DatasetItem[];
  visible: boolean;
  query: string;
  setQuery: (value: string) => void;
  filter: string;
  setFilter: (value: string) => void;
  sort: string;
  scroll: number;
  setScroll: (value: number) => void;
  active: DatasetItem | null;
  setActive: (item: DatasetItem | null) => void;
  select: (item: DatasetItem | null) => Promise<void>;
  selected: Set<string>;
  setSelected: Dispatch<SetStateAction<Set<string>>>;
  editing: CaptionEditing | null;
  setEditing: Dispatch<SetStateAction<CaptionEditing | null>>;
  captions: CaptionSession;
}) {
  const [columns, setColumns] = useState(3);
  const [galleryHeight, setGalleryHeight] = useState(640);
  const gallery = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!gallery.current) return;
    const element = gallery.current;
    const observer = new ResizeObserver((entries) => {
      setColumns(galleryColumns(element, entries[0].contentRect.width));
      setGalleryHeight(entries[0].contentRect.height);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [dataset.id, visible]);
  useEffect(() => {
    if (gallery.current) gallery.current.scrollTop = 0;
  }, [query, filter, sort, dataset.id]);
  const start = Math.max(0, Math.floor(scroll / rowHeight) - 1) * columns;
  const end = Math.min(
    visibleItems.length,
    start + columns * (Math.ceil(galleryHeight / rowHeight) + 2),
  );
  const renderedItems = visibleItems.slice(start, end);
  // The preview takes width and changes the column count, which moves every tile. The tile being
  // worked on stays where it was on screen if possible, and in view whatever happens.
  const keptTile = useRef<{ id?: string; columns: number }>({ columns });
  const keepActiveTile = useEffectEvent(() => {
    const element = gallery.current;
    const previous = keptTile.current;
    const id = active?.id ?? previous.id;
    // A closed preview's tile is only kept through the reflow its closing causes.
    keptTile.current = {
      id: previous.columns !== columns && !active ? undefined : id,
      columns,
    };
    const index = visibleItems.findIndex((item) => item.id === id);
    if (!element || index < 0) return;
    const top = Math.floor(index / columns) * rowHeight;
    let target = element.scrollTop;
    if (previous.columns !== columns && previous.id === id)
      target =
        top -
        (Math.floor(index / previous.columns) * rowHeight - element.scrollTop);
    if (target + element.clientHeight < top + rowHeight)
      target = top + rowHeight - element.clientHeight;
    if (target > top) target = top;
    if (target !== element.scrollTop) element.scrollTop = target;
  });
  useLayoutEffect(() => keepActiveTile(), [columns, active?.id]);
  const focusedItem = visibleItems.find((item) => item.id === editing?.id);
  if (focusedItem && !renderedItems.includes(focusedItem))
    renderedItems.push(focusedItem);
  return (
    <div className="dataset-gallery-area">
      {!visibleItems.length && (
        <div className="dataset-empty">
          <Images />
          <h2>{items.length ? 'No matching media' : 'Ready for your media'}</h2>
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
            height: Math.ceil(visibleItems.length / columns) * rowHeight,
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
                  if (!event.currentTarget.contains(event.relatedTarget)) {
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
                    aria-label={`${item.name} ${item.kind} · ${hasCaption(item) ? 'caption' : 'missing caption'}${item.excluded ? ' · excluded' : ''}${item.valid ? '' : ' · invalid'}`}
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
                      <span className="invalid-media">Cannot preview</span>
                    )}
                    <span className="media-name" title={item.name}>
                      {item.name}
                    </span>
                    <small>{item.kind}</small>
                    <span className="media-badges">
                      {!hasCaption(item) && (
                        <StatusBadge tone="warning">
                          Missing caption
                        </StatusBadge>
                      )}
                      {item.excluded && (
                        <StatusBadge tone="neutral">Excluded</StatusBadge>
                      )}
                      {!item.valid && (
                        <StatusBadge tone="failure">Invalid</StatusBadge>
                      )}
                    </span>
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
                        items.find((value) => value.id === item.id) ?? item,
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
  );
}
