import { test, expect } from '@playwright/test';
import { runWorkspace } from './runHarness';

test('the URL keeps the screen and selection across navigation, refresh and browser history', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const other = app.manifest.jobs.find((item) => item.family === 'h3')!;
    const nav = (name: string) =>
      page.getByRole('button', { name, exact: true });
    const jobName = page.getByLabel('Job name', { exact: true });
    await page.goto(app.url);
    await nav('Jobs').click();
    await page
      .getByRole('button', { name: `${job.name} · WAN · r1`, exact: true })
      .click();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(jobName).toHaveValue(job.name);
    expect(new URL(page.url()).hash).toBe(`#/jobs/${job.id}`);

    // The sidebar opens collections; browser back reopens the selected job.
    await nav('Runs').click();
    await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible();
    await nav('Jobs').click();
    await expect(page.getByRole('table', { name: 'Saved jobs' })).toBeVisible();
    await expect(jobName).toHaveCount(0);
    await page
      .getByRole('button', { name: `${job.name} · WAN · r1`, exact: true })
      .click();
    await nav('Runs').click();
    await page.goBack();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(jobName).toHaveValue(job.name);
    await page.goForward();
    await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible();
    await page.goBack();
    await page.reload();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(jobName).toHaveValue(job.name);

    // Back moves between collection and detail pages too.
    await nav('Back to jobs').click();
    await page
      .getByRole('button', { name: `${other.name} · H3 · r1`, exact: true })
      .click();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(jobName).toHaveValue(other.name);
    await page.goBack();
    await expect(page.getByRole('table', { name: 'Saved jobs' })).toBeVisible();
    await page.goBack();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(jobName).toHaveValue(job.name);

    // Unsaved edits ask first, on back as on the sidebar.
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await page.getByLabel('Training steps', { exact: true }).fill('33');
    await page.goBack();
    const leave = page.getByRole('dialog', { name: 'Leave this job?' });
    await leave.getByRole('button', { name: 'Keep editing' }).click();
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveValue('33');
    expect(new URL(page.url()).hash).toBe(`#/jobs/${job.id}`);
    // Keeping the edits leaves history as it was, including what lay ahead.
    await page.goBack();
    await leave.getByRole('button', { name: 'Keep editing' }).click();
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveValue('33');
    await page.goBack();
    await leave.getByRole('button', { name: 'Discard changes' }).click();
    await expect(jobName).toHaveCount(0);
    // Discarding really leaves: the next step back doesn't return to the discarded job.
    await page.goForward();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(jobName).toHaveValue(job.name);
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).not.toHaveValue('33');
    await page.goBack();
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveCount(0);
    await nav('Settings').click();

    // A link to a Run opens it, and a stale link falls back to the list.
    const run = (await app.post('/api/runs/prepare', { job_id: job.id })) as {
      id: string;
    };
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await expect(
      page.getByText(`Run ${run.id.slice(0, 8)}`, { exact: true }),
    ).toBeVisible();
    await page.reload();
    await expect(
      page.getByText(`Run ${run.id.slice(0, 8)}`, { exact: true }),
    ).toBeVisible();
    await page.goto(`${app.url}/#/runs/${'0'.repeat(32)}`);
    await expect.poll(() => new URL(page.url()).hash).toBe('#/runs');

    // The selected dataset survives a refresh and the trip to another screen.
    await nav('Datasets').click();
    await page.getByRole('button', { name: /^WAN acceptance/ }).click();
    await expect(
      page.getByRole('button', { name: 'Back to datasets' }).first(),
    ).toBeVisible();
    expect(new URL(page.url()).hash).toMatch(/^#\/datasets\/\w+$/);
    await page.reload();
    await expect(
      page.getByRole('button', { name: 'Back to datasets' }).first(),
    ).toBeVisible();
    await page.goBack();
    await expect(
      page.getByRole('button', { name: /^WAN acceptance/ }),
    ).toBeVisible();
  } finally {
    await app.close();
  }
});

test('work finishing on one screen does not pull the user back to it', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const run = (await app.post('/api/runs/prepare', { job_id: job.id })) as {
      id: string;
    };
    await expect
      .poll(async () => (await app.records())[0]?.status, { timeout: 240_000 })
      .toBe('prepared');
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route(`**/api/runs/${run.id}/delete`, async (route) => {
      await held;
      await route.continue();
    });
    await page.goto(`${app.url}/#/runs/${run.id}`);
    await page.getByRole('button', { name: 'Delete run', exact: true }).click();
    await page
      .getByRole('dialog')
      .getByRole('button', { name: 'Delete run' })
      .click();
    await page.getByRole('button', { name: 'Jobs', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Jobs' })).toBeVisible();
    release();
    await expect.poll(async () => (await app.records()).length).toBe(0);
    await expect(page.getByRole('heading', { name: 'Jobs' })).toBeVisible();
    expect(new URL(page.url()).hash).toBe('#/jobs');
  } finally {
    await app.close();
  }
});
