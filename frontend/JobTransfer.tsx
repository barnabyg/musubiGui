import type { Job } from './jobTypes';

export function JobTransfer({
  busy,
  dirty,
  job,
  transfer,
  setTransfer,
  setError,
  importConfiguration,
  exportConfiguration,
  setTransferOpen,
}: {
  busy: boolean;
  dirty: boolean;
  job: Job | null;
  transfer: string;
  setTransfer: (text: string) => void;
  setError: (message: string) => void;
  importConfiguration: () => Promise<void>;
  exportConfiguration: () => Promise<void>;
  setTransferOpen: (open: boolean) => void;
}) {
  return (
    <section className="panel" aria-label="Import or export configuration">
      <h2>Import / export configuration</h2>
      <p>
        Version 1 JSON preserves supported values. Unsupported formats and
        unknown fields are rejected. Recognised credentials are redacted; review
        local paths before sharing.
      </p>
      <label className="field">
        Configuration file
        <input
          type="file"
          accept="application/json,.json"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file)
              void file
                .text()
                .then(setTransfer)
                .catch((reason: Error) => setError(reason.message));
          }}
        />
      </label>
      <label className="field">
        Configuration JSON
        <textarea
          value={transfer}
          disabled={busy}
          onChange={(event) => setTransfer(event.target.value)}
        />
      </label>
      <div className="actions">
        <button
          disabled={busy || !transfer}
          onClick={() => void importConfiguration()}
        >
          Import configuration
        </button>
        <button
          disabled={busy || dirty || !job}
          onClick={() => void exportConfiguration()}
        >
          Export configuration
        </button>
      </div>
      {!transfer && (
        <p className="hint">Choose a file or paste JSON to import it.</p>
      )}
      {(dirty || !job) && (
        <p className="hint">
          {dirty
            ? 'Save your edits to export them.'
            : 'Open a job to export it.'}
        </p>
      )}
      <div className="actions">
        <button onClick={() => setTransferOpen(false)}>Close</button>
      </div>
    </section>
  );
}
