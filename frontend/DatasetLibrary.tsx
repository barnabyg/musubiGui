import { ArrowRight, Images } from 'lucide-react';
import type { Dataset } from './Datasets';
import { contentsLabel, plural } from './labels';

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
  open: (id: string, filter?: 'invalid' | 'missing') => Promise<void>;
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
      {datasets.map((value) => {
        const scanning = ['pending', 'scanning'].includes(value.scan.state);
        const countsReady = !value.missing && value.scan.state === 'complete';
        const { total, valid, captioned, included } = value.counts;
        const invalid = total - valid;
        const missingCaptions = total - captioned;
        return (
          <article className="dataset-library-row" key={value.id}>
            <button
              className="dataset-open"
              disabled={busy}
              onClick={() => void open(value.id)}
            >
              <strong>
                {value.name} <ArrowRight />
              </strong>
              {countsReady && (
                <>
                  <small>
                    {plural(total, 'media file')} · {captioned} captioned
                  </small>
                  <small>
                    {included > 0 && `${included} included · `}
                    <span className="dataset-contents">
                      {contentsLabel(value.counts)}
                    </span>
                  </small>
                </>
              )}
            </button>
            <div className="dataset-row-actions">
              {value.missing && (
                <strong className="dataset-problem">Folder missing</strong>
              )}
              {scanning && <small>Scan in progress</small>}
              {!scanning && !value.missing && !countsReady && (
                <strong className="dataset-problem">
                  Scan incomplete · re-scan in Dataset settings
                </strong>
              )}
              {countsReady && invalid > 0 && (
                <button
                  className="dataset-problem"
                  disabled={busy}
                  onClick={() => void open(value.id, 'invalid')}
                >
                  {plural(invalid, 'invalid media file')} · skipped
                </button>
              )}
              {countsReady && missingCaptions > 0 && (
                <button
                  title="Review captions across all scanned files, including invalid or excluded media"
                  disabled={busy}
                  onClick={() => void open(value.id, 'missing')}
                >
                  {plural(missingCaptions, 'missing caption')}
                </button>
              )}
            </div>
            <details className="dataset-row-details">
              <summary aria-label={`Dataset details for ${value.name}`}>
                Details
              </summary>
              <small>
                {value.managed
                  ? 'Managed upload folder'
                  : 'Registered local folder'}
              </small>
              <span className="dataset-path">{value.path}</span>
              <button
                className="danger"
                aria-label={`Remove ${value.name} from library`}
                disabled={busy || scanning}
                onClick={() => void remove(value)}
              >
                Remove from library
              </button>
            </details>
          </article>
        );
      })}
    </section>
  );
}
