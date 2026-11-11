import { expect, test } from '@playwright/test';
import { runWorkspace } from './runHarness';

test('optimizer choice persists and appears in configuration review', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((job) => job.family === 'wan')!;
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    await page.getByText('Advanced controls', { exact: true }).click();
    const optimizer = page.getByRole('combobox', {
      name: 'Optimizer',
      exact: true,
    });
    await expect(optimizer).toHaveValue('AdamW');
    await optimizer.selectOption('AdamW8bit');
    await expect(
      page.getByText('Synthetic fixture: optimizer execution is simulated.'),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('revision 2');
    await page.reload();
    await page.getByText('Advanced controls', { exact: true }).click();
    await expect(optimizer).toHaveValue('AdamW8bit');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const review = page.getByRole('region', { name: 'Configuration review' });
    await expect(review).toContainText('Review passed', { timeout: 240_000 });
    await expect(review).toContainText('Optimizer: AdamW8bit');
  } finally {
    await app.close();
  }
});
