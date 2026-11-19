import { useEffect, useEffectEvent, useState } from 'react';
import { apiClient } from './api';
import { StatusBadge } from './StatusBadge';

const endpoint = '/api/app-update';
const api = apiClient('Could not start the application update.');
type Status =
  | { available: false }
  | {
      available: true;
      server: string;
      phase: string;
      running: boolean;
      message: string;
      gui_revision: string;
      gui_changed: boolean | null;
      completed_at: string | null;
    };

export function AppUpdate({
  disabled,
  onRunning,
  onComplete,
  onViewDiagnostics,
}: {
  disabled: boolean;
  onRunning: (running: boolean) => void;
  onComplete: () => void;
  onViewDiagnostics: () => void;
}) {
  const [status, setStatus] = useState<Status>();
  const [starting, setStarting] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState('');
  const [initiatedOn, setInitiatedOn] = useState('');
  const updateStatus = useEffectEvent((value: Status) => {
    if (starting && (!value.available || !value.running)) return;
    setStatus(value);
    setOffline(false);
    onRunning(value.available && value.running);
    if (
      value.available &&
      !value.running &&
      initiatedOn &&
      value.server !== initiatedOn &&
      !disabled
    ) {
      window.location.reload();
    } else if (
      value.available &&
      value.phase === 'complete' &&
      (!status?.available ||
        status.phase !== 'complete' ||
        status.completed_at !== value.completed_at)
    ) {
      onComplete();
    }
  });

  useEffect(() => {
    let current = true;
    let timer: number;
    async function refresh() {
      try {
        const value = await api<Status>(
          endpoint,
          'GET',
          undefined,
          AbortSignal.timeout(5000),
        );
        if (current) updateStatus(value);
      } catch {
        if (current) setOffline(true);
      } finally {
        if (current) timer = window.setTimeout(refresh, 2000);
      }
    }
    void refresh();
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, []);

  async function start() {
    if (!status?.available) return;
    setInitiatedOn(status.server);
    setStarting(true);
    setError('');
    onRunning(true);
    try {
      const next = await api<Status>(endpoint, 'POST', {});
      setStatus(next);
    } catch (reason) {
      if (!(reason instanceof TypeError)) setInitiatedOn('');
      setError((reason as Error).message);
      onRunning(false);
    } finally {
      setStarting(false);
    }
  }

  if (!status?.available) return null;
  const waiting = offline && (status.running || !!initiatedOn);
  const complete = status.phase === 'complete' && !waiting && !starting;
  return (
    <section className="panel" aria-labelledby="application-updates-heading">
      <div className="section-heading">
        <h2 id="application-updates-heading">Application updates</h2>
        <p>
          Update and restart Musubi GUI, install its supported trainer, and run
          diagnostics.
        </p>
        {status.gui_revision && (
          <p>
            Musubi GUI commit{' '}
            <code title={status.gui_revision}>
              {status.gui_revision.slice(0, 12)}
            </code>
          </p>
        )}
      </div>
      <button
        disabled={disabled || status.running || starting || waiting}
        onClick={() => void start()}
      >
        {status.phase === 'failed'
          ? 'Retry application update'
          : 'Update application'}
      </button>
      {error ? (
        <p className="error" role="alert">
          {error}
        </p>
      ) : (
        <div role="status">
          {complete && (
            <>
              <p>
                <StatusBadge tone="success">Update complete</StatusBadge>{' '}
                {status.completed_at && (
                  <time dateTime={status.completed_at}>
                    {new Date(status.completed_at).toLocaleString()}
                  </time>
                )}
              </p>
              {status.gui_changed === true && <p>Musubi GUI updated.</p>}
              {status.gui_changed === false && (
                <p>Musubi GUI was already up to date.</p>
              )}
            </>
          )}
          {status.phase === 'failed' && !waiting && !starting && (
            <p>
              <StatusBadge tone="failure">Update failed</StatusBadge>
            </p>
          )}
          <p>
            {waiting
              ? 'Waiting for Musubi GUI to restart. Keep the launcher window open. If it stops, check its output.'
              : status.message}
          </p>
        </div>
      )}
      {complete && !error && (
        <button className="quiet" onClick={onViewDiagnostics}>
          View diagnostics
        </button>
      )}
    </section>
  );
}
