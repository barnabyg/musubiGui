import { test, expect } from '@playwright/test';
import { datasetHarness } from './datasetHarness';

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
