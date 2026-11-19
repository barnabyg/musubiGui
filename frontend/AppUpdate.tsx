import { useEffect, useEffectEvent, useState } from 'react';
import { apiClient } from './api';

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
    };

export function AppUpdate({
  disabled,
  onRunning,
  onComplete,
}: {
  disabled: boolean;
  onRunning: (running: boolean) => void;
  onComplete: () => void;
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
      status?.available &&
      status.running &&
      value.available &&
      value.phase === 'complete'
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
  return (
    <section className="panel">
      <div className="section-heading">
        <h2>Application updates</h2>
        <p>
          Update and restart Musubi GUI, install its supported trainer, and run
          diagnostics.
        </p>
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
        <p role="status">
          {waiting
            ? 'Waiting for Musubi GUI to restart. Keep the launcher window open. If it stops, check its output.'
            : status.message}
        </p>
      )}
    </section>
  );
}
