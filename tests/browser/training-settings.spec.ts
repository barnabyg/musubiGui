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

test('Wan timestep choices persist while H3 explains its fixed Uniform strategy', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const wan = app.manifest.jobs.find((job) => job.family === 'wan')!;
    await page.goto(`${app.url}/#/jobs/${wan.id}`);
    await page.getByText('Advanced controls', { exact: true }).click();
    const strategy = page.getByRole('combobox', {
      name: 'Timestep strategy',
      exact: true,
    });
    await expect(strategy).toHaveValue('shift');
    await strategy.selectOption('sigmoid');
    await page.getByLabel('Sigmoid scale', { exact: true }).fill('1.5');
    await expect(page.getByLabel('Flow shift', { exact: true })).toBeHidden();
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('revision 2');
    await page.reload();
    await page.getByText('Advanced controls', { exact: true }).click();
    await expect(strategy).toHaveValue('sigmoid');
    await expect(page.getByLabel('Sigmoid scale', { exact: true })).toHaveValue(
      '1.5',
    );
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const review = page.getByRole('region', { name: 'Configuration review' });
    await expect(review).toContainText('Review passed', { timeout: 240_000 });
    await expect(review).toContainText('Timestep strategy: sigmoid');
    await expect(review).toContainText('Sigmoid scale: 1.5');
    const h3 = app.manifest.jobs.find((job) => job.family === 'h3')!;
    await page.goto(`${app.url}/#/jobs/${h3.id}`);
    await expect(strategy).toHaveValue('uniform');
    await expect(strategy).toBeDisabled();
    await expect(
      page.getByText(
        'The pinned H3 trainer supports Uniform only, sharing one base draw between video and audio.',
      ),
    ).toBeVisible();
  } finally {
    await app.close();
  }
});
