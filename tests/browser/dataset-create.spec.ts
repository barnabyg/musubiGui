import { test, expect } from '@playwright/test';
import { copyFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import { datasetHarness } from './datasetHarness';

test('upload creation uses a named managed folder under the visible default parent', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await page.goto(`${app.url}/#/datasets`);
    await page.getByRole('button', { name: 'New dataset' }).click();
    await expect(
      page.getByRole('radio', { name: 'Upload files', exact: true }),
    ).toBeChecked();
    await expect(
      page.getByLabel('Upload parent folder', { exact: true }),
    ).toHaveValue(app.media);
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('Name only uploads');
    await page
      .getByRole('button', { name: 'Create a new folder for uploads' })
      .click();
    await expect(
      page.getByRole('heading', { level: 1, name: 'Name only uploads' }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Dataset settings', exact: true })
      .click();
    await expect(
      page.getByRole('region', { name: 'Dataset settings', exact: true }),
    ).toContainText(/managed-/);
    await expect(
      page.getByRole('region', { name: 'Dataset settings', exact: true }),
    ).toContainText(app.media);
    await expect(page.getByText('Scan: complete 0/0')).toBeVisible();
  } finally {
    await app.cleanup();
  }
});

test('missing roots link to Settings and browser Back restores the creation form', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await page.goto(`${app.url}/#/settings`);
    await page.getByLabel('Dataset roots (one per line)').fill('');
    await page
      .getByRole('button', { name: 'Save settings', exact: true })
      .click();
    await expect(page.getByRole('status')).toHaveText('Settings saved');
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page.getByRole('button', { name: 'New dataset' }).click();
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('Retained creation');
    const settings = page.getByRole('link', {
      name: 'Set a dataset root in Settings',
      exact: true,
    });
    expect(await settings.count()).toBe(1);
    await settings.press('Enter');
    await expect(
      page.getByRole('heading', { name: 'Settings', exact: true }),
    ).toBeVisible();
    await page.getByLabel('Dataset roots (one per line)').fill(app.media);
    await page
      .getByRole('button', { name: 'Save settings', exact: true })
      .click();
    await expect(page.getByRole('status')).toHaveText('Settings saved');
    await page.goBack();
    await expect(
      page.getByRole('heading', { name: 'New dataset', exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel('Dataset name', { exact: true })).toHaveValue(
      'Retained creation',
    );
    await expect(
      page.getByRole('radio', { name: 'Upload files', exact: true }),
    ).toBeChecked();
    await expect(
      page.getByLabel('Upload parent folder', { exact: true }),
    ).toHaveValue(app.media);
    await expect(
      page.getByRole('button', { name: 'Create a new folder for uploads' }),
    ).toBeEnabled();
  } finally {
    await app.cleanup();
  }
});

test('an existing registration reopens its identity and a child folder has its own exact scope', async ({
  page,
}) => {
  const app = await datasetHarness();
  const child = path.join(app.media, 'child');
  await mkdir(child);
  await copyFile(path.join(app.media, 'red.png'), path.join(child, 'only.png'));
  try {
    await page.goto(`${app.url}/#/datasets`);
    await page.getByRole('button', { name: 'New dataset' }).click();
    await page
      .getByRole('radio', { name: 'Use existing folder', exact: true })
      .check();
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('Original identity');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page
      .getByRole('checkbox', { name: 'Index this entire dataset root' })
      .check();
    await page
      .getByRole('button', { name: 'Use existing folder', exact: true })
      .click();
    await expect(page.getByText('Scan: complete 5/5')).toBeVisible({
      timeout: 240_000,
    });
    const originalRoute = new URL(page.url()).hash;
    await page.getByRole('button', { name: 'Back to datasets' }).click();
    await page.getByRole('button', { name: 'New dataset' }).click();
    await page
      .getByRole('radio', { name: 'Use existing folder', exact: true })
      .check();
    await page
      .getByLabel('Dataset folder', { exact: true })
      .fill(`${app.media.replace(/\\/g, '/')}/`);
    const existing = page.getByRole('link', {
      name: 'Open existing dataset',
      exact: true,
    });
    expect(await existing.count()).toBe(1);
    await expect(existing).toHaveAttribute('href', originalRoute);
    await expect(
      page.getByText(
        'This folder is already registered as “Original identity”.',
      ),
    ).toBeVisible();
    await existing.press('Enter');
    await expect(
      page.getByRole('heading', { level: 1, name: 'Original identity' }),
    ).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`${originalRoute}$`));
    await page.getByRole('button', { name: 'Back to datasets' }).click();
    await page.getByRole('button', { name: 'New dataset' }).click();
    await page
      .getByLabel('Upload parent folder', { exact: true })
      .fill(path.join(app.fixture, 'images'));
    await page
      .getByRole('radio', { name: 'Use existing folder', exact: true })
      .check();
    await page.getByLabel('Dataset folder', { exact: true }).fill(child);
    await page
      .getByRole('radio', { name: 'Upload files', exact: true })
      .check();
    await expect(
      page.getByLabel('Upload parent folder', { exact: true }),
    ).toHaveValue(path.join(app.fixture, 'images'));
    await page
      .getByRole('radio', { name: 'Use existing folder', exact: true })
      .check();
    await expect(
      page.getByLabel('Dataset folder', { exact: true }),
    ).toHaveValue(child);
    await expect(
      page.getByRole('checkbox', { name: 'Index this entire dataset root' }),
    ).toHaveCount(0);
    await page.getByLabel('Dataset name', { exact: true }).fill('Child only');
    await page
      .getByRole('button', { name: 'Use existing folder', exact: true })
      .click();
    await expect(page.getByText('Scan: complete 1/1')).toBeVisible({
      timeout: 240_000,
    });
    await expect(
      page.getByRole('button', { name: 'only.png image', exact: false }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: /^motion.mp4 video/ }),
    ).toHaveCount(0);
  } finally {
    await app.cleanup();
  }
});

