import { useEffect, useRef, useState } from 'react';
import { apiClient } from './api';

const request = apiClient(
  (status) => `Upload failed (${status}). Completed files are preserved.`,
);
const readSessions = apiClient('Incomplete uploads could not be read.');

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
  const uploading = useRef(false);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const [error, setError] = useState('');
  const abort = useRef<AbortController | null>(null);
  const url = `/api/datasets/${datasetId}/uploads`;
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    void readSessions<Session[]>(url)
      .then(setSessions)
      .catch((reason: Error) => setError(reason.message));
  }, [url]);
  async function refresh() {
    setSessions(await request<Session[]>(url));
  }
  function selectFiles(files: FileList | null) {
    if (uploading.current || !files?.length) return;
    const selected = Array.from(files).map((file) => ({
      file,
      progress: 0,
      status: 'Ready',
    }));
    setEntries(selected);
    return selected;
  }
  async function upload(batch: Entry[] = entries) {
    if (uploading.current || !batch.length) return;
    // Reserve this batch before caption flushing or React's next render.
    uploading.current = true;
    setBusy(true);
    setDragging(false);
    dragDepth.current = 0;
    setError('');
    const controller = new AbortController();
    abort.current = controller;
    const update = (file: File, patch: Partial<Entry>) =>
      setEntries((values) =>
        values.map((entry) =>
          entry.file === file ? { ...entry, ...patch } : entry,
        ),
      );
    const ordered = [...batch].sort(
      (a, b) =>
        Number(a.file.name.toLowerCase().endsWith('.txt')) -
        Number(b.file.name.toLowerCase().endsWith('.txt')),
    );
    try {
      if (!(await beforeAction()) || controller.signal.aborted) return;
      for (const entry of ordered) {
        if (entry.status === 'Uploaded') continue;
        if (controller.signal.aborted) break;
        const file = entry.file;
        update(file, { status: 'Uploading', error: undefined });
        try {
          const existing = await request<Session[]>(
            url,
            'GET',
            undefined,
            controller.signal,
          );
          let session = existing.find(
            (value) =>
              value.filename === file.name &&
              value.size === file.size &&
              value.status === 'uploading',
          );
          session ??= await request<Session>(
            url,
            'POST',
            { filename: file.name, size: file.size },
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
              `${url}/${session.id}`,
              'PUT',
              { offset, data: btoa(parts.join('')) },
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
            `${url}/${session.id}/complete`,
            'POST',
            {},
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
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      abort.current = null;
      try {
        await refresh();
      } catch (reason) {
        setError((reason as Error).message);
      }
      uploading.current = false;
      setBusy(false);
    }
  }
  async function cancel(id: string) {
    try {
      await request(`${url}/${id}/cancel`, 'POST', {});
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
      <div
        role="group"
        aria-label="Dataset file drop area"
        aria-disabled={busy}
        className={`upload-drop-zone${dragging ? ' dragging' : ''}`}
        onDragEnter={(event) => {
          if (!event.dataTransfer.types.includes('Files')) return;
          event.preventDefault();
          dragDepth.current += 1;
          if (!busy) setDragging(true);
        }}
        onDragOver={(event) => {
          if (!event.dataTransfer.types.includes('Files')) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = busy ? 'none' : 'copy';
        }}
        onDragLeave={() => {
          dragDepth.current = Math.max(0, dragDepth.current - 1);
          if (!dragDepth.current) setDragging(false);
        }}
        onDrop={(event) => {
          // Cancel native file opening even when the upload queue is busy.
          event.preventDefault();
          dragDepth.current = 0;
          setDragging(false);
          const selected = selectFiles(event.dataTransfer.files);
          if (selected) void upload(selected);
        }}
      >
        <p>
          {busy
            ? 'Wait for uploads to finish or pause them before selecting files.'
            : dragging
              ? 'Drop files here to start uploading.'
              : 'Drag and drop images, videos, audio sidecars and matching .txt captions here, or choose files below.'}
        </p>
        <p>
          Dropped files start uploading immediately. Files chosen below start
          when you choose Upload selected files.
        </p>
        <label className="field">
          Upload files
          <input
            type="file"
            multiple
            disabled={busy}
            onChange={(event) => {
              selectFiles(event.target.files);
              // Allow choosing the same file again after a drop replaces it.
              event.target.value = '';
            }}
          />
        </label>
      </div>
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
                <button
                  className="danger"
                  disabled={busy}
                  onClick={() => void cancel(value.id)}
                >
                  Cancel partial {value.filename}
                </button>
              </p>
            ))}
        </div>
      )}
    </section>
  );
}
