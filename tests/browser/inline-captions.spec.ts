import { test, expect, type Page } from '@playwright/test';
import { copyFile, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { datasetHarness } from './datasetHarness';

async function openDataset(
  page: Page,
  app: Awaited<ReturnType<typeof datasetHarness>>,
  count = 4,
) {
  await page.goto(app.url);
  await page.getByRole('button', { name: 'Datasets', exact: true }).click();
  await page.getByRole('button', { name: 'New dataset' }).click();
  await page
    .getByLabel('Dataset name', { exact: true })
    .fill('Inline captions');
  await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
  await page.getByRole('button', { name: 'Register folder' }).click();
  await expect(page.locator('.dataset-identity [role="status"]')).toHaveText(
    `Scan: complete ${count}/${count}`,
    { timeout: 45_000 },
  );
}

test('captions edit beneath thumbnails, share the preview draft and flush the latest in-flight edit before leaving', async ({
  page,
}) => {
  const app = await datasetHarness();
  let release = () => {};
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await openDataset(page, app);
    const red = page.getByLabel('Caption for red.png', { exact: true });
    const blue = page.getByLabel('Caption for blue.png', { exact: true });
    await red.fill('红色\nAn inline red caption');
    await blue.fill('A new caption for blue');
    await expect(page.getByAltText('Preview red.png')).toHaveCount(0);
    await expect
      .poll(() => readFile(path.join(app.media, 'red.txt'), 'utf8'))
      .toBe('红色\nAn inline red caption');
    await expect
      .poll(() =>
        readFile(path.join(app.media, 'blue.txt'), 'utf8').catch(() => null),
      )
      .toBe('A new caption for blue');
    await page.getByRole('button', { name: 'red.png image' }).click();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      '红色\nAn inline red caption',
    );
    await red.fill('Shared from the gallery');
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      'Shared from the gallery',
    );
    await page
      .getByLabel('Caption', { exact: true })
      .fill('Shared from the preview');
    await expect(red).toHaveValue('Shared from the preview');
    await page.getByRole('button', { name: 'Close preview' }).click();

    // Undoing a failed edit still resolves the failed save against the server revision.
    // Earlier saves must land first, or the injected failure can hit one of them.
    await expect
      .poll(() => readFile(path.join(app.media, 'red.txt'), 'utf8'))
      .toBe('Shared from the preview');
    let failOnce = true;
    await page.route('**/caption', async (route) => {
      const request = route.request();
      if (
        request.method() === 'PUT' &&
        failOnce &&
        (request.postDataJSON() as { text: string }).text ===
          'An interrupted edit'
      ) {
        failOnce = false;
        await route.fulfill({
          status: 500,
          json: { detail: 'Fixture write interrupted' },
        });
      } else await route.continue();
    });
    await red.fill('An interrupted edit');
    await expect(
      page.getByRole('button', {
        name: 'Review 1 unsaved caption',
        exact: true,
      }),
    ).toBeVisible();
    await red.fill('Shared from the preview');
    await expect(
      page.getByRole('button', {
        name: 'Review 1 unsaved caption',
        exact: true,
      }),
    ).toHaveCount(0);
    await page.unroute('**/caption');

    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let requested = false;
    let first = true;
    await page.route('**/caption', async (route) => {
      if (route.request().method() === 'PUT' && first) {
        first = false;
        requested = true;
        await held;
      }
      await route.continue();
    });
    await red.fill('First write in flight');
    await expect.poll(() => requested).toBe(true);
    await red.fill('Latest text typed while saving');
    await page.getByRole('button', { name: 'Back to datasets' }).click();
    await expect(
      page.getByRole('heading', { level: 1, name: 'Inline captions' }),
    ).toBeVisible();
    release();
    await expect(
      page.getByRole('heading', { level: 1, name: 'Datasets' }),
    ).toBeVisible();
    expect(await readFile(path.join(app.media, 'red.txt'), 'utf8')).toBe(
      'Latest text typed while saving',
    );
    await app.stop();
    await app.start();
    await page.reload();
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page
      .getByRole('button', { name: /Inline captions.*4 media/ })
      .click();
    await expect(red).toHaveValue('Latest text typed while saving');
    await expect(blue).toHaveValue('A new caption for blue');
  } finally {
    release();
    await app.cleanup();
  }
});

