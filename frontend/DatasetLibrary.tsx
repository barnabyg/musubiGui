import { ArrowRight, Images } from 'lucide-react';
import type { Dataset } from './Datasets';
import { contentsLabel } from './labels';

export function DatasetLibrary({
  busy,
  loaded,
  datasets,
  open,
  remove,
}: {
  busy: boolean;
  loaded: boolean;
  datasets: Dataset[];
  open: (id: string) => Promise<void>;
  remove: (value: Dataset) => Promise<void>;
}) {
  return (
    <section
      className="dataset-library"
      aria-label="Dataset library"
      aria-busy={busy}
    >
      {!loaded && <p role="status">Loading datasets…</p>}
      {loaded && !datasets.length && (
        <div className="dataset-empty">
          <Images />
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
            <small className="dataset-contents">
              {contentsLabel(value.counts)}
            </small>
            <small>
              {value.counts.total} media · {value.counts.captioned} captioned ·{' '}
              {value.counts.included} included
            </small>
            <span className="dataset-path">{value.path}</span>
            <span className="dataset-edit-label">
              Edit dataset <ArrowRight />
            </span>
          </button>
          <div className="dataset-row-actions">
            <span className={`dataset-kind ${value.missing ? 'missing' : ''}`}>
              {value.missing
                ? 'Folder missing'
                : value.managed
                  ? 'Managed'
                  : 'Local folder'}
            </span>
            <button
              className="danger"
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
  );
}
