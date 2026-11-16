import { useSyncExternalStore } from 'react';
import { apiClient } from './api';

// A Run's place in its chain of resumed segments, as the backend defines it (musubi_gui/run_chains.py).
export type ChainPlace = {
  // The chain's first segment.
  id: string;
  size: number;
  // 1-based, in creation order.
  position: number;
  latest: string;
  // On the path from the first segment to the latest; other segments are side branches.
  on_line: boolean;
  source: string | null;
  missing_source: string | null;
  later_segments: number;
  steps: {
    start: number;
    target: number;
    end: number | null;
    legacy: boolean;
  };
};

export type RunSummary = {
  id: string;
  created: string;
  status: string;
  order: number;
  job: { id: string; name: string; family: string; revision: number };
  stages: { name: string; status: string }[];
  chain: ChainPlace;
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
