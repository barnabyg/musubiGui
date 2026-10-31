import { useState } from 'react';
import { apiClient } from './api';

const readCoverage = apiClient('Coverage inspection failed.');
const requestInspection = apiClient(
  'Media inspection failed. Source files are unchanged.',
);

type Inspection = {
  source: string;
  audio_path: string | null;
  errors: string[];
  warnings: string[];
  coverage: number;
  metadata: Record<string, unknown>;
  audio_metadata?: Record<string, unknown>;
  offset_seconds?: number;
};

export function H3Coverage({ datasetId }: { datasetId: string }) {
  const [report, setReport] = useState<{
    included: number;
    excluded: number;
    invalid: number;
    supervised: number;
    coverage: number;
    items: (Inspection & { id: string; name: string })[];
  } | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function inspect() {
    setBusy(true);
    setError('');
    try {
      setReport(
        await readCoverage<typeof report>(`/api/datasets/${datasetId}/h3`),
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details>
      <summary>H3 dataset audio coverage</summary>
      <button disabled={busy} onClick={() => void inspect()}>
        {busy ? 'Inspecting coverage…' : 'Inspect H3 coverage'}
      </button>
      {error && <p role="alert">{error}</p>}
      {report && (
        <div>
          <p>
            {report.supervised} of {report.included} included items have valid
            supervised audio ({(report.coverage * 100).toFixed(1)}%).{' '}
            {report.excluded} excluded · {report.invalid} invalid media.
          </p>
          {report.items.map((item) => (
            <p key={item.id}>
              {item.name}: {item.source} · {(item.coverage * 100).toFixed(1)}%
              window coverage
              {item.errors.length ? ` · ${item.errors.join(' ')}` : ''}
            </p>
          ))}
        </div>
      )}
    </details>
  );
}

export function H3Inspector({
  datasetId,
  itemId,
}: {
  datasetId: string;
  itemId: string;
}) {
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const [path, setPath] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function inspect(save: boolean) {
    setBusy(true);
    setError('');
    try {
      const item = `/api/datasets/${datasetId}/items/${itemId}`;
      const value = await (save
        ? requestInspection<Inspection>(`${item}/audio`, 'PUT', { path })
        : requestInspection<Inspection>(`${item}/h3`));
      setInspection(value);
      setPath(value.source === 'explicit' ? (value.audio_path ?? '') : '');
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details>
      <summary>H3 audio and media inspector</summary>
      <p>
        Inspect the selected 124-frame, 24 fps T2VA window. Explicit audio takes
        precedence over one same-stem sidecar and then the embedded track.
      </p>
      <button disabled={busy} onClick={() => void inspect(false)}>
        {busy ? 'Inspecting…' : 'Inspect H3 media'}
      </button>
      <label className="field">
        Explicit audio path
        <input
          value={path}
          onChange={(event) => setPath(event.target.value)}
          placeholder="Leave empty for automatic resolution"
        />
      </label>
      <button disabled={busy} onClick={() => void inspect(true)}>
        Apply audio source
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {inspection && (
        <div aria-label="H3 inspection result">
          <p>
            Audio source: <strong>{inspection.source}</strong>
          </p>
          <p>{inspection.audio_path ?? 'No supervised audio'}</p>
          <p>Audio coverage: {(inspection.coverage * 100).toFixed(1)}%</p>
          {inspection.offset_seconds !== undefined && (
            <p>
              Timeline offset: {inspection.offset_seconds.toFixed(3)} seconds
            </p>
          )}
          {inspection.errors.map((value) => (
            <p role="alert" className="error" key={value}>
              {value}
            </p>
          ))}
          {inspection.warnings.map((value) => (
            <p key={value}>{value}</p>
          ))}
          {inspection.audio_path && (
            <audio
              aria-label="Resolved H3 audio"
              controls
              preload="metadata"
              src={`/api/datasets/${datasetId}/items/${itemId}/audio`}
            />
          )}
          <pre>
            {JSON.stringify(
              { video: inspection.metadata, audio: inspection.audio_metadata },
              null,
              2,
            )}
          </pre>
        </div>
      )}
    </details>
  );
}
