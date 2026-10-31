import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { datasetHarness } from './datasetHarness';

test('picked files publish media and captions, and a rejected upload can be cancelled', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page.getByRole('button', { name: 'New dataset' }).click();
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('Uploads library');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page
      .getByRole('button', { name: 'Create a new folder for uploads' })
      .click();
    await expect(
      page.getByRole('heading', { name: 'Upload media and captions' }),
    ).toBeVisible();
    await page
      .getByLabel('Upload files')
      .setInputFiles([
        path.join(app.media, 'red.png'),
        path.join(app.media, 'red.txt'),
        path.join(app.media, 'invalid.mp4'),
      ]);
    await page.getByRole('button', { name: 'Upload selected files' }).click();
    // Uploaded is reported before the final gallery refresh has finished.
    await expect(page.getByLabel('Upload files')).toBeEnabled({
      timeout: 240_000,
    });
    await expect(
      page.getByLabel('Upload result red.png', { exact: true }),
    ).toContainText('Uploaded');
    await expect(
      page.getByLabel('Upload result red.txt', { exact: true }),
    ).toContainText('Uploaded');
    await expect(
      page.getByLabel('Upload result invalid.mp4', { exact: true }),
    ).toContainText('Failed');
    await expect(
      page.getByLabel('Upload result invalid.mp4', { exact: true }),
    ).toContainText('decode');
    await expect(
      page.getByRole('button', { name: 'red.png image · caption' }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'red.png image' }).click();
    await expect(page.getByAltText('Preview red.png')).toBeVisible();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      (await readFile(path.join(app.media, 'red.txt'), 'utf8')).replace(
        /\r\n/g,
        '\n',
      ),
    );
    await page
      .getByRole('button', { name: 'Cancel partial invalid.mp4' })
      .click();
    await expect(
      page.getByRole('button', { name: 'Cancel partial invalid.mp4' }),
    ).toHaveCount(0);
  } finally {
    await app.cleanup();
  }
});
