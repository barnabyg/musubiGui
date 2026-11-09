import { test, expect, type Page } from '@playwright/test';
import { runWorkspace } from './runHarness';

async function failFirstRead(page: Page, url: string, message: string) {
  let requests = 0;
  let concurrent = 0;
  let peak = 0;
  await page.route(url, async (route) => {
    requests += 1;
    concurrent += 1;
    peak = Math.max(peak, concurrent);
    try {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      if (requests === 1)
        await route.fulfill({ status: 503, json: { detail: message } });
      else await route.continue();
    } finally {
      concurrent -= 1;
    }
  });
  return {
    get requests() {
      return requests;
    },
    get peak() {
      return peak;
    },
  };
}

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
    const traffic = await failFirstRead(
      page,
      `**/api/runs/${run.id}`,
      'Run detail temporarily unavailable.',
    );
    await choice.click();
    await expect(page.getByRole('alert')).toContainText(
      'Run detail temporarily unavailable.',
    );
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole('alert')).toHaveCount(0);
    await new Promise((resolve) => setTimeout(resolve, 3250));
    expect(traffic.requests).toBe(2);
    expect(traffic.peak).toBe(1);
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
    const traffic = await failFirstRead(
      page,
      `**/api/runs/${run.id}/logs`,
      'Run log temporarily unavailable.',
    );
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await expect(page.getByRole('alert')).toContainText(
      'Run log temporarily unavailable.',
    );
    await expect(page.getByLabel('Run logs')).toContainText('step');
    await expect(page.getByRole('alert')).toHaveCount(0);
    await new Promise((resolve) => setTimeout(resolve, 3250));
    expect(traffic.requests).toBe(2);
    expect(traffic.peak).toBe(1);
    await page.unrouteAll({ behavior: 'wait' });
  } finally {
    await app.close();
  }
});
