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
    const timing = page.getByLabel('Run timing', { exact: true });
    const elapsed = timing.getByLabel('Training elapsed');
    await expect(elapsed).toContainText('01:30');
    await expect(page.getByLabel('Run elapsed')).toContainText('02:30');
    await expect(timing).not.toContainText('Phase ETA');
    await expect(timing.getByLabel('Seconds per step')).toContainText(
      '83.20 s/step',
    );
    await expect(page.getByLabel('Training progress')).toContainText(
      'Step 6/8 · Loss 0.5',
    );
    await page.reload();
    await expect(elapsed).toContainText('01:30');
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
    await expect(elapsed).toContainText('01:30');
    await expect(timing).not.toContainText('Training ETA');
    await expect(timing.getByLabel('Seconds per step')).toContainText(
      'Unknown',
    );
    failedDuringLaunch = true;
    await page.reload();
    await expect(elapsed).toContainText('02:00');
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
          execution_end_unknown: status === 'stopped',
          finished:
            status === 'completed'
              ? '2026-11-15T10:03:00Z'
              : status === 'stopped'
                ? '2026-11-15T11:00:00Z'
                : undefined,
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
    await expect(elapsed).toContainText('Unknown (execution end unconfirmed)');
    status = 'stopped';
    await page.reload();
    await expect(elapsed).toContainText('Unknown (execution end unconfirmed)');
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
    let finalUnmeasured = false;
    await page.route(`**/api/runs/${run.id}`, async (route) => {
      const record = await (await route.fetch()).json();
      await route.fulfill({
        json: {
          ...record,
          status: ended ? 'stopped' : 'training',
          execution_started: '2026-11-15T10:00:00Z',
          finished: ended ? '2026-11-15T10:20:00Z' : undefined,
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
            loss: '0.4',
            expert: 'high',
            phase_step: 1,
            phase_total: 5,
            expert_steps: { low: 5, high: 1 },
          },
          run_estimate: {
            remaining_seconds: measured && !finalUnmeasured ? 5580 : null,
            finish_at:
              measured && !finalUnmeasured ? '2026-11-15T11:35:00Z' : null,
            accounted_remaining_seconds: measured ? 5580 : null,
            accounted_finish_at: measured ? '2026-11-15T11:35:00Z' : null,
            completed_previews: measured ? 1 : 0,
            outstanding_previews: measured ? 9 : 10,
            outstanding_final_samples: 0,
            unknown_components: [
              'checkpoint/state saving and stage transitions',
              ...(finalUnmeasured ? ['final sampling'] : []),
            ],
            preview_remaining_seconds: measured ? 5400 : null,
          },
        },
      }),
    );
    await page.goto(`${app.url}/#/runs/${run.id}`);
    const timing = page.getByLabel('Run timing', { exact: true });
    const remaining = timing.getByLabel('Estimated Run remaining');
    await expect(remaining).toContainText('Unknown');
    await expect(timing.getByLabel('Phase ETA')).toContainText('1:33:00');
    await expect(
      page
        .getByLabel('Training progress')
        .getByText(/^Active expert: high-noise/),
    ).toContainText('Loss 0.4');
    await expect(timing).not.toContainText('Loss');
    await expect(timing).not.toContainText(
      /Previews:|Time estimates|Timings vary between executions/,
    );
    measured = true;
    await expect(remaining).toContainText('1h 33m 0s');
    await page.reload();
    await expect(remaining).toContainText('1h 33m 0s');
    finalUnmeasured = true;
    await expect(remaining).toContainText('1h 33m 0s (partial)');
    await expect(timing.getByLabel('Estimated finish')).toContainText(
      '(partial)',
    );
    await expect(timing.getByLabel('Outstanding preview budget')).toContainText(
      '1h 30m 0s',
    );
    await page.setViewportSize({ width: 360, height: 800 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    ended = true;
    await page.reload();
    await expect(timing).toBeVisible();
    await expect(remaining).toHaveCount(0);
    await expect(timing.getByLabel('Phase ETA')).toHaveCount(0);
    await expect(timing.getByLabel('Run elapsed')).toContainText('20:00');
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
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await expect(
      page.getByRole('button', { name: 'Copy output folder' }),
    ).toBeVisible();
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
