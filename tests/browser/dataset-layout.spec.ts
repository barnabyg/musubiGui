import { test, expect } from '@playwright/test';
import { copyFile, readFile } from 'node:fs/promises';
import path from 'node:path';
import { datasetHarness } from './datasetHarness';

test('large galleries scroll, resize and filter without losing access to media', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await Promise.all(
      Array.from({ length: 20 }, (_, index) =>
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
    await page.getByRole('button', { name: 'Register folder' }).click();
    await expect(page.getByText('Scan: complete 24/24')).toBeVisible({
      timeout: 45_000,
    });
    const gallery = page.getByLabel('Dataset gallery');
    // The gallery starts directly below the compact header and fills the viewport.
    await expect
      .poll(() =>
        gallery.evaluate((element) => element.getBoundingClientRect().y),
      )
      .toBeLessThan(80);
    expect((await gallery.boundingBox())?.height).toBeGreaterThan(750);
    expect(await gallery.getByRole('button').count()).toBeLessThan(24);
    await gallery.focus();
    await page.keyboard.press('End');
    const last = page.getByRole('button', { name: 'study-19.png image' });
    await expect(last).toBeVisible();
    await last.click();
    await expect(page.getByAltText('Preview study-19.png')).toBeVisible();
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
    await page.getByRole('button', { name: 'Tools', exact: true }).click();
    await page.getByLabel('Search media and captions').fill('blue.png');
    await expect(
      page.getByRole('button', { name: 'blue.png image' }),
    ).toBeVisible();
    await page.getByLabel('Search media and captions').fill('no such media');
    await expect(
      page.getByText('No matching media', { exact: true }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Close tools', exact: true })
      .click();
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
    await page.getByRole('button', { name: 'Create managed dataset' }).click();
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
      timeout: 45_000,
    });
    const tile = page.getByRole('button', { name: 'red.png image' });
    await expect(
      page.getByLabel('Caption for red.png', { exact: true }),
    ).toHaveValue(/红/);
    await page.getByRole('button', { name: 'Add media', exact: true }).click();
    await expect(page.getByLabel('Upload files')).toBeHidden();
    const tools = page.getByRole('button', { name: 'Tools', exact: true });
    await tools.focus();
    await page.keyboard.press('Enter');
    await page.getByLabel('Search media and captions').focus();
    await page.keyboard.press('Escape');
    await expect(tools).toBeFocused();
    await expect
      .poll(() =>
        tile.evaluate((element) => element.getBoundingClientRect().top),
      )
      .toBeLessThan(80);
    await expect(page.getByLabel('Search media and captions')).toBeHidden();
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
          tile.evaluate((element) => element.getBoundingClientRect().top),
        )
        .toBeLessThan(width < 550 ? 160 : 80);
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
    page.once('dialog', async (dialog) => {
      expect(dialog.message()).toContain('Portrait collection');
      expect(dialog.message()).toContain(
        'Media files, captions and recovery history stay on disk',
      );
      await dialog.dismiss();
    });
    await page
      .getByRole('button', { name: 'Delete Portrait collection' })
      .click();
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
    page.once('dialog', (dialog) => dialog.accept());
    await page
      .getByRole('button', { name: 'Delete Portrait collection' })
      .click();
    await expect(page.getByRole('alert')).toContainText(
      'Fixture deletion failed',
    );
    await expect(
      page.getByRole('button', { name: /Portrait collection.*1 media/ }),
    ).toBeVisible();
    await page.unroute(`**/api/datasets/${stored.id}`);
    page.once('dialog', (dialog) => dialog.accept());
    await page
      .getByRole('button', { name: 'Delete Portrait collection' })
      .click();
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
