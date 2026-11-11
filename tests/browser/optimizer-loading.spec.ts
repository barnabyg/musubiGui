import { expect, test } from '@playwright/test';
import { runWorkspace } from './runHarness';

test('jobs remain usable while optional optimizer detection is pending or fails', async ({
  page,
}) => {
  const app = await runWorkspace();
  let release = () => {};
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  try {
    await page.route('**/api/jobs/training-options', async (route) => {
      await pending;
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Probe unavailable' }),
      });
    });
    const job = app.manifest.jobs.find((job) => job.family === 'wan')!;
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    await expect(
      page.getByRole('textbox', { name: 'Job name', exact: true }),
    ).toHaveValue(job.name);
    await page.getByText('Advanced controls', { exact: true }).click();
    const optimizer = page.getByRole('combobox', {
      name: 'Optimizer',
      exact: true,
    });
    await expect(optimizer).toHaveValue('AdamW');
    release();
    await expect(
      page.getByText(
        'Optimizer availability could not be checked. AdamW remains available.',
      ),
    ).toBeVisible();
    await page
      .getByRole('textbox', { name: 'Job name', exact: true })
      .fill('Still editable');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('revision 2');
  } finally {
    release();
    await app.close();
  }
});
