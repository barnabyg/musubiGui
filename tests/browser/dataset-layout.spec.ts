import { test, expect } from '@playwright/test';
import { copyFile, readFile } from 'node:fs/promises';
import path from 'node:path';
import { datasetHarness } from './datasetHarness';

test('large galleries scroll, resize and filter without losing access to media', async ({
  page,
}) => {
  // Leave room for a real scan and the subsequent gallery interactions.
  const app = await datasetHarness();
  try {
    await Promise.all(
      Array.from({ length: 4 }, (_, index) =>
        copyFile(
          path.join(app.media, 'red.png'),
          path.join(app.media, `study-${String(index).padStart(2, '0')}.png`),
        ),
      ),
    );
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page.getByRole('button', { name: 'New dataset' }).click();
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('Large collection');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page.getByRole('button', { name: 'Use existing folder' }).click();
    await expect(page.locator('.dataset-identity [role="status"]')).toHaveText(
      'Scan: complete 8/8',
      { timeout: 240_000 },
    );
    const gallery = page.getByLabel('Dataset gallery');
    // The gallery starts directly below the compact header and fills the viewport.
    await expect
      .poll(() =>
        gallery.evaluate((element) => element.getBoundingClientRect().y),
      )
      .toBeLessThan(80);
    expect((await gallery.boundingBox())?.height).toBeGreaterThan(750);
    // A narrow viewport forces recycling with eight real files, without 24 probe startups.
    await page.setViewportSize({ width: 640, height: 650 });
    await expect
      .poll(() =>
        gallery
          .getByRole('button')
          .first()
          .evaluate((element) => element.getBoundingClientRect().width),
      )
      .toBeGreaterThan(300);
    expect(await gallery.getByRole('button').count()).toBeLessThan(8);
    await gallery.focus();
    await page.keyboard.press('End');
    const last = page.getByRole('button', { name: 'study-03.png image' });
    await expect(last).toBeVisible();
    await last.click();
    await expect(page.getByAltText('Preview study-03.png')).toBeVisible();
    await page.getByRole('button', { name: 'Close preview' }).click();
    await gallery.focus();
    await page.keyboard.press('Home');
    await page.getByRole('button', { name: 'blue.png image' }).click();
    await page.getByRole('button', { name: 'Close preview' }).click();
    await page.setViewportSize({ width: 820, height: 900 });
    // Wait for the gallery's responsive columns before scrolling its new height.
    await expect
      .poll(() =>
        gallery
          .getByRole('button')
          .first()
          .evaluate((element) => element.getBoundingClientRect().width),
      )
      .toBeGreaterThan(200);
    await gallery.focus();
    await page.keyboard.press('End');
    await expect(last).toBeVisible();
    await page.getByLabel('Search media and captions').fill('blue.png');
    await expect(
      page.getByRole('button', { name: 'blue.png image' }),
    ).toBeVisible();
    await page.getByLabel('Search media and captions').fill('no such media');
    await expect(
      page.getByText('No matching media', { exact: true }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Clear search and filters' })
      .click();
    await expect(
      page.getByRole('button', { name: 'blue.png image' }),
    ).toBeVisible();
  } finally {
    await app.cleanup();
  }
});

test('the active tile stays in view and panels keep the column count', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await Promise.all(
      Array.from({ length: 8 }, (_, index) =>
        copyFile(
          path.join(app.media, 'red.png'),
          path.join(app.media, `study-${String(index).padStart(2, '0')}.png`),
        ),
      ),
    );
    await page.setViewportSize({ width: 1440, height: 800 });
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page.getByRole('button', { name: 'New dataset' }).click();
    await page.getByLabel('Dataset name', { exact: true }).fill('Layout');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page.getByRole('button', { name: 'Use existing folder' }).click();
    await expect(page.locator('.dataset-identity [role="status"]')).toHaveText(
      /^Scan: complete/,
      { timeout: 240_000 },
    );
    const gallery = page.getByLabel('Dataset gallery');
    const tile = page.getByRole('button', { name: 'study-07.png image' });
    const card = page.locator('.media-card').filter({ has: tile });
    const position = () =>
      card.evaluate((element) => {
        const { top, left } = (element as HTMLElement).style;
        return { top, left };
      });

    // A panel keeps every tile in its row and column.
    await gallery.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await expect(tile).toBeInViewport();
    const before = await position();
    const panel = page.getByRole('button', {
      name: 'Bulk actions',
      exact: true,
    });
    await panel.click();
    await expect(
      page.getByRole('button', { name: 'Select visible matches' }),
    ).toBeVisible();
    await expect.poll(position).toEqual(before);
    await expect(tile).toBeInViewport();
    await page.getByRole('button', { name: 'Close bulk actions' }).click();
    await expect.poll(position).toEqual(before);

    // The preview changes the column count, but the active tile stays in view both ways.
    await tile.click();
    await expect(page.getByAltText('Preview study-07.png')).toBeVisible();
    await expect(tile).toBeInViewport();
    // With the preview open too, a panel still leaves the tile in its row and column, wide or not.
    for (const width of [1440, 1944]) {
      await page.setViewportSize({ width, height: 800 });
      await expect(tile).toBeInViewport();
      const withPreview = await position();
      await panel.click();
      await expect(
        page.getByRole('button', { name: 'Select visible matches' }),
      ).toBeVisible();
      await expect.poll(position).toEqual(withPreview);
      await expect(tile).toBeInViewport();
      await page.getByRole('button', { name: 'Close bulk actions' }).click();
    }
    await page.setViewportSize({ width: 1440, height: 800 });
    await page.getByRole('button', { name: 'Close preview' }).click();
    await expect(page.getByAltText('Preview study-07.png')).toHaveCount(0);
    await expect(tile).toBeInViewport();

    // Virtual scrolling still reaches both ends afterwards.
    await gallery.evaluate((element) => {
      element.scrollTop = 0;
    });
    await expect(
      page.getByRole('button', { name: 'study-00.png image' }),
    ).toBeInViewport();

    // At 760 px the preview leaves the gallery usable, so another item can be chosen from it.
    await page.setViewportSize({ width: 760, height: 800 });
    await page.getByRole('button', { name: 'study-00.png image' }).click();
    await expect(page.getByAltText('Preview study-00.png')).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'study-00.png image' }),
    ).toBeInViewport();
    expect((await gallery.boundingBox())!.height).toBeGreaterThan(150);
    await gallery.evaluate((element) => {
      element.scrollTop = 500;
    });
    const next = gallery.getByRole('button', { name: /^study-0[1-7]\.png/ });
    await next.last().click();
    await expect(page.locator('.media-viewer')).toBeVisible();
    await expect(page.getByAltText('Preview study-00.png')).toHaveCount(0);
  } finally {
    await app.cleanup();
  }
});

