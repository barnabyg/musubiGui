import { test, expect } from '@playwright/test';
import { runWorkspace } from './runHarness';

test('a delayed history refresh cannot replace live training with the start response', async ({
  page,
}) => {
  const app = await runWorkspace();
  let releaseFirst = () => {};
  let releaseSecond = () => {};
  let releaseStart = () => {};
  const firstGate = new Promise<void>((resolve) => (releaseFirst = resolve));
  const secondGate = new Promise<void>((resolve) => (releaseSecond = resolve));
  const startGate = new Promise<void>((resolve) => (releaseStart = resolve));
  let firstSeen = () => {};
  let secondSeen = () => {};
  const firstRequest = new Promise<void>((resolve) => (firstSeen = resolve));
  const secondRequest = new Promise<void>((resolve) => (secondSeen = resolve));
  try {
    const job = app.manifest.jobs[0];
    const saved = (await (
      await page.request.get(`${app.url}/api/jobs/${job.id}`)
    ).json()) as { config: Record<string, unknown> } & Record<string, unknown>;
    const changed = await page.request.put(`${app.url}/api/jobs/${job.id}`, {
      data: {
        ...saved,
        version: undefined,
        id: undefined,
        config: { ...saved.config, steps: 50, save_every: 50 },
      },
    });
    expect(changed.ok()).toBe(true);
    const run = (await app.post('/api/runs/prepare', { job_id: job.id })) as {
      id: string;
    };
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    let requests = 0;
    let trainingSummary = '';
    await page.route('**/api/runs/summaries', async (route) => {
      requests += 1;
      if (requests === 1) {
        firstSeen();
        await firstGate;
        const response = await route.fetch();
        trainingSummary = await response.text();
        await route.fulfill({ response, body: trainingSummary });
        return;
      } else {
        if (requests === 2) {
          secondSeen();
          await secondGate;
        }
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: trainingSummary,
        });
        return;
      }
    });
    await page.route(`**/api/runs/${run.id}/start`, async (route) => {
      const response = await route.fetch();
      const pending = (await response.json()) as { status: string };
      expect(pending.status).toBe('pending_training');
      await startGate;
      await route.fulfill({ response });
    });
    await firstRequest;
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await app.records())
            .find((record) => record.id === run.id)
            ?.stages.find((stage) => stage.name === 'train')?.status,
      )
      .toBe('running');
    await expect
      .poll(async () => {
        const logs = (await (
          await page.request.get(`${app.url}/api/runs/${run.id}/logs`)
        ).json()) as { progress: { step: number } | null };
        return logs.progress?.step ?? 0;
      })
      .toBeGreaterThanOrEqual(1);
    releaseFirst();
    await expect(
      page.getByText('State: Training', { exact: true }),
    ).toBeVisible();
    const progress = page.getByLabel('Training steps');
    await expect(progress).toHaveAttribute('max', '50');
    const firstStep = Number(await progress.getAttribute('value'));
    releaseStart();
    await secondRequest;
    releaseSecond();
    await expect(
      page.getByRole('button', { name: 'Request graceful stop', exact: true }),
    ).toBeEnabled();
    await expect(
      page.getByText('State: Training', { exact: true }),
    ).toBeVisible();
    await expect
      .poll(async () => Number(await progress.getAttribute('value')))
      .toBeGreaterThan(firstStep);
  } finally {
    releaseFirst();
    releaseSecond();
    releaseStart();
    await page.unrouteAll({ behavior: 'wait' });
    await app.close();
  }
});