test('registration starts empty and requires acknowledgement of a whole root before scanning', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await page.setViewportSize({ width: 700, height: 768 });
    await page.goto(`${app.url}/#/datasets`);
    await page.getByRole('button', { name: 'New dataset' }).click();
    await page.getByLabel('Dataset name', { exact: true }).fill('Root scope');
    const upload = page.getByRole('radio', {
      name: 'Upload files',
      exact: true,
    });
    await upload.focus();
    await page.keyboard.press('ArrowRight');
    await expect(
      page.getByRole('radio', { name: 'Use existing folder', exact: true }),
    ).toBeChecked();
    await expect(
      page.getByLabel('Dataset folder', { exact: true }),
    ).toHaveValue('');
    await page
      .getByRole('button', { name: 'Browse for Dataset folder' })
      .click();
    const browser = page.getByRole('dialog', { name: 'Choose Dataset folder' });
    await expect(browser.locator('.path-location')).toHaveText(app.media);
    await browser.getByRole('button', { name: 'Choose this folder' }).click();
    await expect(
      page.getByText(
        `Index this exact folder and all its subfolders: ${app.media}. Source media and captions stay in place.`,
      ),
    ).toBeVisible();
    const submit = page.getByRole('button', {
      name: 'Use existing folder',
      exact: true,
    });
    expect(await submit.isEnabled()).toBe(false);
    const acknowledge = page.getByRole('checkbox', {
      name: 'Index this entire dataset root',
    });
    await expect(acknowledge).toBeVisible();
    await acknowledge.focus();
    await page.keyboard.press('Space');
    await expect(submit).toBeEnabled();
    await submit.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByText('Scan: complete 4/4')).toBeVisible({
      timeout: 240_000,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  } finally {
    await app.cleanup();
  }
});
