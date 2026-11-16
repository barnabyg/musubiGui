import { test, expect } from '@playwright/test';
import { jobWorkspace, openJobs } from './jobHarness';

test('job actions wait for the initial load so it cannot replace new drafts', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route('**/api/jobs/presets', async (route) => {
      await held;
      await route.continue();
    });
    await openJobs(page, app.url);
    const create = page.getByRole('button', {
      name: 'New Wan job',
      exact: true,
    });
    await expect(create).toBeDisabled();
    release();
    await create.click();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await page.getByLabel('Job name', { exact: true }).fill('After load');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page
      .getByRole('button', { name: 'Back to jobs', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: 'After load · WAN · r2', exact: true }),
    ).toBeVisible();
  } finally {
    await app.close();
  }
});

test('a failed job library load must be retried before creating a draft', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    let fail = true;
    await page.route('**/api/jobs/presets', async (route) => {
      if (fail) {
        await route.fulfill({
          status: 503,
          json: { detail: 'Preset library temporarily unavailable.' },
        });
      } else await route.continue();
    });
    await openJobs(page, app.url);
    await expect(page.getByRole('alert')).toContainText(
      'Preset library temporarily unavailable.',
    );
    await expect(
      page.getByRole('button', { name: 'New Wan job', exact: true }),
    ).toBeDisabled();
    fail = false;
    await page
      .getByRole('button', { name: 'Retry loading jobs', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await page.getByRole('tab', { name: 'Presets', exact: true }).click();
    const presets = page.getByRole('combobox', {
      name: 'Compatible preset',
      exact: true,
    });
    await presets.selectOption({
      label: 'Smoke test · Wan alternating · 4 steps',
    });
    await page
      .getByRole('button', { name: 'Apply preset', exact: true })
      .click();
    await expect(page.getByRole('status')).toHaveText(
      'Preset applied as a new revision',
    );
  } finally {
    await app.close();
  }
});

test('a transient initial preset failure recovers before a draft can be created', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    let calls = 0;
    await page.route('**/api/jobs/presets', async (route) => {
      calls += 1;
      if (calls === 1)
        await route.fulfill({
          status: 503,
          json: { detail: 'Presets temporarily unavailable.' },
        });
      else await route.continue();
    });
    await openJobs(page, app.url);
    await expect(page.getByRole('status')).toHaveText('Jobs loaded');
    expect(calls).toBe(2);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await page.getByRole('tab', { name: 'Presets', exact: true }).click();
    const presets = page.getByRole('combobox', {
      name: 'Compatible preset',
      exact: true,
    });
    await presets.selectOption({
      label: 'Smoke test · Wan alternating · 4 steps',
    });
    await page
      .getByRole('button', { name: 'Apply preset', exact: true })
      .click();
    await expect(page.getByRole('status')).toHaveText(
      'Preset applied as a new revision',
    );
    await expect(page.getByRole('alert')).toHaveCount(0);
  } finally {
    await app.close();
  }
});
