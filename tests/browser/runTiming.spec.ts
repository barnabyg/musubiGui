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
    let failedDuringLaunch = false;
    await page.route(`**/api/runs/${run.id}`, async (route) => {
      const response = await route.fetch();
      const record = await response.json();
      await route.fulfill({
        json: {
          ...record,
          status: failedDuringLaunch ? 'failed' : 'completed',
          finished: '2026-11-11T10:01:30Z',
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
              finished: failedDuringLaunch ? undefined : '2026-11-11T10:01:30Z',
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
            seconds_per_step: 83.2,
          },
        },
      }),
    );
    await page.goto(`${app.url}/#/runs/${run.id}`);
    const progress = page.getByLabel('Training progress');
    await expect(progress).toContainText('Training elapsed 01:30');
    await expect(progress).toContainText('Phase ETA 00:20');
    await expect(progress).toContainText('Seconds per step: 83.20 s/step');
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
    await expect(progress).toContainText('Seconds per step: unknown');
    failedDuringLaunch = true;
    await page.reload();
    await expect(progress).toContainText('Training elapsed 01:30');
  } finally {
    await app.close();
  }
});

test('H3 diagnostics download contains measurements from the completed fixture run', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'h3')!;
    const run = (await app.post('/api/runs/prepare', {
      job_id: job.id,
      start: true,
    })) as { id: string };
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await expect(
      page.getByRole('link', { name: 'Download final LoRA', exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const link = page.getByRole('link', {
      name: 'Download performance diagnostics',
    });
    await expect(link).toBeVisible();
    const response = await page.request.get(
      app.url + (await link.getAttribute('href'))!,
    );
    expect(response.ok()).toBe(true);
    const records = (await response.text())
      .trim()
      .split('\n')
      .map((line) => JSON.parse(line));
    expect(
      records.some(
        (row) =>
          row.event === 'stage_start' && row.stage === 'train' && row.fixture,
      ),
    ).toBe(true);
    expect(
      records.some(
        (row) => row.event === 'sample' && row.host_available_bytes > 0,
      ),
    ).toBe(true);
    await page.reload();
    await expect(link).toBeVisible();
  } finally {
    await app.close();
  }
});
