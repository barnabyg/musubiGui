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
          execution_started: '2026-11-11T09:59:30Z',
          finished: '2026-11-11T10:02:00Z',
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
    await expect(page.getByLabel('Run elapsed')).toContainText('02:30');
    await expect(progress).toContainText('Phase ETA 00:20');
    await expect(progress).toContainText('Seconds per step: 83.20 s/step');
    await page.reload();
    await expect(progress).toContainText('Training elapsed 01:30');
    await expect(page.getByLabel('Run elapsed')).toContainText('02:30');
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
    await expect(progress).toContainText('Training elapsed 02:00');
  } finally {
    await app.close();
  }
});

test('Run elapsed advances through final generation and freezes only at the Run end', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const run = (await app.post('/api/runs/prepare', {
      job_id: app.manifest.jobs[0].id,
    })) as { id: string };
    let status = 'generating';
    await page.clock.setFixedTime('2026-11-15T10:02:00Z');
    await page.route(`**/api/runs/${run.id}`, async (route) => {
      const record = await (await route.fetch()).json();
      await route.fulfill({
        json: {
          ...record,
          status,
          execution_started: '2026-11-15T10:00:00Z',
          finished: status === 'completed' ? '2026-11-15T10:03:00Z' : undefined,
          stages: [
            {
              name: 'train',
              status: 'completed',
              started: '2026-11-15T10:00:10Z',
              finished: '2026-11-15T10:01:00Z',
            },
          ],
        },
      });
    });
    await page.goto(`${app.url}/#/runs/${run.id}`);
    const elapsed = page.getByLabel('Run elapsed');
    await expect(elapsed).toContainText('02:00');
    await page.clock.setFixedTime('2026-11-15T10:02:30Z');
    await expect(elapsed).toContainText('02:30');
    await page.reload();
    await expect(elapsed).toContainText('02:30');
    status = 'completed';
    await page.reload();
    await expect(elapsed).toContainText('03:00');
    await page.clock.setFixedTime('2026-11-15T11:00:00Z');
    await expect(elapsed).toContainText('03:00');
    status = 'interrupted';
    await page.reload();
    await expect(elapsed).toContainText('unknown (execution end unconfirmed)');
  } finally {
    await app.close();
  }
});

test('whole-Run forecast refreshes after a measured preview independently of Phase ETA', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const run = (await app.post('/api/runs/prepare', {
      job_id: app.manifest.jobs[0].id,
    })) as { id: string };
    let measured = false;
    let ended = false;
    await page.route(`**/api/runs/${run.id}`, async (route) => {
      const record = await (await route.fetch()).json();
      await route.fulfill({
        json: {
          ...record,
          status: ended ? 'completed' : 'training',
          execution_started: '2026-11-15T10:00:00Z',
        },
      });
    });
    await page.route(`**/api/runs/${run.id}/logs`, (route) =>
      route.fulfill({
        json: {
          text: '',
          progress: {
            step: 2,
            total: 20,
            elapsed: '10:20',
            eta: '1:33:00',
            eta_scope: 'phase',
            loss: null,
          },
          run_estimate: {
            remaining_seconds: measured ? 5580 : null,
            finish_at: measured ? '2026-11-15T11:35:00Z' : null,
            completed_previews: measured ? 1 : 0,
            outstanding_previews: measured ? 9 : 10,
            outstanding_final_samples: 0,
            unknown_components: [
              'checkpoint/state saving and stage transitions',
            ],
            preview_remaining_seconds: measured ? 5400 : null,
          },
        },
      }),
    );
    await page.goto(`${app.url}/#/runs/${run.id}`);
    const forecast = page.getByLabel('Run completion estimate');
    await expect(forecast).toContainText('Estimated Run remaining: unknown');
    measured = true;
    await expect(forecast).toContainText('Estimated Run remaining: 1h 33m 0s');
    await expect(forecast).toContainText('1 completed · 9 outstanding');
    await expect(forecast).toContainText(
      'Unestimated or assumed: checkpoint/state saving',
    );
    await page.reload();
    await expect(forecast).toContainText('Estimated Run remaining: 1h 33m 0s');
    ended = true;
    await page.reload();
    await expect(forecast).toHaveCount(0);
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
