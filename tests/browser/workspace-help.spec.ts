import { expect, test } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import type { Job, Review } from '../../frontend/jobTypes';
import { runWorkspace } from './runHarness';

test('clearing a model override restores the Settings path for display and review', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const wan = app.manifest.jobs.find((draft) => draft.family === 'wan')!;
    const settings = (await (
      await page.request.get(`${app.url}/api/settings`)
    ).json()) as { models: Record<string, string> };
    // Previously saved drafts may store an explicit empty-string override.
    const file = path.join(app.manifest.state, 'jobs', wan.id, '1.json');
    const legacy = JSON.parse(await readFile(file, 'utf8')) as Job;
    legacy.config.components.wan_low = '';
    await writeFile(file, JSON.stringify(legacy));
    const component = page.getByRole('textbox', { name: 'Wan low-noise DiT' });
    const openComponents = () =>
      page.getByText('Model components', { exact: true }).click();
    async function reviewedPath() {
      const response = page.waitForResponse(
        (response) =>
          response.url().endsWith(`/api/jobs/${wan.id}/review`) &&
          response.request().method() === 'POST',
      );
      await page
        .getByRole('button', { name: 'Review configuration', exact: true })
        .click();
      const review = (await (await response).json()) as Review;
      expect(review.errors).toEqual([]);
      return review.models.wan_low;
    }
    await page.goto(`${app.url}/#/jobs/${wan.id}`);
    await openComponents();
    await expect(component).toHaveAttribute('title', settings.models.wan_low);
    expect(await reviewedPath()).toBe(settings.models.wan_low);

    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await component.fill(settings.models.wan_high);
    await expect(page.getByText('Job override', { exact: true })).toBeVisible();
    expect(await reviewedPath()).toBe(settings.models.wan_high);
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await component.fill('');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page.reload();
    await openComponents();
    await expect(component).toHaveAttribute('title', settings.models.wan_low);
    await expect(page.getByText('Job override', { exact: true })).toHaveCount(
      0,
    );
    expect(await reviewedPath()).toBe(settings.models.wan_low);
  } finally {
    await app.close();
  }
});

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
