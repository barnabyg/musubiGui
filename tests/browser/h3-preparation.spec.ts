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

test('mixed videos keep their orientation and the H3 training handoff enables bucketing', async ({
  page,
  request,
}) => {
  const app = await datasetHarness({ h3Portrait: true });
  try {
    const settings = await (
      await request.get(`${app.url}/api/settings`)
    ).json();
    await request.put(`${app.url}/api/settings`, {
      data: { ...settings, dataset_roots: [app.fixture] },
    });
    const registered = await request.post(`${app.url}/api/datasets`, {
      data: {
        name: 'Mixed orientations',
        path: path.join(app.fixture, 'videos'),
      },
    });
    expect(registered.ok()).toBeTruthy();
    const source = await registered.json();
    await page.goto(`${app.url}/#/datasets/${source.id}`);
    await expect(page.getByText('Scan: complete 2/2')).toBeVisible({
      timeout: 240_000,
    });
    await page
      .getByRole('button', { name: 'Dataset settings', exact: true })
      .click();
    await page.getByText('Prepare videos for H3', { exact: true }).click();
    await expect(page.getByLabel('H3 orientation buckets')).toHaveValue('auto');
    await expect(page.getByLabel('H3 aspect ratio handling')).toHaveValue(
      'pad',
    );
    await expect(
      page.getByText(/Buckets: landscape 640 × 352; portrait 352 × 640/),
    ).toBeVisible();
    await page.getByLabel('H3 orientation buckets').selectOption('fixed');
    await expect(page.getByText(/Output: 640 × 352/)).toBeVisible();
    await page.getByLabel('H3 orientation buckets').selectOption('auto');
    await page
      .getByRole('button', { name: 'Prepare H3 dataset', exact: true })
      .click();
    const result = page.getByLabel('H3 preparation result');
    await expect(result).toContainText('2/2 processed · 2 prepared', {
      timeout: 240_000,
    });
    await expect(result).toContainText('portrait.mp4: prepared · 352 × 640');
    await expect(result).toContainText('motion.mp4: prepared · 640 × 352');
    await page
      .getByRole('link', { name: 'Open prepared dataset', exact: true })
      .click();
    await expect(page.getByText('Scan: complete 2/2')).toBeVisible({
      timeout: 240_000,
    });
    await page.getByRole('button', { name: /^portrait-.* video/ }).click();
    const video = page.locator('.media-viewer video');
    await video.evaluate(async (element: HTMLVideoElement) => {
      element.muted = true;
      await element.play();
    });
    await expect
      .poll(() =>
        video.evaluate((element: HTMLVideoElement) => element.currentTime),
      )
      .toBeGreaterThan(0);
    const head = await video.evaluate((element: HTMLVideoElement) => {
      const canvas = document.createElement('canvas');
      canvas.width = element.videoWidth;
      canvas.height = element.videoHeight;
      const context = canvas.getContext('2d')!;
      context.drawImage(element, 0, 0);
      return {
        width: canvas.width,
        height: canvas.height,
        pixel: [...context.getImageData(176, 50, 1, 1).data],
      };
    });
    expect(head.width).toBe(352);
    expect(head.height).toBe(640);
    expect(head.pixel[0]).toBeGreaterThan(200);
    expect(head.pixel[2]).toBeLessThan(60);
    await page.getByText('Create training job', { exact: true }).click();
    await page
      .getByRole('button', { name: 'Create H3 job', exact: true })
      .click();
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      /Mixed orientations/,
    );
    const jobs = await (await request.get(`${app.url}/api/jobs`)).json();
    expect(jobs).toHaveLength(1);
    expect(jobs[0].config.enable_bucket).toBe(true);
    expect(jobs[0].config.bucket_no_upscale).toBe(true);
  } finally {
    await app.cleanup();
  }
});
