import { expect, test } from '@playwright/test';
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { datasetHarness } from './datasetHarness';

test('8, 32 and 64 item galleries expose current captions and save one edit', async ({
  page,
  request,
}) => {
  const app = await datasetHarness();
  try {
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    for (const size of [8, 32, 64]) {
      const folder = path.join(app.media, `gallery-${size}`);
      await mkdir(folder);
      await Promise.all(
        Array.from({ length: size }, async (_, index) => {
          const name = `image-${String(index).padStart(2, '0')}`;
          await copyFile(
            path.join(app.media, 'blue.png'),
            path.join(folder, `${name}.png`),
          );
          await writeFile(
            path.join(folder, `${name}.txt`),
            `Caption ${index}`,
            'utf8',
          );
        }),
      );
      const created = await request.post(`${app.url}/api/datasets`, {
        data: { name: `Gallery ${size}`, path: folder },
      });
      expect(created.ok()).toBeTruthy();
      const dataset = (await created.json()) as { id: string };
      const datasetUrl = `${app.url}/api/datasets/${dataset.id}`;
      await expect
        .poll(
          async () => {
            const response = await request.get(datasetUrl);
            return ((await response.json()) as { scan: { state: string } }).scan
              .state;
          },
          { timeout: 240_000 },
        )
        .toBe('complete');
      await writeFile(
        path.join(folder, 'image-00.txt'),
        'External gallery edit',
        'utf8',
      );
      const apiMs = [];
      for (let attempt = 0; attempt < 3; attempt++) {
        const start = performance.now();
        const response = await request.get(`${datasetUrl}/items`);
        expect(response.ok()).toBeTruthy();
        expect(await response.json()).toHaveLength(size);
        apiMs.push(Math.round(performance.now() - start));
      }
      await page.reload();
      const row = page.getByRole('button', {
        name: new RegExp(`Gallery ${size}.*${size} media`),
      });
      await expect(row).toBeVisible();
      const start = performance.now();
      await row.click();
      const caption = page.getByRole('textbox', {
        name: 'Caption for image-00.png',
        exact: true,
      });
      await expect(caption).toBeVisible();
      await expect(caption).toHaveValue('External gallery edit');
      const galleryMs = Math.round(performance.now() - start);
      console.log(JSON.stringify({ size, apiMs, galleryMs }));
      const image = page.getByRole('img', {
        name: 'image-00.png',
        exact: true,
      });
      await expect
        .poll(() =>
          image.evaluate((element: HTMLImageElement) => element.naturalWidth),
        )
        .toBeGreaterThan(0);
      await caption.fill(`Saved in gallery ${size}`);
      await page.getByRole('button', { name: 'Back to datasets' }).click();
      await expect
        .poll(() => readFile(path.join(folder, 'image-00.txt'), 'utf8'))
        .toBe(`Saved in gallery ${size}`);
    }
  } finally {
    await app.cleanup();
  }
});
