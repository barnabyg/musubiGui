import { test, expect } from '@playwright/test';
import { runWorkspace } from './runHarness';

for (const family of ['wan', 'h3'] as const) {
  test(`${family} sample settings have one editing location and persist independently enabled previews`, async ({
    page,
  }) => {
    const app = await runWorkspace();
    try {
      const job = app.manifest.jobs.find((item) => item.family === family)!;
      await page.goto(`${app.url}/#/jobs/${job.id}`);
      const samples = page.getByRole('region', {
        name: 'Samples configuration',
      });
      await expect(samples).toBeVisible();
      await samples
        .getByLabel('Sample prompt', { exact: true })
        .fill('A blue square moves.');
      await samples.getByLabel('Seed', { exact: true }).fill('123');
      await samples
        .getByLabel('Sample inference steps', { exact: true })
        .fill('8');
      await samples
        .getByLabel('Preview every N steps', { exact: true })
        .fill('1');
      await samples
        .getByRole('checkbox', { name: 'Generate a final sample', exact: true })
        .uncheck();
      await page
        .getByRole('navigation', { name: 'Job sections' })
        .getByRole('button', { name: 'Training', exact: true })
        .click();
      await page
        .getByRole('navigation', { name: 'Job sections' })
        .getByRole('button', { name: 'Samples', exact: true })
        .click();
      await expect(
        samples.getByLabel('Sample prompt', { exact: true }),
      ).toHaveValue('A blue square moves.');
      await page
        .getByRole('button', { name: 'Save draft', exact: true })
        .click();
      await expect(
        page.getByText('Draft saved — revision 2', { exact: true }),
      ).toBeVisible();
      await page.reload();
      await expect(samples.getByLabel('Seed', { exact: true })).toHaveValue(
        '123',
      );
      await expect(
        samples.getByLabel('Sample inference steps', { exact: true }),
      ).toHaveValue('8');
      await expect(
        samples.getByLabel('Preview every N steps', { exact: true }),
      ).toHaveValue('1');
      await expect(
        samples.getByRole('checkbox', {
          name: 'Generate a final sample',
          exact: true,
        }),
      ).not.toBeChecked();
      for (const name of [
        'Seed',
        'Sample inference steps',
        'Preview every N steps',
        'Sample prompt',
      ])
        await expect(page.getByLabel(name, { exact: true })).toHaveCount(1);
      await samples
        .getByLabel('Preview every N steps', { exact: true })
        .fill('0');
      await samples
        .getByRole('checkbox', { name: 'Generate a final sample', exact: true })
        .check();
      await expect(
        samples.getByLabel('Sample prompt', { exact: true }),
      ).toHaveValue('A blue square moves.');
      await page
        .getByRole('button', { name: 'Review configuration', exact: true })
        .click();
      await expect(page.getByLabel('Configuration review')).toContainText(
        'Review passed',
      );
    } finally {
      await app.close();
    }
  });
}
