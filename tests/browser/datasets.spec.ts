import { test, expect } from '@playwright/test';
import { datasetHarness } from './datasetHarness';
import {
  copyFile,
  mkdir,
  readFile,
  rename,
  rmdir,
  writeFile,
} from 'node:fs/promises';
import { execFile } from 'node:child_process';
import path from 'node:path';

test('register mixed media, preview, and recover library after service restart', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('Mixed library');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page.getByRole('button', { name: 'Register folder' }).click();
    await expect(page.getByText('Scan: complete 4/4')).toBeVisible();
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
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('Caption library');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page.getByRole('button', { name: 'Register folder' }).click();
    await expect(page.getByText('Scan: complete 4/4')).toBeVisible();
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
    await page.reload();
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page
      .getByRole('button', { name: /Caption library.*4 media/ })
      .click();
    await page.getByRole('button', { name: 'red.png image' }).click();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      '红色\nupdated immediately',
    );
  } finally {
    await app.cleanup();
  }
});

test('caption autosave rejects an inside-root Windows junction redirect and retains the intended draft', async ({
  page,
}) => {
  test.skip(process.platform !== 'win32', 'Native Windows junction boundary');
  const app = await datasetHarness();
  const original = path.join(app.fixture, 'original media');
  const target = path.join(app.fixture, 'unrelated media');
  let moved = false;
  let linked = false;
  try {
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('Junction library');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page.getByRole('button', { name: 'Register folder' }).click();
    await expect(page.getByText('Scan: complete 4/4')).toBeVisible();
    await page.getByRole('button', { name: 'red.png image' }).click();
    const caption = await readFile(path.join(app.media, 'red.txt'), 'utf8');
    await mkdir(target);
    await copyFile(
      path.join(app.media, 'red.png'),
      path.join(target, 'red.png'),
    );
    await writeFile(
      path.join(target, 'red.txt'),
      'Unrelated sidecar remains intact',
      'utf8',
    );
    await rename(app.media, original);
    moved = true;
    const command = path.join(
      process.env.SystemRoot ?? 'C:\\Windows',
      'System32',
      'cmd.exe',
    );
    await new Promise<void>((resolve, reject) =>
      execFile(
        command,
        ['/d', '/c', 'mklink', '/J', app.media, target],
        { windowsHide: true },
        (error) => (error ? reject(error) : resolve()),
      ),
    );
    linked = true;
    await page
      .getByLabel('Caption', { exact: true })
      .fill('Draft must not follow the redirect');
    await expect(page.getByRole('alert')).toContainText('Linked or redirected');
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      'Draft must not follow the redirect',
    );
    expect(await readFile(path.join(target, 'red.txt'), 'utf8')).toBe(
      'Unrelated sidecar remains intact',
    );
    expect(await readFile(path.join(original, 'red.txt'), 'utf8')).toBe(
      caption,
    );
  } finally {
    if (linked) await rmdir(app.media);
    if (moved) await rename(original, app.media);
    await app.cleanup();
  }
});
