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
    const rows = page
      .getByRole('list', { name: 'Datasets in this job' })
      .getByRole('listitem');
    await expect(
      rows.first().getByText('Training resolution', { exact: true }),
    ).toBeVisible();
    for (const [index, name, width, height] of [
      [0, 'WAN acceptance', '320', '256'],
      [1, 'H3 acceptance', '256', '320'],
    ] as const) {
      await rows
        .nth(index)
        .getByText('Training resolution', { exact: true })
        .click();
      await page
        .getByRole('checkbox', {
          name: `Use job resolution for ${name}`,
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
    const saved = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/jobs/${wan.id}`) &&
        response.request().method() === 'PUT',
    );
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    expect((await saved).ok()).toBe(true);
    await page.reload();
    await rows
      .first()
      .getByText('Training resolution', { exact: true })
      .click();
    await expect(
      page.getByRole('spinbutton', {
        name: 'Training width for WAN acceptance',
        exact: true,
      }),
    ).toHaveValue('320');
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
