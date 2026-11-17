import { expect, test } from '@playwright/test';
import { runWorkspace } from './runHarness';

test('contextual help preserves the draft and run notifications persist in Settings', async ({
  page,
  context,
}) => {
  const app = await runWorkspace();
  try {
    await page.goto(`${app.url}/#/runs`);
    const notifications = page.getByRole('checkbox', {
      name: /browser notification/,
    });
    await expect(notifications).toHaveCount(0);
    await page
      .getByRole('button', { name: 'Background training', exact: true })
      .click();
    const help = page.getByRole('dialog', { name: 'Help', exact: true });
    await expect(
      help.getByRole('heading', { name: 'Background training' }),
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(help).toBeHidden();
    await context.grantPermissions(['notifications'], { origin: app.url });
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await notifications.check();
    await page.reload();
    await expect(notifications).toBeChecked();
    await notifications.uncheck();
    await page.goto(`${app.url}/#/jobs/${app.manifest.jobs[0].id}`);
    const name = page.getByLabel('Job name', { exact: true });
    await name.fill('Unfinished edit');
    await page.getByRole('button', { name: 'Help', exact: true }).click();
    await help.getByLabel('Help topic').selectOption({ label: 'Model paths' });
    await expect(
      help.getByRole('heading', { name: 'Model paths' }),
    ).toBeVisible();
    await help.getByRole('button', { name: 'Close help' }).click();
    await expect(name).toHaveValue('Unfinished edit');
  } finally {
    await app.close();
  }
});
