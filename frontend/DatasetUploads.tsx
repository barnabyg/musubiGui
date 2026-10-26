import { useEffect, useRef, useState } from 'react';

type Session = {
  id: string;
  filename: string;
  size: number;
  received: number;
  status: string;
};
type Entry = { file: File; progress: number; status: string; error?: string };
type Props = {
  datasetId: string;
  beforeAction: () => Promise<boolean>;
  onChanged: () => Promise<void>;
};

export function DatasetUploads({ datasetId, beforeAction, onChanged }: Props) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const abort = useRef<AbortController | null>(null);
  const url = `/api/datasets/${datasetId}/uploads`;
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    void fetch(url)
      .then(async (response) => {
        if (!response.ok)
          throw new Error('Incomplete uploads could not be read.');
        setSessions((await response.json()) as Session[]);
      })
      .catch((reason: Error) => setError(reason.message));
  }, [url]);
  async function request<T>(
    path: string,
    data?: object,
    method = 'POST',
    signal?: AbortSignal,
  ): Promise<T> {
    const response = await fetch(
      url + path,
      data
        ? {
            method,
            signal,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
          }
        : { signal },
    );
    const value = (await response.json()) as T & { detail?: string };
    if (!response.ok)
      throw new Error(
        value.detail ??
          `Upload failed (${response.status}). Completed files are preserved.`,
      );
    return value;
  }
  async function refresh() {
    setSessions(await request<Session[]>(''));
  }
  async function upload() {
    if (!(await beforeAction())) return;
    setBusy(true);
    setError('');
    const controller = new AbortController();
    abort.current = controller;
    const update = (file: File, patch: Partial<Entry>) =>
      setEntries((values) =>
        values.map((entry) =>
          entry.file === file ? { ...entry, ...patch } : entry,
        ),
      );
    const ordered = [...entries].sort(
      (a, b) =>
        Number(a.file.name.toLowerCase().endsWith('.txt')) -
        Number(b.file.name.toLowerCase().endsWith('.txt')),
    );
    try {
      for (const entry of ordered) {
        if (entry.status === 'Uploaded') continue;
        if (controller.signal.aborted) break;
        const file = entry.file;
        update(file, { status: 'Uploading', error: undefined });
        try {
          const existing = await request<Session[]>(
            '',
            undefined,
            'GET',
            controller.signal,
          );
          let session = existing.find(
            (value) =>
              value.filename === file.name &&
              value.size === file.size &&
              value.status === 'uploading',
          );
          session ??= await request<Session>(
            '',
            { filename: file.name, size: file.size },
            'POST',
            controller.signal,
          );
          for (let offset = 0; offset < file.size; offset += 512 * 1024) {
            const bytes = new Uint8Array(
              await file.slice(offset, offset + 512 * 1024).arrayBuffer(),
            );
            const parts: string[] = [];
            for (let index = 0; index < bytes.length; index += 8192)
              parts.push(
                String.fromCharCode(...bytes.subarray(index, index + 8192)),
              );
            session = await request<Session>(
              `/${session.id}`,
              { offset, data: btoa(parts.join('')) },
              'PUT',
              controller.signal,
            );
            update(file, {
              progress: Math.round(
                (session.received * 100) / Math.max(file.size, 1),
              ),
            });
          }
          update(file, { status: 'Validating media', progress: 100 });
          await request(
            `/${session.id}/complete`,
            {},
            'POST',
            controller.signal,
          );
          update(file, { status: 'Uploaded', progress: 100 });
          await onChanged();
        } catch (reason) {
          update(file, {
            status: controller.signal.aborted ? 'Paused' : 'Failed',
            error: controller.signal.aborted
              ? 'Partial upload is preserved. Select the same file and retry to verify/resume its bytes.'
              : (reason as Error).message,
          });
        }
      }
    } finally {
      abort.current = null;
      setBusy(false);
      try {
        await refresh();
      } catch (reason) {
        setError((reason as Error).message);
      }
    }
  }
  async function cancel(id: string) {
    try {
      await request(`/${id}/cancel`, {});
      await refresh();
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  return (
    <section className="upload-panel">
      <h2>Upload media and captions</h2>
      <p>
        Upload images/videos, audio sidecars and matching UTF-8 .txt captions.
        Each filename is exclusive; collisions preserve the existing file. Media
        is validated before appearing in the gallery.
      </p>
      <label className="field">
        Upload files
        <input
          type="file"
          multiple
          disabled={busy}
          onChange={(event) =>
            setEntries(
              Array.from(event.target.files ?? []).map((file) => ({
                file,
                progress: 0,
                status: 'Ready',
              })),
            )
          }
        />
      </label>
      <div className="actions">
        <button
          disabled={busy || !entries.length}
          onClick={() => void upload()}
        >
          Upload selected files
        </button>
        <button disabled={!busy} onClick={() => abort.current?.abort()}>
          Pause uploads
        </button>
      </div>
      {entries.map((entry, index) => (
        <div key={index} aria-label={`Upload result ${entry.file.name}`}>
          <p>
            {entry.file.name}: {entry.status} · {entry.progress}%
          </p>
          <progress
            aria-label={`Progress ${entry.file.name}`}
            value={entry.progress}
            max={100}
          />
          {entry.error && (
            <p role="alert" className="error">
              {entry.error}
            </p>
          )}
        </div>
      ))}
      {error && <p role="alert">{error}</p>}
      {sessions.filter((value) => value.status === 'uploading').length > 0 && (
        <div>
          <h3>Incomplete uploads</h3>
          <p>
            Select the same local file to verify its uploaded prefix and resume
            after interruption. Cancel removes only that upload's private
            temporary file.
          </p>
          {sessions
            .filter((value) => value.status === 'uploading')
            .map((value) => (
              <p key={value.id}>
                {value.filename} · {value.received}/{value.size} bytes{' '}
                <button disabled={busy} onClick={() => void cancel(value.id)}>
                  Cancel partial {value.filename}
                </button>
              </p>
            ))}
        </div>
      )}
    </section>
  );
}
