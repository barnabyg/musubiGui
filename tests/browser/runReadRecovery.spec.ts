import { test, expect } from '@playwright/test';
import { runWorkspace } from './runHarness';

test('an unchanged Run retries a failed first detail read and clears its read error', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs[0];
    const run = (await app.post('/api/runs/prepare', {
      job_id: job.id,
      start: true,
    })) as { id: string };
    await expect
      .poll(
        async () =>
          (await app.records()).find((record) => record.id === run.id)?.status,
        { timeout: 240_000 },
      )
      .toBe('completed');
    await page.goto(`${app.url}/#/runs`);
    const choice = page.getByRole('button', {
      name: `${job.name} · ${job.family.toUpperCase()} · r1`,
      exact: true,
    });
    await expect(choice).toBeVisible();
    let requests = 0;
    let concurrent = 0;
    let peak = 0;
    await page.route(`**/api/runs/${run.id}`, async (route) => {
      requests += 1;
      concurrent += 1;
      peak = Math.max(peak, concurrent);
      try {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        if (requests === 1)
          await route.fulfill({
            status: 503,
            json: { detail: 'Run detail temporarily unavailable.' },
          });
        else await route.continue();
      } finally {
        concurrent -= 1;
      }
    });
    await choice.click();
    await expect(page.getByRole('alert')).toContainText(
      'Run detail temporarily unavailable.',
    );
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole('alert')).toHaveCount(0);
    await new Promise((resolve) => setTimeout(resolve, 3250));
    expect(requests).toBe(2);
    expect(peak).toBe(1);
    await page.unrouteAll({ behavior: 'wait' });
  } finally {
    await app.close();
  }
});

test('a terminal log retries a failed read then stops after success and clears its error', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const run = (await app.post('/api/runs/prepare', {
      job_id: app.manifest.jobs[0].id,
      start: true,
    })) as { id: string };
    await expect
      .poll(
        async () =>
          (await app.records()).find((record) => record.id === run.id)?.status,
        { timeout: 240_000 },
      )
      .toBe('completed');
    let requests = 0;
    let concurrent = 0;
    let peak = 0;
    await page.route(`**/api/runs/${run.id}/logs`, async (route) => {
      requests += 1;
      concurrent += 1;
      peak = Math.max(peak, concurrent);
      try {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        if (requests === 1)
          await route.fulfill({
            status: 503,
            json: { detail: 'Run log temporarily unavailable.' },
          });
        else await route.continue();
      } finally {
        concurrent -= 1;
      }
    });
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await expect(page.getByRole('alert')).toContainText(
      'Run log temporarily unavailable.',
    );
    await expect(page.getByLabel('Run logs')).toContainText('step');
    await expect(page.getByRole('alert')).toHaveCount(0);
    await new Promise((resolve) => setTimeout(resolve, 3250));
    expect(requests).toBe(2);
    expect(peak).toBe(1);
    await page.unrouteAll({ behavior: 'wait' });
  } finally {
    await app.close();
  }
});
