import { test, expect } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { datasetHarness } from './datasetHarness';

test('filters retain explicit selection, bulk previews report conflicts, restore successes, and persist exclusions', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page.getByRole('button', { name: 'New dataset' }).click();
    await page.getByLabel('Dataset name', { exact: true }).fill('Bulk library');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page.getByRole('button', { name: 'Register folder' }).click();
    await expect(page.getByText('Scan: complete 4/4')).toBeVisible({
      timeout: 240_000,
    });
    const originalRed = await readFile(path.join(app.media, 'red.txt'), 'utf8');
    const originalMotion = await readFile(
      path.join(app.media, 'motion.txt'),
      'utf8',
    );
    await page.getByRole('button', { name: 'Tools', exact: true }).click();
    await page
      .getByLabel('Filter media', { exact: true })
      .selectOption('missing');
    await page.getByLabel('Select blue.png', { exact: true }).check();
    await page.getByLabel('Filter media', { exact: true }).selectOption('all');
    await page.getByLabel('Select red.png', { exact: true }).check();
    await page.getByLabel('Search media and captions').fill('red');
    await expect(
      page.getByText('1 matching · 2 selected (1 hidden by filters)'),
    ).toBeVisible();
    await page
      .getByText('Selection and bulk caption actions', { exact: true })
      .click();
    await page.getByLabel('Caption prefix', { exact: true }).fill('trigger, ');
    await page.getByRole('button', { name: 'Preview caption changes' }).click();
    await expect(page.getByLabel('Bulk caption preview')).toContainText(
      'blue.png',
    );
    await expect(page.getByLabel('Bulk caption preview')).toContainText(
      'red.png',
    );
    expect(await readFile(path.join(app.media, 'red.txt'), 'utf8')).toBe(
      originalRed,
    );
    await writeFile(path.join(app.media, 'blue.txt'), 'External blue', 'utf8');
    await page
      .getByRole('button', { name: 'Apply previewed captions' })
      .click();
    await expect(page.getByLabel('Bulk caption results')).toContainText(
      'blue.png: conflict',
    );
    await expect(page.getByLabel('Bulk caption results')).toContainText(
      'red.png: saved',
    );
    expect(await readFile(path.join(app.media, 'red.txt'), 'utf8')).toBe(
      'trigger, ' + originalRed,
    );
    expect(await readFile(path.join(app.media, 'blue.txt'), 'utf8')).toBe(
      'External blue',
    );
    expect(await readFile(path.join(app.media, 'motion.txt'), 'utf8')).toBe(
      originalMotion,
    );
    await page
      .getByRole('button', { name: 'Restore successful captions' })
      .click();
    await expect
      .poll(() => readFile(path.join(app.media, 'red.txt'), 'utf8'))
      .toBe(originalRed);
    await page.getByRole('button', { name: 'Exclude selection' }).click();
    await page
      .getByRole('button', { name: 'Dataset settings', exact: true })
      .click();
    await expect(
      page.getByText('1 included · 2 excluded · 1 invalid'),
    ).toBeVisible();
    await app.stop();
    await app.start();
    await page.reload();
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page.getByRole('button', { name: /Bulk library.*4 media/ }).click();
    await page.getByRole('button', { name: 'Tools', exact: true }).click();
    await page
      .getByLabel('Filter media', { exact: true })
      .selectOption('excluded');
    await expect(
      page.getByRole('button', { name: 'red.png image' }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'blue.png image' }),
    ).toBeVisible();
  } finally {
    await app.cleanup();
  }
});
