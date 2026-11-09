import { useState } from 'react';
import type { Dataset } from './Datasets';
import { PathField } from './PathField';
import { formatRoute } from './route';

function folderKey(value: string) {
  const parts: string[] = [];
  for (const part of value.trim().replace(/\\/g, '/').split('/')) {
    if (!part || part === '.') continue;
    if (part === '..' && parts.length > 1) parts.pop();
    else parts.push(part);
  }
  return parts.join('/').toLowerCase();
}

export function DatasetCreate({
  busy,
  name,
  setName,
  folder,
  setFolder,
  datasetRoots,
  datasets,
  create,
}: {
  busy: boolean;
  name: string;
  setName: (value: string) => void;
  folder: string;
  setFolder: (value: string) => void;
  datasetRoots?: string[];
  datasets: Dataset[];
  create: (managed: boolean, folder: string) => Promise<void>;
}) {
  const [method, setMethod] = useState<'upload' | 'existing'>('upload');
  const [existingFolder, setExistingFolder] = useState('');
  const [acknowledgedRoot, setAcknowledgedRoot] = useState('');
  const managed = method === 'upload';
  const path = managed ? folder : existingFolder;
  const existing =
    !managed &&
    datasets.find((dataset) => folderKey(dataset.path) === folderKey(path));
  const wholeRoot =
    !managed &&
    datasetRoots?.some((root) => folderKey(root) === folderKey(path));
  const acknowledged = !!path.trim() && acknowledgedRoot === folderKey(path);
  const noRoots = datasetRoots !== undefined && datasetRoots.length === 0;
  return (
    <section className="panel dataset-create">
      <h2>Dataset settings</h2>
      <fieldset className="dataset-create-method" disabled={busy}>
        <legend>How will you add media?</legend>
        <label>
          <input
            type="radio"
            name="dataset-method"
            checked={managed}
            onChange={() => setMethod('upload')}
          />
          Upload files
        </label>
        <label>
          <input
            type="radio"
            name="dataset-method"
            checked={!managed}
            onChange={() => setMethod('existing')}
          />
          Use existing folder
        </label>
      </fieldset>
      {noRoots && (
        <p role="status">
          No dataset root is configured.{' '}
          <a href={formatRoute({ screen: 'Settings' })}>
            Set a dataset root in Settings
          </a>
          . Save your root, then use browser Back to return to this form.
        </p>
      )}
      <div className="columns">
        <label className="field">
          Dataset name
          <input
            maxLength={200}
            disabled={busy}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <PathField
          label={managed ? 'Upload parent folder' : 'Dataset folder'}
          kind="folder"
          roots={datasetRoots}
          className="dataset-folder-field"
          missingMessage={
            managed
              ? "This folder doesn't exist yet. Create a new folder for uploads will create it."
              : undefined
          }
          value={path}
          disabled={busy}
          placeholder="C:\datasets\…"
          onChange={managed ? setFolder : setExistingFolder}
        />
      </div>
      <p>
        {managed
          ? 'A separate managed subfolder will be created inside the upload parent folder. Its final path appears in Dataset settings after creation.'
          : `Index this exact folder and all its subfolders: ${path || 'choose a folder'}. Source media and captions stay in place.`}
      </p>
      {existing && (
        <p role="status">
          This folder is already registered as “{existing.name}”.{' '}
          <a href={formatRoute({ screen: 'Datasets', id: existing.id })}>
            Open existing dataset
          </a>
        </p>
      )}
      {wholeRoot && !existing && (
        <div className="dataset-root-warning">
          <p>
            This is an entire configured dataset root. Indexing it includes all
            descendant folders, which may contain other datasets.
          </p>
          <label>
            <input
              type="checkbox"
              disabled={busy}
              checked={acknowledged}
              onChange={(event) =>
                setAcknowledgedRoot(event.target.checked ? folderKey(path) : '')
              }
            />
            Index this entire dataset root
          </label>
        </div>
      )}
      <div className="actions">
        <button
          className="primary"
          disabled={
            busy ||
            !datasetRoots?.length ||
            !name.trim() ||
            !path.trim() ||
            !!existing ||
            (wholeRoot && !acknowledged)
          }
          onClick={() => void create(managed, path)}
        >
          {managed ? 'Create a new folder for uploads' : 'Use existing folder'}
        </button>
      </div>
      {busy && <p role="status">Opening dataset…</p>}
    </section>
  );
}
