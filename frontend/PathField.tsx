import { useEffect, useId, useRef, useState } from 'react';
import { apiClient } from './api';

type Kind = 'folder' | 'file';
type Check = { state: string; message: string };
type Listing = {
  path: string;
  parent: string | null;
  entries: { name: string; path: string; kind: Kind }[];
};

const request = apiClient('Path request failed.');

// Advice while typing; the backend still enforces every rule when the value is saved or used.
function usePathCheck(value: string, kind: Kind, roots: string[]) {
  const [result, setResult] = useState<Check & { for: string }>();
  const key = JSON.stringify([value, kind, roots]);
  useEffect(() => {
    if (!value.trim()) return;
    let current = true;
    const timer = window.setTimeout(() => {
      void request<Check>('/api/paths/check', 'POST', {
        path: value,
        kind,
        roots,
      })
        .then((check) => {
          if (current) setResult({ ...check, for: key });
        })
        .catch(() => {
          // A failed check says nothing about the path, so it shows nothing.
        });
    }, 250);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
    // `key` stands for value, kind and the contents of roots, whose array identity changes on render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return value.trim() && result?.for === key ? result : undefined;
}

function PathBrowser({
  title,
  kind,
  start,
  onChoose,
  onClose,
}: {
  title: string;
  kind: Kind;
  start: string;
  onChoose: (path: string) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [location, setLocation] = useState(start);
  const [listing, setListing] = useState<Listing>();
  const [error, setError] = useState('');
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  useEffect(() => {
    let current = true;
    void request<Listing>('/api/paths/browse', 'POST', {
      path: location,
      kind,
    })
      .then((value) => {
        if (!current) return;
        setListing(value);
        setError('');
      })
      .catch((reason: Error) => {
        if (current) setError(reason.message);
      });
    return () => {
      current = false;
    };
  }, [location, kind]);
  return (
    <dialog
      ref={dialog}
      className="confirm-dialog path-browser"
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <h2>{title}</h2>
      <p className="path-location">{listing?.path || 'Drives'}</p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <ul aria-label="Folder contents">
        {listing && listing.parent !== null && (
          <li>
            <button onClick={() => setLocation(listing.parent ?? '')}>
              ↑ Up
            </button>
          </li>
        )}
        {listing?.entries.map((entry) => (
          <li key={entry.path}>
            <button
              onClick={() =>
                entry.kind === 'folder'
                  ? setLocation(entry.path)
                  : onChoose(entry.path)
              }
            >
              <span aria-hidden="true">
                {entry.kind === 'folder' ? '▸ ' : ''}
              </span>
              {entry.name}
            </button>
          </li>
        ))}
        {listing?.entries.length === 0 && (
          <li className="hint">Nothing to choose here.</li>
        )}
      </ul>
      <div className="actions">
        <button onClick={onClose}>Cancel</button>
        {kind === 'folder' && (
          <button
            className="primary"
            disabled={!listing?.path}
            onClick={() => listing && onChoose(listing.path)}
          >
            Choose this folder
          </button>
        )}
      </div>
    </dialog>
  );
}

function Browse({
  label,
  kind,
  start,
  disabled,
  onChoose,
}: {
  label: string;
  kind: Kind;
  start: string;
  disabled?: boolean;
  onChoose: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  function close() {
    setOpen(false);
    // The page behind a modal dialog can't take focus until the dialog has gone.
    window.setTimeout(() => trigger.current?.focus(), 0);
  }
  return (
    <>
      <button
        ref={trigger}
        type="button"
        aria-label={`Browse for ${label}`}
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        Browse
      </button>
      {open && (
        <PathBrowser
          title={`Choose ${label}`}
          kind={kind}
          start={start}
          onClose={close}
          onChoose={(path) => {
            onChoose(path);
            close();
          }}
        />
      )}
    </>
  );
}

function CheckMessage({ id, check }: { id: string; check?: Check }) {
  if (!check?.message) return null;
  return (
    <small id={id} className="path-warning" role="alert">
      {check.message}
    </small>
  );
}

export function PathField({
  label,
  value,
  kind,
  roots = [],
  help,
  placeholder,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  kind: Kind;
  roots?: string[];
  help?: string;
  placeholder?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const [focused, setFocused] = useState(false);
  const check = usePathCheck(value, kind, roots);
  return (
    <div className="field path-field">
      <label htmlFor={id}>{label}</label>
      <div className="path-row">
        <input
          id={id}
          value={value}
          title={value || undefined}
          disabled={disabled}
          placeholder={placeholder}
          aria-invalid={check?.message ? true : undefined}
          aria-describedby={check?.message ? `${id}-check` : undefined}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onChange={(event) => onChange(event.target.value)}
        />
        <Browse
          label={label}
          kind={kind}
          start={value || roots[0] || ''}
          disabled={disabled}
          onChoose={onChange}
        />
      </div>
      {focused && value && <small className="path-full">{value}</small>}
      <CheckMessage id={`${id}-check`} check={check} />
      {help && <small>{help}</small>}
    </div>
  );
}

function RootLineCheck({ line }: { line: string }) {
  const id = useId();
  const check = usePathCheck(line, 'folder', []);
  if (!check?.message) return null;
  return (
    <CheckMessage
      id={id}
      check={{ ...check, message: `${line}: ${check.message}` }}
    />
  );
}

export function RootsField({
  label,
  browseLabel,
  value,
  placeholder,
  help,
  disabled,
  onChange,
}: {
  label: string;
  browseLabel: string;
  value: string;
  placeholder?: string;
  help?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const id = useId();
  const lines = value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  return (
    <div className="field path-field">
      <label htmlFor={id}>{label}</label>
      <div className="path-row">
        <textarea
          id={id}
          disabled={disabled}
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
        <Browse
          label={browseLabel}
          kind="folder"
          start={lines.at(-1) ?? ''}
          disabled={disabled}
          onChoose={(path) =>
            onChange(lines.length ? `${lines.join('\n')}\n${path}` : path)
          }
        />
      </div>
      {lines.map((line) => (
        <RootLineCheck key={line} line={line} />
      ))}
      {help && <small>{help}</small>}
    </div>
  );
}
