import { test, expect } from '@playwright/test';
import path from 'node:path';
import { access, readdir } from 'node:fs/promises';
import { openRunDiagnostics, runWorkspace, type RunRecord } from './runHarness';

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
      await review.getByText('Configuration details', { exact: true }).click();
      await expect(review).toContainText('Summer---test-{timestamp}');
      await page
        .getByRole('button', { name: 'Prepare only', exact: true })
        .click();
      await expect(
        page.getByRole('button', { name: 'Start training', exact: true }),
      ).toBeEnabled({ timeout: 240_000 });
      const record = (await app.records()).find(
        (item) => item.job.id === job.id,
      )!;
      expect(path.basename(record.directory)).toMatch(
        /^Summer---test-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}Z(?:-\d+)?$/,
      );
      records.push(record);
    }
    expect(records[0].directory).not.toBe(records[1].directory);
    await page.goto(`${app.url}/#/runs/${records[0].id}`);
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    await openRunDiagnostics(page);
    await expect(page.getByLabel('Output folder', { exact: true })).toHaveText(
      records[0].directory,
    );
    const directory = records[0].directory;
    const entries = await readdir(directory, { withFileTypes: true });
    expect(
      entries
        .filter((entry) => entry.isFile())
        .map((entry) => entry.name)
        .sort(),
    ).toEqual([
      'Summer---test-r2-step00000001.safetensors',
      'Summer---test-r2.safetensors',
    ]);
    expect(
      entries
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name)
        .sort(),
    ).toEqual(['config', 'logs', 'media', 'samples', 'state']);
    await access(
      path.join(directory, 'state', 'Summer---test-r2-state', 'scheduler.bin'),
    );
    await access(path.join(directory, 'config', 'dataset.toml'));
    await access(path.join(directory, 'logs', 'train.log'));
    const response = await page.request.get(
      `${app.url}/api/runs/${records[0].id}/outputs`,
    );
    expect(response.ok()).toBe(true);
    const { outputs } = await response.json();
    expect(
      outputs.map((output: { path: string }) => output.path).sort(),
    ).toEqual([
      'Summer---test-r2-step00000001.safetensors',
      'Summer---test-r2.safetensors',
      'samples/sample.mp4',
    ]);
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
    await openRunDiagnostics(page);
    await expect(page.getByLabel('Output folder', { exact: true })).toHaveText(
      records[0].directory,
    );
    await access(records[0].directory);
  } finally {
    await app.close();
  }
});