test('finished Runs transfer summaries without repeating frozen inputs or terminal logs', async ({
  page,
}, testInfo) => {
  const app = await runWorkspace();
  try {
    for (const job of app.manifest.jobs) {
      const run = (await app.post('/api/runs/prepare', {
        job_id: job.id,
        start: true,
      })) as { id: string };
      await expect
        .poll(
          async () =>
            (await app.records()).find((record) => record.id === run.id)
              ?.status,
          { timeout: 240_000 },
        )
        .toBe('completed');
    }
    const [run] = await app.records();
    const traffic: { path: string; bytes: number }[] = [];
    page.on('response', async (response) => {
      const url = new URL(response.url());
      if (url.pathname.startsWith('/api/runs')) {
        const body = await response.body().catch(() => Buffer.alloc(0));
        traffic.push({ path: url.pathname, bytes: body.length });
      }
    });
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel('Run logs')).toContainText('step');
    traffic.length = 0;
    // A bounded observation window measures idle traffic, rather than waiting for app state.
    await new Promise((resolve) => setTimeout(resolve, 3250));
    await testInfo.attach('steady-state-traffic', {
      body: JSON.stringify(traffic, null, 2),
      contentType: 'application/json',
    });
    expect(traffic.filter((entry) => entry.path === '/api/runs')).toHaveLength(
      0,
    );
    expect(
      traffic.filter((entry) => entry.path.endsWith('/logs')),
    ).toHaveLength(0);
    expect(
      traffic.filter((entry) => entry.path === `/api/runs/${run.id}`),
    ).toHaveLength(0);
    expect(
      traffic.filter((entry) => entry.path === '/api/runs/summaries').length,
    ).toBeGreaterThan(0);

    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const saved = (await (
      await page.request.get(`${app.url}/api/jobs/${job.id}`)
    ).json()) as {
      config: Record<string, unknown>;
    } & Record<string, unknown>;
    const changed = await page.request.put(`${app.url}/api/jobs/${job.id}`, {
      data: {
        ...saved,
        version: undefined,
        id: undefined,
        config: { ...saved.config, steps: 12, save_every: 12 },
      },
    });
    expect(changed.ok()).toBe(true);
    const active = (await app.post('/api/runs/prepare', {
      job_id: job.id,
      start: true,
    })) as { id: string };
    const activeButton = page.getByRole('button', {
      name: `${job.name} · WAN · r2`,
      exact: true,
    });
    await expect(activeButton).toBeVisible();
    traffic.length = 0;
    await new Promise((resolve) => setTimeout(resolve, 3250));
    await testInfo.attach('other-active-run-traffic', {
      body: JSON.stringify(traffic, null, 2),
      contentType: 'application/json',
    });
    expect(traffic.filter((entry) => entry.path === '/api/runs')).toHaveLength(
      0,
    );
    expect(
      traffic.filter((entry) => entry.path.endsWith('/logs')),
    ).toHaveLength(0);
    expect(
      traffic.filter((entry) => entry.path === `/api/runs/${active.id}`),
    ).toHaveLength(0);
    await activeButton.click();
    await expect(page.getByLabel('Training progress')).toContainText(
      /Step \d+\/12/,
      { timeout: 240_000 },
    );
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await expect(
      page.getByRole('link', { name: 'Download final LoRA', exact: true }),
    ).toBeVisible();
    await expect(page).toHaveTitle(`Finished: ${job.name} — Musubi`);
  } finally {
    await app.close();
  }
});

test('slow summary responses never accumulate overlapping history polls', async ({
  page,
}) => {
  const app = await runWorkspace();
  let requests = 0;
  let concurrent = 0;
  let peak = 0;
  try {
    const run = (await app.post('/api/runs/prepare', {
      job_id: app.manifest.jobs[0].id,
      start: true,
    })) as { id: string };
    await page.route('**/api/runs/summaries', async (route) => {
      concurrent += 1;
      peak = Math.max(peak, concurrent);
      requests += 1;
      try {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        await route.continue();
      } finally {
        concurrent -= 1;
      }
    });
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await expect.poll(() => requests).toBeGreaterThanOrEqual(3);
    expect(peak).toBe(1);
    await page.unrouteAll({ behavior: 'wait' });
  } finally {
    await app.close();
  }
});
