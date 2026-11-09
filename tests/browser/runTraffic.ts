import type { Page, Request } from '@playwright/test';

export type RunTrafficEntry = { path: string; bytes: number };

export function observeRunTraffic(page: Page) {
  let observation:
    { entries: RunTrafficEntry[]; completed: Promise<void>[] } | undefined;
  const pending = new Map<
    Request,
    { entry: RunTrafficEntry; finish: () => void }
  >();
  // Attribute each request before any response/body waits can cross the window boundary.
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (!observation || !path.startsWith('/api/runs')) return;
    const entry = { path, bytes: 0 };
    let finish = () => {};
    observation.entries.push(entry);
    observation.completed.push(
      new Promise<void>((resolve) => (finish = resolve)),
    );
    pending.set(request, { entry, finish });
  });
  page.on('response', async (response) => {
    const recorded = pending.get(response.request());
    if (!recorded) return;
    try {
      const body = await response.body().catch(() => Buffer.alloc(0));
      recorded.entry.bytes = body.length;
    } finally {
      pending.delete(response.request());
      recorded.finish();
    }
  });
  page.on('requestfailed', (request) => {
    pending.get(request)?.finish();
    pending.delete(request);
  });
  return {
    start() {
      observation = { entries: [], completed: [] };
    },
    async finish() {
      const finished = observation;
      observation = undefined;
      await Promise.all(finished?.completed ?? []);
      return finished?.entries ?? [];
    },
  };
}
