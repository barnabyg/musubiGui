import { test, expect } from '@playwright/test';
import { rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { runWorkspace, type RunRecord } from './runHarness';

test('queued training cancellation and frozen-input retry describe the work they perform', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const original = (await app.post('/api/runs/prepare', {
      job_id: job.id,
    })) as RunRecord;
    await expect
      .poll(
        async () =>
          (await app.records()).find((run) => run.id === original.id)?.status,
        { timeout: 240_000 },
      )
      .toBe('prepared');
    await app.leaseGpu('acquire');
    await page.goto(`${app.url}/#/runs/${original.id}`);
    await page
      .getByRole('button', { name: 'Add to queue', exact: true })
      .click();
    await expect(
      page.getByText('State: Waiting to train', { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(
        'Remove this Run from the training queue. Training will not start; prepared files remain available.',
      ),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Cancel queued training', exact: true })
      .click();
    await expect(
      page.getByText('State: Cancelled', { exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel('Retry frozen inputs')).toContainText(
      'Training will not start automatically',
    );
    await app.leaseGpu('release');
    await page
      .getByRole('button', {
        name: 'Prepare new Run from frozen inputs',
        exact: true,
      })
      .click();
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const retry = (await app.records()).find((run) => run.id !== original.id)!;
    expect(retry.job).toEqual(original.job);
    expect(retry.manifest).toEqual(original.manifest);
    expect(retry.stages.find((stage) => stage.name === 'train')?.status).toBe(
      'pending',
    );
    await writeFile(path.join(app.fixture, 'checkout', 'fail-train'), '');
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(page.getByText('State: Failed', { exact: true })).toBeVisible({
      timeout: 240_000,
    });
    const explanation = page.getByLabel('Retry frozen inputs');
    await expect(explanation).toContainText(
      'new Run using this Run’s frozen job revision and dataset inputs',
    );
    await expect(explanation).toContainText(
      'Only verified matching latent and text caches are eligible for reuse',
    );
    await expect(explanation).toContainText(
      'Training and sampling run again after you start training',
    );
    await expect(explanation).toContainText(
      'edit the job, save and review it, then prepare from Jobs',
    );
    await page
      .getByRole('button', {
        name: 'Prepare new Run from frozen inputs',
        exact: true,
      })
      .click();
    await expect(
      page.getByText('State: Ready to start', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const records = await app.records();
    expect(records).toHaveLength(3);
    expect(
      records.every((run) => run.job.revision === original.job.revision),
    ).toBe(true);
    expect(
      records
        .find((run) => ![original.id, retry.id].includes(run.id))
        ?.stages.find((stage) => stage.name === 'train')?.status,
    ).toBe('pending');
    await rm(path.join(app.fixture, 'checkout', 'fail-train'));
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const completedUrl = page.url();
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    const inspect = page.getByRole('button', {
      name: 'Review configuration',
      exact: true,
    });
    await inspect.click();
    await expect(inspect).toBeEnabled();
    const results = page.getByRole('button', {
      name: 'View results',
      exact: true,
    });
    await expect(results).toBeVisible();
    await results.click();
    await expect(page).toHaveURL(completedUrl);
  } finally {
    await app.close();
  }
});
