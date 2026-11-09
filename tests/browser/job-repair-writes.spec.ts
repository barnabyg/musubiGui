import { expect, test, type Page } from '@playwright/test';
import { copyFile, readFile, unlink } from 'node:fs/promises';
import path from 'node:path';
import { runWorkspace } from './runHarness';

async function delayRequest(page: Page, pattern: string) {
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(pattern, async (route) => {
    await gate;
    await route.continue();
  });
  return release;
}

async function openBulkRepair(
  page: Page,
  app: Awaited<ReturnType<typeof runWorkspace>>,
) {
  const job = app.manifest.jobs.find((entry) => entry.family === 'wan')!;
  const sidecar = path.join(app.fixture, 'datasets', 'wan', 'motion.txt');
  await unlink(sidecar);
  await page.goto(`${app.url}/#/jobs/${job.id}`);
  await page
    .getByRole('button', { name: 'Review configuration', exact: true })
    .click();
  const review = page.getByRole('region', { name: 'Configuration review' });
  await review.getByRole('link', { name: /motion.mp4: no caption/ }).click();
  await page.getByRole('button', { name: 'Bulk actions', exact: true }).click();
  await page.getByLabel('Select motion.mp4', { exact: true }).check();
  await page
    .getByText('Selection and bulk caption actions', { exact: true })
    .click();
  return { job, sidecar, review };
}

test('returning to a job waits for a shared exclusion and reviews the updated dataset', async ({
  page,
}) => {
  const app = await runWorkspace();
  const release = await delayRequest(page, '**/exclusions');
  try {
    const { job, review } = await openBulkRepair(page, app);
    const posted = page.waitForRequest('**/exclusions');
    await page.getByRole('button', { name: 'Exclude selection' }).click();
    await posted;
    const back = page.getByRole('button', { name: 'Return to job and review' });
    await back.click();
    expect(await back.isDisabled()).toBe(true);
    release();
    await expect(review).toContainText('no valid, included media remain.');
    await expect(page).toHaveURL(`${app.url}/#/jobs/${job.id}`);
  } finally {
    release();
    await app.close();
  }
});

test('leaving a pending bulk caption repair via Jobs waits before preparation and stays on Jobs', async ({
  page,
}) => {
  const app = await runWorkspace();
  const release = await delayRequest(page, '**/bulk/apply');
  try {
    const { job, sidecar } = await openBulkRepair(page, app);
    await page
      .getByLabel('Caption prefix', { exact: true })
      .fill('A shared caption');
    await page.getByRole('button', { name: 'Preview caption changes' }).click();
    const posted = page.waitForRequest('**/bulk/apply');
    await page
      .getByRole('button', { name: 'Apply previewed captions' })
      .click();
    await posted;
    await page.getByRole('button', { name: 'Jobs', exact: true }).click();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await expect(page.getByText('Review passed for revision 1')).toBeVisible();
    const prepare = page.getByRole('button', {
      name: 'Prepare run',
      exact: true,
    });
    await expect(prepare).toBeDisabled();
    await expect(
      page.getByRole('button', { name: 'Prepare and start', exact: true }),
    ).toBeDisabled();
    release();
    await expect(prepare).toBeEnabled();
    await expect(page).toHaveURL(`${app.url}/#/jobs/${job.id}`);
    expect(await readFile(sidecar, 'utf8')).toBe('A shared caption');
  } finally {
    release();
    await app.close();
  }
});

test('return waits for an uploaded audio sidecar to finish validation even when uploads are paused', async ({
  page,
}) => {
  const app = await runWorkspace();
  const release = await delayRequest(page, '**/uploads/*/complete');
  try {
    const job = app.manifest.jobs.find((entry) => entry.family === 'h3')!;
    const root = path.join(app.fixture, 'datasets');
    const dataset = (await app.post('/api/datasets/managed', {
      name: 'Audio repair',
      root,
    })) as { id: string; path: string };
    await copyFile(
      path.join(root, 'h3', 'motion.mp4'),
      path.join(dataset.path, 'motion.mp4'),
    );
    await app.post(`/api/datasets/${dataset.id}/scan?wait=true`);
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    await page
      .getByRole('combobox', { name: 'Add a dataset' })
      .selectOption(dataset.id);
    await page
      .getByRole('button', { name: 'Add dataset', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const review = page.getByRole('region', { name: 'Configuration review' });
    await review
      .getByRole('link', {
        name: /Audio repair.*motion.mp4: audio policy requires/,
      })
      .click();
    await page.getByRole('button', { name: 'Add media', exact: true }).click();
    await page
      .getByLabel('Upload files')
      .setInputFiles(path.join(root, 'h3', 'motion.wav'));
    const completing = page.waitForRequest('**/uploads/*/complete');
    await page.getByRole('button', { name: 'Upload selected files' }).click();
    await completing;
    await page.getByRole('button', { name: 'Pause uploads' }).click();
    const back = page.getByRole('button', { name: 'Return to job and review' });
    await back.click();
    expect(await back.isDisabled()).toBe(true);
    release();
    await expect(review).toContainText('Review passed', { timeout: 240_000 });
    await expect(review).toContainText('2 supervised / 2 included');
  } finally {
    release();
    await app.close();
  }
});
