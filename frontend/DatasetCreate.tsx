import { PathField } from './PathField';

export function DatasetCreate({
  busy,
  name,
  setName,
  folder,
  setFolder,
  datasetRoots,
  create,
}: {
  busy: boolean;
  name: string;
  setName: (value: string) => void;
  folder: string;
  setFolder: (value: string) => void;
  datasetRoots?: string[];
  create: (managed: boolean) => Promise<void>;
}) {
  return (
    <section className="panel dataset-create">
      <h2>Dataset settings</h2>
      <div className="columns">
        <label className="field">
          Dataset name
          <input
            maxLength={200}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <PathField
          label="Dataset folder"
          kind="folder"
          roots={datasetRoots}
          className="dataset-folder-field"
          missingMessage="This folder doesn't exist yet. Create a new folder for uploads will create it."
          value={folder}
          placeholder="C:\datasets\…"
          onChange={setFolder}
        />
      </div>
      <p>
        Use a folder within the dataset roots configured in Settings. Register
        an existing folder to edit its media and captions in place, or create an
        isolated subfolder for uploads.
      </p>
      <div className="actions">
        <button
          className="primary"
          disabled={busy || !name.trim() || !folder.trim()}
          onClick={() => void create(true)}
        >
          Create a new folder for uploads
        </button>
        <button
          disabled={busy || !name.trim() || !folder.trim()}
          onClick={() => void create(false)}
        >
          Use existing folder
        </button>
      </div>
      {busy && <p role="status">Opening dataset…</p>}
    </section>
  );
}
