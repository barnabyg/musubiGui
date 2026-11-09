import { test, expect, type Page } from '@playwright/test';
import { datasetHarness } from './datasetHarness';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';

async function openDataset(
  page: Page,
  app: Awaited<ReturnType<typeof datasetHarness>>,
) {
  await page.goto(app.url);
  await page.getByRole('button', { name: 'Datasets', exact: true }).click();
  await page.getByRole('button', { name: 'New dataset' }).click();
  await page
    .getByLabel('Dataset name', { exact: true })
    .fill('Preview captions');
  await page
    .getByRole('radio', { name: 'Use existing folder', exact: true })
    .check();
  await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
  await page
    .getByRole('checkbox', { name: 'Index this entire dataset root' })
    .check();
  await page.getByRole('button', { name: 'Use existing folder' }).click();
  await expect(page.getByText('Scan: complete 4/4')).toBeVisible({
    timeout: 240_000,
  });
}

test('preview keyboard navigation keeps caption focus across consecutive items', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await openDataset(page, app);
    await page.getByRole('button', { name: 'blue.png image' }).click();
    const caption = page.getByLabel('Caption', { exact: true });
    await caption.fill('Saved before moving');
    await page.keyboard.press('Alt+ArrowRight');
    await expect(page.locator('.media-viewer h2')).toHaveText('invalid.mp4');
    await expect(caption).toBeFocused();
    await page.keyboard.press('Alt+ArrowRight');
    await expect(page.locator('.media-viewer h2')).toHaveText('motion.mp4');
    await expect(caption).toBeFocused();
    await page.keyboard.press('Alt+ArrowLeft');
    await expect(page.locator('.media-viewer h2')).toHaveText('invalid.mp4');
    await expect(caption).toBeFocused();
    await page.keyboard.press('Alt+ArrowLeft');
    await expect(page.locator('.media-viewer h2')).toHaveText('blue.png');
    await expect(caption).toHaveValue('Saved before moving');
    await expect(caption).toBeFocused();
    await page.getByRole('button', { name: 'Close preview' }).click();
    await page.getByLabel('Search media and captions').fill('.mp4');
    await page.getByRole('button', { name: 'invalid.mp4 video' }).click();
    await page.keyboard.press('Alt+ArrowRight');
    await expect(page.locator('.media-viewer h2')).toHaveText('motion.mp4');
    await expect(caption).toBeFocused();
    await expect(
      page.getByRole('button', { name: 'Next', exact: true }),
    ).toBeDisabled();
    await page.keyboard.press('Alt+ArrowLeft');
    await expect(page.locator('.media-viewer h2')).toHaveText('invalid.mp4');
    await expect(caption).toBeFocused();
  } finally {
    await app.cleanup();
  }
});

test('narrow preview keeps media, caption and navigation together and provides a disabled-caption fallback', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await writeFile(path.join(app.media, 'red.txt'), Buffer.from([0xff]));
    await page.setViewportSize({ width: 390, height: 844 });
    await openDataset(page, app);
    await page.getByRole('button', { name: 'blue.png image' }).click();
    await expect(page.getByLabel('Dataset gallery')).toBeHidden();
    await expect(page.getByAltText('Preview blue.png')).toBeInViewport();
    await expect(page.getByLabel('Caption', { exact: true })).toBeInViewport();
    await expect(
      page.getByRole('button', { name: 'Next', exact: true }),
    ).toBeInViewport();
    await expect(
      page.getByText('Alt+Left / Alt+Right: previous / next caption'),
    ).toBeVisible();
    await page.keyboard.press('Alt+ArrowRight');
    await expect(page.locator('.media-viewer h2')).toHaveText('invalid.mp4');
    await expect(page.getByLabel('Caption', { exact: true })).toBeFocused();
    await page.keyboard.press('Alt+ArrowRight');
    await expect(page.locator('.media-viewer h2')).toHaveText('motion.mp4');
    await page.keyboard.press('Alt+ArrowRight');
    await expect(page.locator('.media-viewer h2')).toHaveText('red.png');
    await expect(page.getByLabel('Caption', { exact: true })).toBeDisabled();
    await expect(page.locator('.media-viewer')).toBeFocused();
    await page.keyboard.press('Alt+ArrowLeft');
    await expect(page.locator('.media-viewer h2')).toHaveText('motion.mp4');
    await expect(page.getByLabel('Caption', { exact: true })).toBeFocused();
    await page.getByRole('button', { name: 'Close preview' }).click();
    await expect(page.getByLabel('Dataset gallery')).toBeVisible();
    // Reflow can recycle the selected tile; the keyboard-operable gallery is a fallback.
    await expect
      .poll(() =>
        page
          .getByLabel('Dataset gallery')
          .evaluate(
            (gallery) =>
              gallery === document.activeElement ||
              gallery.contains(document.activeElement),
          ),
      )
      .toBe(true);
  } finally {
    await app.cleanup();
  }
});

test('failed preview navigation retains the draft and caption focus, and close returns to the tile', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await openDataset(page, app);
    await page.getByRole('button', { name: 'blue.png image' }).click();
    await page.route('**/caption', async (route) => {
      if (route.request().method() === 'PUT')
        await route.fulfill({
          status: 500,
          json: { detail: 'Fixture write denied' },
        });
      else await route.continue();
    });
    const caption = page.getByLabel('Caption', { exact: true });
    await caption.fill('Retained while navigation fails');
    await page.getByRole('button', { name: 'Next', exact: true }).click();
    await expect(page.locator('.media-viewer [role="alert"]')).toContainText(
      'Fixture write denied',
    );
    await expect(page.locator('.media-viewer h2')).toHaveText('blue.png');
    await expect(caption).toHaveValue('Retained while navigation fails');
    await expect(caption).toBeFocused();
    await page.getByRole('button', { name: 'Close preview' }).click();
    await expect(caption).toBeFocused();
    await page.unroute('**/caption');
    await page.getByRole('button', { name: 'Next', exact: true }).click();
    await expect(page.locator('.media-viewer h2')).toHaveText('invalid.mp4');
    await page.getByRole('button', { name: 'Close preview' }).click();
    await expect(
      page.getByRole('button', { name: 'invalid.mp4 video' }),
    ).toBeFocused();
  } finally {
    await app.cleanup();
  }
});
