import { useSyncExternalStore } from 'react';
import { apiClient } from './api';

export type RunSummary = {
  id: string;
  created: string;
  status: string;
  order: number;
  job: { id: string; name: string; family: string; revision: number };
  stages: { name: string; status: string }[];
  change_key: string;
};

const read = apiClient('Run history could not be read.');
const listeners = new Set<() => void>();
let snapshot: { runs: RunSummary[] | null; error: string } = {
  runs: null,
  error: '',
};
let pending: Promise<void> | undefined;
let controller: AbortController | undefined;
let timer: number | undefined;

function publish(next: typeof snapshot) {
  snapshot = next;
  for (const listener of listeners) listener();
}

function poll() {
  if (pending) return pending;
  window.clearTimeout(timer);
  const request = new AbortController();
  controller = request;
  pending = read<RunSummary[]>(
    '/api/runs/summaries',
    'GET',
    undefined,
    request.signal,
  )
    .then((runs) => {
      if (!request.signal.aborted) publish({ runs, error: '' });
    })
    .catch((reason: Error) => {
      if (!request.signal.aborted)
        publish({ ...snapshot, error: reason.message });
    })
    .finally(() => {
      pending = undefined;
      if (listeners.size) timer = window.setTimeout(() => void poll(), 1000);
    });
  return pending;
}

// Mutations wait out an older request, then read history that includes their result.
export async function refreshRunHistory() {
  if (pending) await pending;
  await poll();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  void poll();
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      window.clearTimeout(timer);
      controller?.abort();
    }
  };
}

const getSnapshot = () => snapshot;

// Runs and completion signals share one request and one completion-scheduled timer.
export function useRunHistory() {
  return useSyncExternalStore(subscribe, getSnapshot);
}
