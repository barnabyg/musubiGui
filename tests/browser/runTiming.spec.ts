import { test, expect } from '@playwright/test';
import { runWorkspace } from './runHarness';

test('training elapsed spans expert switches and survives reload independently of phase ETA', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const run = (await app.post('/api/runs/prepare', { job_id: job.id })) as {
      id: string;
    };
    await page.route(`**/api/runs/${run.id}`, async (route) => {
      const response = await route.fetch();
      const record = await response.json();
      await route.fulfill({
        json: {
          ...record,
          status: 'completed',
          stages: [
            {
              name: 'train',
              status: 'completed',
              started: '2026-11-11T10:00:00Z',
              finished: '2026-11-11T10:01:00Z',
            },
            {
              name: 'train',
              status: 'completed',
              started: '2026-11-11T10:01:10Z',
              finished: '2026-11-11T10:01:30Z',
            },
          ],
        },
      });
    });
    await page.route(`**/api/runs/${run.id}/logs`, (route) =>
      route.fulfill({
        json: {
          text: '',
          progress: {
            step: 6,
            total: 8,
            elapsed: '00:02',
            eta: '00:20',
            eta_scope: 'phase',
            loss: '0.5',
          },
        },
      }),
    );
    await page.goto(`${app.url}/#/runs/${run.id}`);
    const progress = page.getByLabel('Training progress');
    await expect(progress).toContainText('Training elapsed 01:30');
    await expect(progress).toContainText('Phase ETA 00:20');
    await page.reload();
    await expect(progress).toContainText('Training elapsed 01:30');
    await page.unroute(`**/api/runs/${run.id}/logs`);
    await page.route(`**/api/runs/${run.id}/logs`, (route) =>
      route.fulfill({
        json: {
          text: '',
          progress: { step: 6, total: 8, elapsed: null, eta: null, loss: null },
        },
      }),
    );
    await page.reload();
    await expect(progress).toContainText('Training elapsed 01:30');
    await expect(progress).toContainText('Training ETA unknown');
  } finally {
    await app.close();
  }
});
