import { test, expect } from '@playwright/test';
import path from 'node:path';
import { access } from 'node:fs/promises';
import { runWorkspace, type RunRecord } from './runHarness';

test('Run and output folders keep readable frozen Job names without collisions', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const jobs = app.manifest.jobs.filter((job) => job.family === 'wan');
    const records: RunRecord[] = [];
    for (const job of jobs.slice(0, 2)) {
      await page.goto(`${app.url}/#/jobs/${job.id}`);
      await page.getByLabel('Job name', { exact: true }).fill('Summer / test');
      await page
        .getByRole('button', { name: 'Save draft', exact: true })
        .click();
      await page
        .getByRole('button', { name: 'Review configuration', exact: true })
        .click();
      const review = page.getByLabel('Configuration review');
      await review.getByText('Advanced', { exact: true }).click();
      await expect(review).toContainText('Summer---test-{unique}');
      await page
        .getByRole('button', { name: 'Prepare run', exact: true })
        .click();
      await expect(
        page.getByRole('button', { name: 'Start training', exact: true }),
      ).toBeEnabled({ timeout: 240_000 });
      const record = (await app.records()).find(
        (item) => item.job.id === job.id,
      )!;
      expect(path.basename(record.directory)).toMatch(
        /^Summer---test-[a-f0-9]{32}$/,
      );
      records.push(record);
    }
    expect(records[0].directory).not.toBe(records[1].directory);
    await page.goto(`${app.url}/#/runs/${records[0].id}`);
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    const download = page.getByRole('link', {
      name: 'Download final LoRA',
      exact: true,
    });
    await expect(download).toBeVisible({ timeout: 240_000 });
    const href = await download.getAttribute('href');
    expect((await fetch(app.url + href)).ok).toBe(true);
    await page.goto(`${app.url}/#/jobs/${jobs[0].id}`);
    await page.getByLabel('Job name', { exact: true }).fill('Renamed Job');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(
      page.getByText('Draft saved — revision 3', { exact: true }),
    ).toBeVisible();
    await page.goto(`${app.url}/#/runs/${records[0].id}`);
    await expect(
      page.getByRole('heading', { name: 'Summer / test', exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(`Output: ${records[0].directory}`, { exact: true }),
    ).toBeVisible();
    await access(records[0].directory);
    expect((await fetch(app.url + href)).ok).toBe(true);
  } finally {
    await app.close();
  }
});
