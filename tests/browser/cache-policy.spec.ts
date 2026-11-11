import { expect, test } from '@playwright/test';
import { runWorkspace } from './runHarness';
import type { Job } from '../../frontend/jobTypes';

test('cache policies persist from the job form and review explains preparation', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const fixtureJob = app.manifest.jobs.find((job) => job.family === 'wan')!;
    await page.goto(`${app.url}/#/jobs/${fixtureJob.id}`);
    await page.getByText('Advanced controls', { exact: true }).click();
    const latent = page.getByRole('combobox', {
      name: 'Latent caching',
      exact: true,
    });
    const text = page.getByRole('combobox', {
      name: 'Text embedding caching',
      exact: true,
    });
    await expect(latent).toHaveValue('reuse');
    await expect(text).toHaveValue('reuse');
    await text.selectOption('rebuild');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('revision 2');
    await page.reload();
    await page.getByText('Advanced controls', { exact: true }).click();
    await expect(latent).toHaveValue('reuse');
    await expect(text).toHaveValue('rebuild');
    const saved = (await (
      await page.request.get(`${app.url}/api/jobs/${fixtureJob.id}`)
    ).json()) as Job;
    expect(saved.config.text_cache_policy).toBe('rebuild');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const review = page.getByRole('region', {
      name: 'Cache preparation policy',
    });
    await expect(review).toContainText('Latents: Reuse compatible cache');
    await expect(review).toContainText('Text embeddings: Rebuild cache');
    await expect(review).toContainText(
      'Preparation verifies compatible artifacts',
    );
    const prepared = (await app.post('/api/runs/prepare', {
      job_id: fixtureJob.id,
      reviewed_revision: saved.revision,
    })) as { id: string };
    await expect
      .poll(
        async () =>
          (await app.records()).find((run) => run.id === prepared.id)?.status,
        { timeout: 240_000 },
      )
      .toBe('prepared');
    await page.goto(`${app.url}/#/runs/${prepared.id}`);
    const result = page.getByRole('region', {
      name: 'Cache preparation results',
    });
    await expect(result).toContainText('Text embeddings: Cache rebuilt');
    await expect(result).toContainText('Rebuild requested by this Run.');
  } finally {
    await app.close();
  }
});
