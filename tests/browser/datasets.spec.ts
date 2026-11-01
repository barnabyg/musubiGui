import { test, expect } from '@playwright/test';
import { datasetHarness } from './datasetHarness';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

test('register mixed media, preview, and recover library after service restart', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page.getByRole('button', { name: 'New dataset' }).click();
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('Mixed library');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page.getByRole('button', { name: 'Use existing folder' }).click();
    await expect(page.getByText('Scan: complete 4/4')).toBeVisible({
      timeout: 240_000,
    });
    await page.getByRole('button', { name: 'red.png image' }).click();
    await expect(page.getByAltText('Preview red.png')).toBeVisible();
    await page.getByRole('button', { name: 'motion.mp4 video' }).click();
    await expect
      .poll(() =>
        page
          .locator('video')
          .evaluate((video: HTMLVideoElement) => video.readyState),
      )
      .toBeGreaterThanOrEqual(1);
    await page.getByRole('button', { name: 'invalid.mp4 video' }).click();
    await expect(page.getByRole('alert')).toContainText(
      'Media cannot be decoded',
    );
    await app.stop();
    await app.start();
    await page.reload();
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await expect(
      page.getByRole('button', { name: /Mixed library.*4 media/ }),
    ).toBeVisible();
    // The invalid video is in the media total but not in what the dataset contains.
    await expect(page.locator('.dataset-contents')).toHaveText(
      '2 images · 1 video',
    );
  } finally {
    await app.cleanup();
  }
});

test('caption autosave flushes navigation, preserves failed drafts, rejects external conflicts and restores history', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page.getByRole('button', { name: 'New dataset' }).click();
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('Caption library');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page.getByRole('button', { name: 'Use existing folder' }).click();
    await expect(page.getByText('Scan: complete 4/4')).toBeVisible({
      timeout: 240_000,
    });
    await page.getByRole('button', { name: 'red.png image' }).click();
    await page
      .getByLabel('Caption', { exact: true })
      .fill('红色\nupdated immediately');
    await page.getByRole('button', { name: 'blue.png image' }).click();
    await expect
      .poll(() => readFile(path.join(app.media, 'red.txt'), 'utf8'))
      .toBe('红色\nupdated immediately');
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue('');
    await page.getByRole('button', { name: 'red.png image' }).click();
    await page.route('**/caption', async (route) => {
      if (route.request().method() === 'PUT')
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Fixture disk write denied' }),
        });
      else await route.continue();
    });
    await page
      .getByLabel('Caption', { exact: true })
      .fill('Draft survives failure');
    await expect(page.getByRole('alert')).toContainText(
      'Fixture disk write denied',
    );
    await page.getByRole('button', { name: 'blue.png image' }).click();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      'Draft survives failure',
    );
    await page.getByRole('button', { name: 'Back to datasets' }).click();
    await expect(
      page.getByRole('heading', { level: 1, name: 'Caption library' }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Close preview' }).click();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      'Draft survives failure',
    );
    await page.unroute('**/caption');
    await page.getByRole('button', { name: 'Retry save' }).click();
    await expect
      .poll(() => readFile(path.join(app.media, 'red.txt'), 'utf8'))
      .toBe('Draft survives failure');
    await writeFile(
      path.join(app.media, 'red.txt'),
      'External editor text',
      'utf8',
    );
    await page
      .getByLabel('Caption', { exact: true })
      .fill('My conflicting draft');
    await expect(page.getByRole('alert')).toContainText(
      'Caption changed outside this editor',
    );
    await expect
      .poll(() => readFile(path.join(app.media, 'red.txt'), 'utf8'))
      .toBe('External editor text');
    await page.getByRole('button', { name: 'Review current sidecar' }).click();
    await page.getByRole('button', { name: 'Use current sidecar' }).click();
    await expect(page.getByLabel('Retained unsaved caption')).toHaveValue(
      'My conflicting draft',
    );
    await page.getByRole('button', { name: 'Caption history' }).click();
    await page
      .getByRole('button', { name: 'Restore this caption' })
      .first()
      .click();
    await expect
      .poll(() => readFile(path.join(app.media, 'red.txt'), 'utf8'))
      .toBe('红色\nupdated immediately');
    await app.stop();
    await app.start();
    // The dataset stays open across a reload, read back from the restarted backend.
    await page.reload();
    await page.getByRole('button', { name: 'red.png image' }).click();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      '红色\nupdated immediately',
    );
  } finally {
    await app.cleanup();
  }
});

test('search stays visible, states read as badges and the caption count filters to missing ones', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page.getByRole('button', { name: 'New dataset' }).click();
    await page.getByLabel('Dataset name', { exact: true }).fill('Badges');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page.getByRole('button', { name: 'Use existing folder' }).click();
    await expect(page.getByText('Scan: complete 4/4')).toBeVisible({
      timeout: 240_000,
    });
    await expect(page.getByLabel('Search media and captions')).toBeVisible();
    await expect(page.getByLabel('Filter media')).toBeVisible();

    const tile = (name: string) =>
      page.getByRole('button', { name: new RegExp(`^${name}`) });
    await expect(tile('blue.png').locator('.status-badge')).toHaveText(
      'Missing caption',
    );
    await expect(tile('invalid.mp4').locator('.status-badge')).toHaveText([
      'Missing caption',
      'Invalid',
    ]);
    await expect(tile('red.png').locator('.status-badge')).toHaveCount(0);

    const count = page.getByRole('button', { name: '2 of 4 captioned' });
    await count.click();
    await expect(count).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByLabel('Filter media')).toHaveValue('missing');
    await expect(page.locator('.media-tile')).toHaveCount(2);
    await expect(tile('red.png')).toHaveCount(0);
    await count.click();
    await expect(page.locator('.media-tile')).toHaveCount(4);

    // Excluding adds its badge, and selection still works from the bulk panel.
    await page.getByLabel('Select red.png', { exact: true }).check();
    await page.getByRole('button', { name: 'Bulk actions · 1' }).click();
    await page.getByText('Selection and bulk caption actions').click();
    await page.getByRole('button', { name: 'Exclude selection' }).click();
    await expect(tile('red.png').locator('.status-badge')).toHaveText(
      'Excluded',
    );
  } finally {
    await app.cleanup();
  }
});
