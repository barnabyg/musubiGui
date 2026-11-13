import { useEffect, useState } from 'react';
import { apiClient } from './api';
import type { Dataset } from './Datasets';
import { PathField } from './PathField';

type Preparation = {
  state: string;
  dataset_id: string;
  path: string;
  completed: number;
  total: number;
  error?: string;
  items: {
    name: string;
    state: string;
    error: string;
    width?: number;
    height?: number;
  }[];
};
const api = apiClient('H3 preparation failed.');

export function H3Preparation({
  dataset,
  roots,
  afterCaptionFlush,
}: {
  dataset: Dataset;
  roots: string[];
  afterCaptionFlush: (action: () => Promise<void>) => Promise<unknown>;
}) {
  const [width, setWidth] = useState(640);
  const [height, setHeight] = useState(352);
  const [frames, setFrames] = useState(124);
  const [orientation, setOrientation] = useState('auto');
  const [fit, setFit] = useState('pad');
  const [allowMissing, setAllowMissing] = useState(false);
  const [parent, setParent] = useState(roots[0] ?? '');
  const [name, setName] = useState(`${dataset.name} · H3`.slice(0, 200));
  const [record, setRecord] = useState<Preparation | null>(null);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [busy, setBusy] = useState(false);
  const endpoint = `/api/datasets/${dataset.id}/h3-preparation`;
  useEffect(() => {
    let current = true;
    let timer: number;
    async function poll() {
      try {
        const value = await api<Preparation | null>(endpoint);
        if (current) {
          setRecord(value);
          setPollError('');
        }
      } catch (reason) {
        if (current) setPollError((reason as Error).message);
      }
      if (current) timer = window.setTimeout(() => void poll(), 1500);
    }
    void poll();
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [endpoint]);
  const running = busy || record?.state === 'running';
  async function start() {
    setBusy(true);
    setError('');
    try {
      await afterCaptionFlush(async () => {
        setRecord(
          await api<Preparation>(endpoint, 'POST', {
            name,
            output_root: parent,
            width,
            height,
            frames,
            orientation,
            fit,
            allow_missing_audio: allowMissing,
          }),
        );
      });
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details>
      <summary>Prepare videos for H3</summary>
      <p>
        Create a new dataset from all included videos. Originals stay in place.
        Each copy uses the beginning of its source; shorter clips are skipped
        without padding. Captions are copied: review them after trimming.
      </p>
      <p>
        Landscape 640 × 352 at 124 frames has trained on the target 24 GB GPU.
        Portrait uses the same pixel count, but portrait and mixed-orientation
        GPU training have not been measured. Larger settings may run out of GPU
        memory during training.
      </p>
      <fieldset disabled={running}>
        <label className="field">
          Prepared dataset name
          <input
            value={name}
            maxLength={200}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <PathField
          label="Prepared dataset parent"
          value={parent}
          onChange={setParent}
          kind="folder"
          roots={roots}
        />
        <p>
          Choose a parent outside this source dataset and within a configured
          dataset root. A new folder is created for every batch.
        </p>
        <label className="field">
          H3 orientation buckets
          <select
            value={orientation}
            onChange={(e) => setOrientation(e.target.value)}
          >
            <option value="auto">Automatic landscape and portrait</option>
            <option value="fixed">One fixed width and height</option>
          </select>
        </label>
        <label className="field">
          H3 preparation width
          <input
            type="number"
            min={64}
            max={2048}
            step={32}
            value={width}
            onChange={(e) => setWidth(Number(e.target.value))}
          />
        </label>
        <label className="field">
          H3 preparation height
          <input
            type="number"
            min={64}
            max={2048}
            step={32}
            value={height}
            onChange={(e) => setHeight(Number(e.target.value))}
          />
        </label>
        <label className="field">
          H3 clip length
          <select
            value={frames}
            onChange={(e) => setFrames(Number(e.target.value))}
          >
            {Array.from({ length: 14 }, (_, i) => 124 + i * 17).map((value) => (
              <option key={value} value={value}>
                {value} frames · {(value / 24).toFixed(3)} seconds
              </option>
            ))}
          </select>
        </label>
        <p>
          {orientation === 'auto'
            ? `Buckets: landscape ${Math.max(width, height)} × ${Math.min(width, height)}; portrait ${Math.min(width, height)} × ${Math.max(width, height)}`
            : `Output: ${width} × ${height}`}
          , 24 fps, {(frames / 24).toFixed(3)} seconds. Source clips must be at
          least this long.
        </p>
        {orientation === 'auto' && (
          <p>
            Orientation follows the displayed video, including rotation. Square
            sources use the landscape bucket. Fit with black borders keeps the
            whole picture. Enable aspect-ratio bucketing in the training job to
            preserve these shapes.
          </p>
        )}
        <label className="field">
          H3 aspect ratio handling
          <select value={fit} onChange={(e) => setFit(e.target.value)}>
            <option value="pad">Fit whole picture with black borders</option>
            <option value="crop">
              Centre crop to fill (can cut off subjects)
            </option>
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={allowMissing}
            onChange={(e) => setAllowMissing(e.target.checked)}
          />{' '}
          Allow videos without audio
        </label>
        <p>
          Supplied audio is aligned and converted to 32 kHz stereo WAV. Missing
          audio stays absent and requires allow-missing audio policy in the
          training job.
        </p>
        <button
          disabled={
            !name.trim() ||
            !parent ||
            width % 32 !== 0 ||
            height % 32 !== 0 ||
            width < 64 ||
            height < 64
          }
          onClick={() => void start()}
        >
          Prepare H3 dataset
        </button>
      </fieldset>
      {error && <p role="alert">{error}</p>}
      {pollError && <p role="alert">{pollError}</p>}
      {record && (
        <div aria-label="H3 preparation result">
          <p role="status">
            Preparation: {record.state} · {record.completed}/{record.total}{' '}
            processed ·{' '}
            {record.items.filter((item) => item.state === 'complete').length}{' '}
            prepared
          </p>
          <p className="dataset-path">{record.path}</p>
          {record.error && <p role="alert">{record.error}</p>}
          {record.state !== 'running' && (
            <a href={`#/datasets/${record.dataset_id}`}>
              Open prepared dataset
            </a>
          )}
          <ul>
            {record.items.map((item) => (
              <li key={item.name}>
                {item.name}:{' '}
                {item.state === 'complete'
                  ? `prepared${item.width && item.height ? ` · ${item.width} × ${item.height}` : ''}`
                  : item.error}
              </li>
            ))}
          </ul>
        </div>
      )}
    </details>
  );
}