test('separate library and editor create, populate, rename, reopen and delete without removing files', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await expect(page.getByText('Your library starts here')).toBeVisible();
    await expect(page.getByLabel('Dataset name', { exact: true })).toHaveCount(
      0,
    );
    const add = page.getByRole('button', { name: 'New dataset' });
    await add.focus();
    await page.keyboard.press('Enter');
    await expect(
      page.getByRole('heading', { level: 1, name: 'New dataset' }),
    ).toBeFocused();
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('Portrait studies');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page
      .getByRole('button', { name: 'Create a new folder for uploads' })
      .click();
    await expect(page.getByText('Ready for your media')).toBeVisible();
    await expect(page.getByRole('button', { name: 'New dataset' })).toHaveCount(
      0,
    );
    await page
      .getByLabel('Upload files')
      .setInputFiles([
        path.join(app.media, 'red.png'),
        path.join(app.media, 'red.txt'),
      ]);
    await page.getByRole('button', { name: 'Upload selected files' }).click();
    await expect(page.getByLabel('Upload files')).toBeEnabled({
      timeout: 240_000,
    });
    const tile = page.getByRole('button', { name: 'red.png image' });
    await expect(
      page.getByLabel('Caption for red.png', { exact: true }),
    ).toHaveValue(/红/);
    await page.getByRole('button', { name: 'Add media', exact: true }).click();
    await expect(page.getByLabel('Upload files')).toBeHidden();
    const tools = page.getByRole('button', {
      name: 'Bulk actions',
      exact: true,
    });
    await tools.focus();
    await page.keyboard.press('Enter');
    await page.getByRole('button', { name: 'Select visible matches' }).focus();
    await page.keyboard.press('Escape');
    await expect(tools).toBeFocused();
    await expect
      .poll(() =>
        tile.evaluate((element) => element.getBoundingClientRect().top),
      )
      .toBeLessThan(80);
    await expect(
      page.getByRole('button', { name: 'Select visible matches' }),
    ).toBeHidden();
    const image = tile.locator('img');
    expect((await image.boundingBox())?.height).toBeGreaterThan(180);
    await tile.focus();
    await page.keyboard.press('Enter');
    await page
      .getByLabel('Caption', { exact: true })
      .fill('A red portrait, soft light');
    await page.getByRole('button', { name: 'Back to datasets' }).click();
    await expect(
      page.getByRole('heading', { level: 1, name: 'Datasets' }),
    ).toBeFocused();
    await expect(page.getByLabel('Dataset gallery')).toHaveCount(0);
    await page
      .getByRole('button', { name: /Portrait studies.*1 media.*1 captioned/ })
      .click();
    await expect(
      page.getByLabel('Caption for red.png', { exact: true }),
    ).toHaveValue('A red portrait, soft light');
    await tile.click();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      'A red portrait, soft light',
    );
    await page.getByRole('button', { name: 'Close preview' }).click();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveCount(0);
    await page
      .getByRole('button', { name: 'Dataset settings', exact: true })
      .click();
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('Portrait collection');
    await page.getByRole('button', { name: 'Save dataset name' }).click();
    await expect(
      page.getByRole('heading', { level: 1, name: 'Portrait collection' }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Close settings', exact: true })
      .click();
    for (const width of [820, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(tile).toBeVisible();
      await expect
        .poll(() =>
          // The top bar wraps on narrow windows; tiles still start right below it.
          tile.evaluate(
            (element) =>
              element.getBoundingClientRect().top -
              document.querySelector('.dataset-topbar')!.getBoundingClientRect()
                .bottom,
          ),
        )
        .toBeLessThan(24);
      await tile.click();
      await expect(page.getByLabel('Caption', { exact: true })).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
      await page.getByRole('button', { name: 'Close preview' }).click();
    }
    const datasets = (await (
      await page.request.get(`${app.url}/api/datasets`)
    ).json()) as { id: string; path: string }[];
    const stored = datasets[0];
    expect(await readFile(path.join(stored.path, 'red.txt'), 'utf8')).toBe(
      'A red portrait, soft light',
    );
    await page.getByRole('button', { name: 'Back to datasets' }).click();
    await page
      .getByRole('button', { name: 'Delete Portrait collection' })
      .click();
    const question = page.getByRole('dialog');
    await expect(question).toContainText('Portrait collection');
    await expect(question).toContainText(
      'Media files, captions and recovery history stay on disk',
    );
    await question.getByRole('button', { name: 'Cancel' }).click();
    await expect(
      page.getByRole('button', { name: /Portrait collection.*1 media/ }),
    ).toBeVisible();
    await page.route(`**/api/datasets/${stored.id}`, async (route) => {
      if (route.request().method() === 'DELETE')
        await route.fulfill({
          status: 500,
          json: { detail: 'Fixture deletion failed' },
        });
      else await route.continue();
    });
    await page
      .getByRole('button', { name: 'Delete Portrait collection' })
      .click();
    await question.getByRole('button', { name: 'Delete from library' }).click();
    await expect(page.getByRole('alert')).toContainText(
      'Fixture deletion failed',
    );
    await expect(
      page.getByRole('button', { name: /Portrait collection.*1 media/ }),
    ).toBeVisible();
    await page.unroute(`**/api/datasets/${stored.id}`);
    await page
      .getByRole('button', { name: 'Delete Portrait collection' })
      .click();
    await question.getByRole('button', { name: 'Delete from library' }).click();
    await expect(page.getByText('Your library starts here')).toBeVisible();
    expect(await readFile(path.join(stored.path, 'red.png'))).toEqual(
      await readFile(path.join(app.media, 'red.png')),
    );
    expect(await readFile(path.join(stored.path, 'red.txt'), 'utf8')).toBe(
      'A red portrait, soft light',
    );
    await app.stop();
    await app.start();
    await page.reload();
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await expect(page.getByText('Your library starts here')).toBeVisible();
    expect(
      (await page.request.get(`${app.url}/api/datasets/${stored.id}`)).status(),
    ).toBe(404);
  } finally {
    await app.cleanup();
  }
});
