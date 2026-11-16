import { expect, test } from '@playwright/test';
import { runWorkspace } from './runHarness';

for (const status of [409, 500])
  test(`cleanup handles a stale active status without swallowing HTTP ${status}`, async () => {
    const app = await runWorkspace();
    const realFetch = globalThis.fetch;
    let closing = false;
    try {
      const job = app.manifest.jobs.find((job) => job.family === 'wan')!;
      await app.post('/api/runs/prepare', { job_id: job.id, start: true });
      await expect
        .poll(async () => (await app.records())[0]?.status, {
          timeout: 240_000,
        })
        .toBe('completed');
      let stale = true;
      // Replay a stale list response at the HTTP boundary; Stop still reaches the real backend.
      globalThis.fetch = async (input, init) => {
        if (
          status === 500 &&
          String(input).endsWith('/stop') &&
          init?.method === 'POST'
        )
          return new Response(
            JSON.stringify({ detail: 'Injected server failure' }),
            { status: 500, headers: { 'Content-Type': 'application/json' } },
          );
        const response = await realFetch(input, init);
        if (stale && String(input) === `${app.url}/api/runs` && !init?.method) {
          stale = false;
          const runs = await response.json();
          runs[0].status = 'generating';
          return new Response(JSON.stringify(runs), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        return response;
      };
      closing = true;
      if (status === 409) await app.close();
      else await expect(app.close()).rejects.toThrow('Injected server failure');
    } finally {
      globalThis.fetch = realFetch;
      if (!closing) await app.close();
    }
  });