test('virtual scrolling preserves pending and failed inline drafts, with recovery and external conflict review', async ({
  page,
}) => {
  // Keep scan and recovery time separate from the default assertion deadline.
  test.setTimeout(90_000);
  const app = await datasetHarness();
  let release = () => {};
  try {
    await Promise.all(
      Array.from({ length: 4 }, (_, index) =>
        copyFile(
          path.join(app.media, 'red.png'),
          path.join(app.media, `study-${String(index).padStart(2, '0')}.png`),
        ),
      ),
    );
    // One column makes eight real files exceed the mounted virtual window.
    await page.setViewportSize({ width: 640, height: 650 });
    await openDataset(page, app, 8);
    const blue = page.getByLabel('Caption for blue.png', { exact: true });
    const red = page.getByLabel('Caption for red.png', { exact: true });
    const gallery = page.getByLabel('Dataset gallery');
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let requested = false;
    await page.route('**/caption', async (route) => {
      if (route.request().method() === 'PUT') {
        requested = true;
        await held;
      }
      await route.continue();
    });
    await blue.fill('Save continues after the card leaves the window');
    await expect.poll(() => requested).toBe(true);
    await gallery.focus();
    await page.keyboard.press('End');
    await expect(blue).toHaveCount(0);
    let warned = false;
    page.once('dialog', async (dialog) => {
      expect(dialog.type()).toBe('beforeunload');
      await dialog.dismiss();
      warned = true;
    });
    await page.close({ runBeforeUnload: true });
    await expect.poll(() => warned).toBe(true);
    expect(page.isClosed()).toBe(false);
    release();
    await expect
      .poll(() =>
        readFile(path.join(app.media, 'blue.txt'), 'utf8').catch(() => null),
      )
      .toBe('Save continues after the card leaves the window');
    await page.unroute('**/caption');
    await page.keyboard.press('Home');
    await expect(blue).toHaveValue(
      'Save continues after the card leaves the window',
    );
    await page.route('**/caption', async (route) => {
      if (route.request().method() === 'PUT')
        await route.fulfill({
          status: 500,
          json: { detail: 'Fixture disk full' },
        });
      else await route.continue();
    });
    await red.fill('Failed inline draft must survive');
    await expect(
      page.getByRole('button', {
        name: 'Review 1 unsaved caption',
        exact: true,
      }),
    ).toBeVisible();
    await gallery.focus();
    await page.keyboard.press('End');
    await expect(red).toHaveCount(0);
    await page.getByRole('button', { name: 'Back to datasets' }).click();
    await expect(
      page.getByRole('heading', { level: 1, name: 'Inline captions' }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Review 1 unsaved caption', exact: true })
      .click();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      'Failed inline draft must survive',
    );
    await expect(page.getByRole('alert')).toContainText('Fixture disk full');
    await page.unroute('**/caption');
    await page.getByRole('button', { name: 'Retry save', exact: true }).click();
    await expect
      .poll(() => readFile(path.join(app.media, 'red.txt'), 'utf8'))
      .toBe('Failed inline draft must survive');
    await page.getByRole('button', { name: 'Close preview' }).click();
    await gallery.focus();
    await page.keyboard.press('Home');
    await expect(red).toHaveValue('Failed inline draft must survive');
    await writeFile(
      path.join(app.media, 'red.txt'),
      'External editor caption',
      'utf8',
    );
    await red.fill('Keep my conflicting inline draft');
    await page
      .getByRole('button', { name: 'Review 1 unsaved caption', exact: true })
      .click();
    await expect(page.getByRole('alert')).toContainText(
      'Caption changed outside this editor',
    );
    expect(await readFile(path.join(app.media, 'red.txt'), 'utf8')).toBe(
      'External editor caption',
    );
    await page.getByRole('button', { name: 'Review current sidecar' }).click();
    await page.getByRole('button', { name: 'Use current sidecar' }).click();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      'External editor caption',
    );
    await expect(page.getByLabel('Retained unsaved caption')).toHaveValue(
      'Keep my conflicting inline draft',
    );
    await page.getByRole('button', { name: 'Caption history' }).click();
    const restoreHeld = new Promise<void>((resolve) => {
      release = resolve;
    });
    let restoring = false;
    await page.route('**/caption/restore', async (route) => {
      restoring = true;
      await restoreHeld;
      await route.continue();
    });
    await page
      .getByRole('button', { name: 'Restore this caption' })
      .first()
      .click();
    await expect.poll(() => restoring).toBe(true);
    await page
      .getByLabel('Caption', { exact: true })
      .fill('Text typed while history is restoring');
    release();
    await expect
      .poll(() => readFile(path.join(app.media, 'red.txt'), 'utf8'))
      .toBe('Text typed while history is restoring');
    await page.getByRole('button', { name: 'Close preview' }).click();
    await gallery.focus();
    await page.keyboard.press('Home');
    await expect(red).toHaveValue('Text typed while history is restoring');
  } finally {
    release();
    await app.cleanup();
  }
});

