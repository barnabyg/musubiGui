import { test, expect } from '@playwright/test';
import path from 'node:path';
import { datasetHarness } from './datasetHarness';

test('prepare H3 videos creates a playable dataset with preserved captions and supervised audio', async ({
  page,
  request,
}) => {
  const app = await datasetHarness();
  try {
    const settings = await (
      await request.get(`${app.url}/api/settings`)
    ).json();
    await request.put(`${app.url}/api/settings`, {
      data: { ...settings, dataset_roots: [app.fixture] },
    });
    const registered = await request.post(`${app.url}/api/datasets`, {
      data: {
        name: 'Preparation sources',
        path: path.join(app.fixture, 'videos'),
      },
    });
    expect(registered.ok()).toBeTruthy();
    const source = await registered.json();
    await page.goto(`${app.url}/#/datasets/${source.id}`);
    await expect(page.getByText('Scan: complete 1/1')).toBeVisible({
      timeout: 240_000,
    });
    const caption = page.getByLabel('Caption for motion.mp4', { exact: true });
    await caption.fill('A brightening field for H3.');
    await page
      .getByRole('button', { name: 'Dataset settings', exact: true })
      .click();
    await page.getByText('Prepare videos for H3', { exact: true }).click();
    await page
      .getByLabel('Prepared dataset name', { exact: true })
      .fill('H3 ready');
    await expect(page.getByLabel('H3 preparation width')).toHaveValue('640');
    await expect(page.getByLabel('H3 preparation height')).toHaveValue('352');
    await page
      .getByRole('button', { name: 'Prepare H3 dataset', exact: true })
      .click();
    const result = page.getByLabel('H3 preparation result');
    await expect(result).toContainText(
      'Preparation: complete · 1/1 processed · 1 prepared',
      { timeout: 240_000 },
    );
    await expect(result).toContainText('motion.mp4: prepared');
    // Reloading recovers the batch status; conversion doesn't depend on keeping the panel open.
    await page.reload();
    await page
      .getByRole('button', { name: 'Dataset settings', exact: true })
      .click();
    await page.getByText('Prepare videos for H3', { exact: true }).click();
    await expect(result).toContainText('1 prepared');
    await page
      .getByRole('link', { name: 'Open prepared dataset', exact: true })
      .click();
    await expect(page.getByText('Scan: complete 1/1')).toBeVisible({
      timeout: 240_000,
    });
    await expect(
      page.getByRole('textbox', { name: /^Caption for motion-/ }),
    ).toHaveValue('A brightening field for H3.');
    await page.getByRole('button', { name: /^motion-.* video/ }).click();
    const video = page.locator('.media-viewer video');
    await expect(video).toBeVisible();
    await video.evaluate(async (element: HTMLVideoElement) => {
      element.muted = true;
      await element.play();
    });
    await expect
      .poll(() =>
        video.evaluate((element: HTMLVideoElement) => element.currentTime),
      )
      .toBeGreaterThan(0);
    await page
      .getByText('H3 audio and media inspector', { exact: true })
      .click();
    await page.getByRole('button', { name: 'Inspect H3 media' }).click();
    await expect(page.getByText('Audio coverage: 100.0%')).toBeVisible({
      timeout: 240_000,
    });
    await expect(page.getByText('Audio source: sidecar')).toBeVisible();
    const datasets = await (
      await request.get(`${app.url}/api/datasets`)
    ).json();
    const prepared = datasets.find(
      (item: { name: string }) => item.name === 'H3 ready',
    );
    const items = await (
      await request.get(`${app.url}/api/datasets/${prepared.id}/items`)
    ).json();
    expect(items[0].metadata).toMatchObject({
      width: 640,
      height: 352,
      fps: 24,
      frames: 124,
    });
    expect(
      (await request.get(`${app.url}/api/datasets/${source.id}/items`)).ok(),
    ).toBeTruthy();
  } finally {
    await app.cleanup();
  }
});
