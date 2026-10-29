import { test, expect, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { datasetHarness } from './datasetHarness';

async function fileTransfer(page: Page, media: string, names: string[]) {
  const files = await Promise.all(
    names.map(async (name) => ({
      name,
      bytes: Array.from(await readFile(path.join(media, name))),
    })),
  );
  return page.evaluateHandle((files) => {
    const transfer = new DataTransfer();
    for (const file of files)
      transfer.items.add(new File([new Uint8Array(file.bytes)], file.name));
    return transfer;
  }, files);
}

async function createDataset(page: Page, url: string, media: string) {
  await page.goto(url);
  await page.getByRole('button', { name: 'Datasets', exact: true }).click();
  await page.getByRole('button', { name: 'New dataset' }).click();
  await page.getByLabel('Dataset name', { exact: true }).fill('Dropped media');
  await page.getByLabel('Dataset folder', { exact: true }).fill(media);
  await page.getByRole('button', { name: 'Create managed dataset' }).click();
  // Creation scans the new folder before responding, so it gets the scan deadline.
  // Stopping at an alert reports a creation error instead of a missing drop area.
  const dropArea = page.getByLabel('Dataset file drop area');
  await expect(dropArea.or(page.getByRole('alert'))).toBeVisible({
    timeout: 45_000,
  });
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(dropArea).toBeVisible();
}

test('dropped images, video and captions use managed uploads, preserve collisions and survive restart', async ({
  page,
}) => {
  const app = await datasetHarness();
  try {
    await createDataset(page, app.url, app.media);
    const dropArea = page.getByLabel('Dataset file drop area');
    await page.getByLabel('Upload files').setInputFiles({
      name: 'picker-only.png',
      mimeType: 'image/png',
      buffer: await readFile(path.join(app.media, 'red.png')),
    });
    await expect(
      page.getByLabel('Upload result picker-only.png', { exact: true }),
    ).toContainText('Ready');
    // Captions precede media in the drop to exercise the shared upload ordering.
    const transfer = await fileTransfer(page, app.media, [
      'red.txt',
      'motion.txt',
      'red.png',
      'motion.mp4',
      'invalid.mp4',
      'blue.png',
    ]);
    await page.evaluate((transfer) => {
      transfer.items.add(new File(['unsupported'], 'notes.exe'));
    }, transfer);
    await dropArea.dispatchEvent('dragenter', { dataTransfer: transfer });
    await expect(dropArea).toHaveClass(/dragging/);
    await dropArea.dispatchEvent('drop', { dataTransfer: transfer });
    await expect(dropArea).not.toHaveClass(/dragging/);
    await expect(
      page.getByLabel('Upload result red.png', { exact: true }),
    ).toContainText(/Uploading|Validating media|Uploaded/);
    // This batch validates media sequentially and refreshes the gallery per file.
    await expect(page.getByLabel('Upload files')).toBeEnabled({
      timeout: 45_000,
    });
    for (const name of [
      'red.png',
      'blue.png',
      'motion.mp4',
      'red.txt',
      'motion.txt',
    ])
      await expect(
        page.getByLabel(`Upload result ${name}`, { exact: true }),
      ).toContainText('Uploaded');
    for (const name of ['invalid.mp4', 'notes.exe'])
      await expect(
        page.getByLabel(`Upload result ${name}`, { exact: true }),
      ).toContainText('Failed');
    await expect(
      page.getByRole('button', {
        name: 'motion.mp4 video · caption',
      }),
    ).toBeVisible();
    await page.getByRole('button', { name: 'motion.mp4 video' }).click();
    await expect(page.locator('video')).toBeVisible();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      await readFile(path.join(app.media, 'motion.txt'), 'utf8'),
    );
    const datasets = (await (
      await page.request.get(`${app.url}/api/datasets`)
    ).json()) as { path: string }[];
    const stored = datasets[0].path;
    await expect(
      readFile(path.join(stored, 'picker-only.png')),
    ).rejects.toMatchObject({ code: 'ENOENT' });
    for (const name of [
      'red.png',
      'blue.png',
      'motion.mp4',
      'red.txt',
      'motion.txt',
    ])
      expect(await readFile(path.join(stored, name))).toEqual(
        await readFile(path.join(app.media, name)),
      );

    const collision = await page.evaluateHandle(() => {
      const transfer = new DataTransfer();
      transfer.items.add(new File(['replacement bytes'], 'red.png'));
      return transfer;
    });
    await dropArea.dispatchEvent('drop', { dataTransfer: collision });
    await expect(page.getByLabel('Upload files')).toBeEnabled({
      timeout: 45_000,
    });
    await expect(
      page.getByLabel('Upload result red.png', { exact: true }),
    ).toContainText('already exists');
    expect(await readFile(path.join(stored, 'red.png'))).toEqual(
      await readFile(path.join(app.media, 'red.png')),
    );
    await transfer.dispose();
    await collision.dispose();
    await app.stop();
    await app.start();
    await page.reload();
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page
      .getByRole('button', { name: /Dropped media.*3 media.*2 captioned/ })
      .click();
    await page.getByRole('button', { name: 'red.png image' }).click();
    await expect(page.getByLabel('Caption', { exact: true })).toHaveValue(
      (await readFile(path.join(app.media, 'red.txt'), 'utf8')).replace(
        /\r\n/g,
        '\n',
      ),
    );
  } finally {
    await app.cleanup();
  }
});