test('autosave keeps a focused inline caption stable when its text stops matching the gallery search', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await openDataset(page, app);
    await page.getByRole('button', { name: 'Tools', exact: true }).click();
    await page.getByLabel('Search media and captions').fill('red square');
    const red = page.getByLabel('Caption for red.png', { exact: true });
    await red.fill('A completely different description');
    await expect
      .poll(() => readFile(path.join(app.media, 'red.txt'), 'utf8'))
      .toBe('A completely different description');
    await expect(red).toBeFocused();
    await expect(red).toHaveValue('A completely different description');
    await page.getByRole('button', { name: 'red.png image' }).click();
    await expect(page.getByAltText('Preview red.png')).toBeVisible();
    await page.getByRole('button', { name: 'Close preview' }).click();
    await page
      .getByRole('button', { name: 'Close tools', exact: true })
      .click();
    await expect(
      page.getByText('No matching media', { exact: true }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Clear search and filters' })
      .click();
    await expect(red).toHaveValue('A completely different description');
  } finally {
    await app.cleanup();
  }
});

test('a caption saved while the item list is loading is not reverted by that list', async ({
  page,
}) => {
  const app = await datasetHarness();
  let release = () => {};
  try {
    await openDataset(page, app);
    const blue = page.getByLabel('Caption for blue.png', { exact: true });
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let holding = false;
    let read = false;
    // Read one list before the save and deliver it after; later polls pass through.
    await page.route('**/items', async (route) => {
      if (holding) return route.continue();
      holding = true;
      const response = await route.fetch();
      read = true;
      await held;
      await route.fulfill({ response });
    });
    await page
      .getByRole('button', { name: 'Dataset settings', exact: true })
      .click();
    const rescan = page.getByRole('button', { name: 'Re-scan', exact: true });
    await rescan.click();
    await expect.poll(() => read).toBe(true);
    await blue.fill('Saved while the list was loading');
    await blue.blur();
    await expect
      .poll(() =>
        readFile(path.join(app.media, 'blue.txt'), 'utf8').catch(() => null),
      )
      .toBe('Saved while the list was loading');
    await expect(
      page.getByText('Saved', { exact: true }).first(),
    ).toBeVisible();
    release();
    await expect(rescan).toBeEnabled();
    // Read once after React applies the list: a later scan poll would mask a revert.
    await page.evaluate(
      () =>
        new Promise((resolve) =>
          requestAnimationFrame(() => setTimeout(resolve, 50)),
        ),
    );
    expect(await blue.inputValue()).toBe('Saved while the list was loading');
  } finally {
    release();
    await app.cleanup();
  }
});
