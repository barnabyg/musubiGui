import { expect, test } from '@playwright/test';
import { runWorkspace } from './runHarness';

test('dataset resolutions survive saving and preparation while sample geometry stays independent', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const wan = app.manifest.jobs.find((job) => job.family === 'wan')!;
    const h3 = app.manifest.jobs.find((job) => job.family === 'h3')!;
    await page.goto(`${app.url}/#/jobs/${wan.id}`);
    await page
      .getByRole('combobox', { name: 'Add a dataset' })
      .selectOption(h3.dataset_id);
    await page
      .getByRole('button', { name: 'Add dataset', exact: true })
      .click();
    const preparation = page.locator('#job-section-dataset');
    await page
      .getByRole('button', {
        name: 'Set training resolutions in Dataset preparation',
        exact: true,
      })
      .click();
    await expect(
      page.getByLabel('Default training width', { exact: true }),
    ).toBeFocused();
    const rows = preparation.getByRole('group');
    await expect(rows).toHaveCount(2);
    await expect(rows.first()).toContainText(
      'Effective training resolution: 256 × 256 · default',
    );
    await preparation
      .getByLabel('Default training width', { exact: true })
      .fill('288');
    await expect(rows.first()).toContainText('288 × 256 · default');
    await expect(rows.last()).toContainText('288 × 256 · default');
    for (const [index, name, width, height] of [
      [0, 'WAN acceptance', '320', '256'],
      [1, 'H3 acceptance', '256', '320'],
    ] as const) {
      await expect(rows.nth(index)).toContainText('default');
      await page
        .getByRole('checkbox', {
          name: `Use default training resolution for ${name}`,
          exact: true,
        })
        .uncheck();
      await page
        .getByRole('spinbutton', {
          name: `Training width for ${name}`,
          exact: true,
        })
        .fill(width);
      await page
        .getByRole('spinbutton', {
          name: `Training height for ${name}`,
          exact: true,
        })
        .fill(height);
    }
    await expect(rows.first()).toContainText('320 × 256 · custom');
    await expect(rows.last()).toContainText('256 × 320 · custom');
    await preparation
      .getByLabel('Default training height', { exact: true })
      .fill('288');
    await expect(rows.first()).toContainText('320 × 256 · custom');
    await expect(rows.last()).toContainText('256 × 320 · custom');
    const inherit = rows.last().getByRole('checkbox');
    await inherit.check();
    await expect(rows.last()).toContainText('288 × 288 · default');
    await inherit.uncheck();
    await rows
      .last()
      .getByRole('spinbutton', {
        name: 'Training width for H3 acceptance',
        exact: true,
      })
      .fill('256');
    await rows
      .last()
      .getByRole('spinbutton', {
        name: 'Training height for H3 acceptance',
        exact: true,
      })
      .fill('320');
    const samples = page.getByRole('region', { name: 'Samples configuration' });
    await expect(
      samples.getByLabel('Sample width', { exact: true }),
    ).toHaveValue('288');
    await samples.getByLabel('Sample width', { exact: true }).fill('256');
    await samples.getByLabel('Sample height', { exact: true }).fill('256');
    await expect(rows.first()).toContainText('320 × 256 · custom');
    const saved = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/jobs/${wan.id}`) &&
        response.request().method() === 'PUT',
    );
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    expect((await saved).ok()).toBe(true);
    await page.reload();
    await page
      .getByRole('button', { name: 'Dataset preparation', exact: true })
      .click();
    await expect(
      page.getByRole('spinbutton', {
        name: 'Training width for WAN acceptance',
        exact: true,
      }),
    ).toHaveValue('320');
    const width = page.getByRole('spinbutton', {
      name: 'Training width for WAN acceptance',
      exact: true,
    });
    await width.fill('65');
    await expect(width).toHaveAttribute('aria-invalid', 'true');
    await page.getByText('Advanced controls', { exact: true }).click();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await page
      .getByRole('button', {
        name: 'Dataset "WAN acceptance": resolution width must be at least 64 and a multiple of 16.',
        exact: true,
      })
      .click();
    await expect(width).toBeFocused();
    await width.fill('320');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const review = page.getByRole('region', { name: 'Configuration review' });
    await expect(review).toContainText('Review passed', { timeout: 240_000 });
    await expect(
      page.getByRole('table', { name: 'Training sections' }),
    ).toContainText('320 × 256');
    await expect(
      page.getByRole('table', { name: 'Training sections' }),
    ).toContainText('256 × 320');
    await expect(review).toContainText('Sample output: 256 × 256');
    await page
      .getByRole('button', { name: 'Prepare run', exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await app.records()).some((run) => run.status === 'prepared'),
        { timeout: 240_000 },
      )
      .toBe(true);
  } finally {
    await app.close();
  }
});