test('drop feedback handles child boundaries and non-file drags, and busy uploads keep their queue', async ({
  page,
}) => {
  const app = await datasetHarness();
  let releaseUpload = () => {};
  try {
    await createDataset(page, app.url, app.media);
    const dropArea = page.getByLabel('Dataset file drop area');
    const picker = page.getByLabel('Upload files');
    const transfer = await fileTransfer(page, app.media, ['blue.png']);
    await dropArea.dispatchEvent('dragenter', { dataTransfer: transfer });
    await picker.dispatchEvent('dragenter', { dataTransfer: transfer });
    await picker.dispatchEvent('dragleave', { dataTransfer: transfer });
    await expect(dropArea).toHaveClass(/dragging/);
    await dropArea.dispatchEvent('dragleave', { dataTransfer: transfer });
    await expect(dropArea).not.toHaveClass(/dragging/);
    await picker.setInputFiles(path.join(app.media, 'red.png'));
    const text = await page.evaluateHandle(() => {
      const transfer = new DataTransfer();
      transfer.setData('text/uri-list', 'https://example.com/image.png');
      return transfer;
    });
    await dropArea.dispatchEvent('dragenter', { dataTransfer: text });
    await expect(dropArea).not.toHaveClass(/dragging/);
    await dropArea.dispatchEvent('drop', { dataTransfer: text });
    await expect(
      page.getByLabel('Upload result red.png', { exact: true }),
    ).toContainText('Ready');
    const held = new Promise<void>((resolve) => {
      releaseUpload = resolve;
    });
    await page.route('**/uploads/*', async (route) => {
      if (route.request().method() === 'PUT') await held;
      await route.continue();
    });
    await dropArea.dispatchEvent('drop', { dataTransfer: transfer });
    await expect(
      page.getByLabel('Upload result red.png', { exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByLabel('Upload result blue.png', { exact: true }),
    ).toContainText('Uploading');
    await expect(picker).toBeDisabled();
    await page.getByRole('button', { name: 'Tools', exact: true }).click();
    await expect(picker).toBeHidden();
    await page.getByRole('button', { name: 'Add media', exact: true }).click();
    await expect(picker).toBeDisabled();
    await expect(
      page.getByLabel('Upload result blue.png', { exact: true }),
    ).toContainText('Uploading');
    await expect(dropArea).toHaveAttribute('aria-disabled', 'true');
    const replacement = await fileTransfer(page, app.media, ['red.png']);
    await dropArea.dispatchEvent('dragenter', { dataTransfer: replacement });
    await expect(dropArea).not.toHaveClass(/dragging/);
    await dropArea.dispatchEvent('drop', { dataTransfer: replacement });
    await expect(
      page.getByLabel('Upload result red.png', { exact: true }),
    ).toHaveCount(0);
    releaseUpload();
    await expect(picker).toBeEnabled({ timeout: 45_000 });
    await expect(
      page.getByLabel('Upload result blue.png', { exact: true }),
    ).toContainText('Uploaded');
    await expect(dropArea).toHaveAttribute('aria-disabled', 'false');
    // Re-select the file previously chosen before the drop replaced the queue.
    await picker.setInputFiles(path.join(app.media, 'red.png'));
    await expect(
      page.getByLabel('Upload result red.png', { exact: true }),
    ).toContainText('Ready');
    await expect(
      page.getByLabel('Upload result blue.png', { exact: true }),
    ).toHaveCount(0);
    await transfer.dispose();
    await replacement.dispose();
    await text.dispose();
  } finally {
    releaseUpload();
    await app.cleanup();
  }
});

test('rapid drops keep the first batch while caption saving is pending', async ({
  page,
}) => {
  const app = await datasetHarness();
  let releaseCaption = () => {};
  try {
    await createDataset(page, app.url, app.media);
    const area = page.getByLabel('Dataset file drop area');
    const initial = await fileTransfer(page, app.media, ['blue.png']);
    await area.dispatchEvent('drop', { dataTransfer: initial });
    await expect(
      page.getByLabel('Upload result blue.png', { exact: true }),
    ).toContainText('Uploaded', { timeout: 45_000 });
    await expect(page.getByLabel('Upload files')).toBeEnabled({
      timeout: 45_000,
    });
    await page.getByRole('button', { name: 'blue.png image' }).click();
    const held = new Promise<void>((resolve) => {
      releaseCaption = resolve;
    });
    await page.route('**/caption', async (route) => {
      if (route.request().method() === 'PUT') await held;
      await route.continue();
    });
    await page
      .getByLabel('Caption', { exact: true })
      .fill('Caption before the next upload.');
    const first = await fileTransfer(page, app.media, ['red.png']);
    const second = await fileTransfer(page, app.media, ['motion.mp4']);
    await area.evaluate(
      (element, { first, second }) => {
        for (const dataTransfer of [first, second])
          element.dispatchEvent(
            new DragEvent('drop', {
              bubbles: true,
              cancelable: true,
              dataTransfer,
            }),
          );
      },
      { first, second },
    );
    await expect(page.getByLabel('Upload files')).toBeDisabled();
    await expect(
      page.getByLabel('Upload result red.png', { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByLabel('Upload result motion.mp4', { exact: true }),
    ).toHaveCount(0);
    releaseCaption();
    await expect(
      page.getByLabel('Upload result red.png', { exact: true }),
    ).toContainText('Uploaded', { timeout: 45_000 });
    await expect(page.getByLabel('Upload files')).toBeEnabled({
      timeout: 45_000,
    });
    const records = (await (
      await page.request.get(`${app.url}/api/datasets`)
    ).json()) as { path: string }[];
    expect(await readFile(path.join(records[0].path, 'blue.txt'), 'utf8')).toBe(
      'Caption before the next upload.',
    );
    await expect(
      readFile(path.join(records[0].path, 'motion.mp4')),
    ).rejects.toMatchObject({ code: 'ENOENT' });
    await initial.dispose();
    await first.dispose();
    await second.dispose();
  } finally {
    releaseCaption();
    await app.cleanup();
  }
});
