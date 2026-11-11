import { expect, test } from '@playwright/test';
import {
  copyFile,
  mkdir,
  readFile,
  readdir,
  writeFile,
} from 'node:fs/promises';
import path from 'node:path';
import type { Job } from '../../frontend/jobTypes';
import { runWorkspace } from './runHarness';

test.use({ actionTimeout: 240_000 });

for (const family of ['wan', 'h3'] as const) {
  for (const media of ['images', 'videos', 'combined'] as const) {
    test(`${family} prepares ${media} with media-appropriate controls and frozen sections`, async ({
      page,
    }) => {
      const app = await runWorkspace();
      try {
        const original = app.manifest.jobs.find(
          (job) => job.family === family,
        )!;
        const folder = path.join(app.fixture, 'datasets/images');
        await mkdir(folder);
        await copyFile(
          path.join(app.fixture, 'library/media 资料/blue.png'),
          path.join(folder, 'motion.png'),
        );
        await writeFile(path.join(folder, 'motion.txt'), 'A still. No sound.');
        const images = (await app.post('/api/datasets', {
          name: 'Character images',
          path: folder,
        })) as { id: string };
        await app.post(`/api/datasets/${images.id}/scan?wait=true`);
        const saved = (await (
          await page.request.get(`${app.url}/api/jobs/${original.id}`)
        ).json()) as Job;
        // Stale video settings in a saved image job must not block preparation.
        const updated = await page.request.put(
          `${app.url}/api/jobs/${saved.id}`,
          {
            data: {
              revision: saved.revision,
              name: saved.name,
              family,
              datasets: [],
              config: {
                ...saved.config,
                sample_enabled: false,
                ...(media === 'images'
                  ? { frames: 1, frame_extraction: 'slide', frame_stride: 0 }
                  : {}),
              },
            },
          },
        );
        expect(updated.ok(), await updated.text()).toBe(true);
        await page.goto(`${app.url}/#/jobs/${saved.id}`);
        const add = async (id: string) => {
          await page
            .getByRole('combobox', { name: 'Add a dataset' })
            .selectOption(id);
          await page
            .getByRole('button', { name: 'Add dataset', exact: true })
            .click();
        };
        if (media !== 'videos') {
          await add(images.id);
          await page.getByLabel('Repeats for Character images').fill('2');
        }
        if (media !== 'images') await add(original.dataset_id);
        await page.getByText('Advanced controls', { exact: true }).click();
        const extraction = page.getByRole('combobox', {
          name: 'Frame extraction',
          exact: true,
        });
        if (media === 'images') {
          await expect(extraction).toHaveCount(0);
          await expect(
            page.getByRole('combobox', { name: 'Audio policy' }),
          ).toHaveCount(0);
          await expect(page.getByLabel('Frames', { exact: true })).toHaveCount(
            0,
          );
          await expect(
            page.getByText('Images train as single frames.', { exact: false }),
          ).toBeVisible();
        } else {
          await expect(extraction).toBeVisible();
          if (family === 'h3')
            await expect(
              page.getByRole('combobox', { name: 'Audio policy' }),
            ).toBeVisible();
        }
        await page
          .getByRole('button', { name: 'Save draft', exact: true })
          .click();
        await expect(page.getByRole('status')).toContainText('Draft saved');
        await app.crash();
        await app.start();
        await page.reload();
        await expect(
          page
            .getByRole('list', { name: 'Datasets in this job' })
            .getByRole('listitem'),
        ).toHaveCount(media === 'combined' ? 2 : 1);
        if (media !== 'videos')
          await expect(
            page.getByLabel('Repeats for Character images'),
          ).toHaveValue('2');
        await page
          .getByRole('button', { name: 'Review configuration', exact: true })
          .click();
        const review = page.getByRole('region', {
          name: 'Configuration review',
        });
        await expect(review).toContainText('Review passed', {
          timeout: 240_000,
        });
        const rows = review
          .getByRole('table', { name: 'Training sections' })
          .locator('tbody tr');
        await expect(rows).toHaveCount(media === 'combined' ? 2 : 1);
        if (family === 'h3' && media !== 'videos')
          await expect(review).toContainText('experimental');
        if (media === 'images')
          await expect(
            review.getByRole('heading', { name: 'Audio coverage' }),
          ).toHaveCount(0);
        await page
          .getByRole('button', { name: 'Prepare run', exact: true })
          .click();
        await expect
          .poll(
            async () =>
              (await app.records()).find((run) => run.job.id === saved.id)
                ?.status,
            { timeout: 240_000 },
          )
          .toBe('prepared');
        const run = (await app.records()).find(
          (item) => item.job.id === saved.id,
        )!;
        const toml = await readFile(
          path.join(run.directory, 'dataset.toml'),
          'utf8',
        );
        expect(toml.includes('image_directory')).toBe(media !== 'videos');
        expect(toml.includes('target_frames')).toBe(media !== 'images');
        expect(run.manifest.items).toHaveLength(media === 'combined' ? 2 : 1);
        const captions = run.manifest.items.map((item) => item.caption.text);
        if (media !== 'videos')
          expect(captions).toContain('A still. No sound.');
        if (media !== 'images')
          expect(captions).toContain(
            'A blue field gradually brightens. A steady tone plays.',
          );
        const cache = path.join(app.fixture, 'cache');
        const cacheFiles = await readdir(cache, { recursive: true });
        expect(
          cacheFiles.filter((file) => file.endsWith('.safetensors')).length,
        ).toBeGreaterThanOrEqual(media === 'combined' ? 4 : 2);
      } finally {
        await app.close();
      }
    });
  }
}

test('image sample length and video controls return when needed without changing saved settings', async ({
  page,
}) => {
  const app = await runWorkspace({ datasetMedia: true });
  try {
    const images = app.manifest.jobs.find(
      (job) => job.name === 'H3 images only',
    )!;
    const videos = app.manifest.jobs.find(
      (job) => job.name === 'H3 acceptance 1',
    )!;
    await page.goto(`${app.url}/#/jobs/${images.id}`);
    await page.getByText('Advanced controls', { exact: true }).click();
    const frames = page.getByLabel('Sample frames', { exact: true });
    await expect(frames).toHaveValue('124');
    await frames.fill('1');
    const inspect = page.getByRole('button', {
      name: 'Review configuration',
      exact: true,
    });
    await inspect.click();
    const review = page.getByRole('region', { name: 'Configuration review' });
    await expect(review).toContainText('Frames: H3 requires');
    await review.getByRole('button', { name: /^Frames:/ }).click();
    await expect(frames).toBeFocused();
    await page
      .getByRole('checkbox', { name: 'Generate a final sample', exact: true })
      .uncheck();
    await expect(frames).toHaveCount(0);
    await inspect.click();
    await expect(review).toContainText('Review passed');
    await page
      .getByRole('combobox', { name: 'Add a dataset' })
      .selectOption(videos.dataset_id);
    await page
      .getByRole('button', { name: 'Add dataset', exact: true })
      .click();
    await expect(
      page.getByRole('combobox', { name: 'Frame extraction', exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('combobox', { name: 'Audio policy' }),
    ).toBeVisible();
    await expect(page.getByLabel('Frames', { exact: true })).toHaveValue('1');
    await inspect.click();
    await expect(review).toContainText('Frames: H3 requires');
    await page.getByLabel('Frames', { exact: true }).fill('124');
    await inspect.click();
    await expect(review).toContainText('Review passed', { timeout: 240_000 });
    await expect(review).toContainText('1 supervised / 1 included');
  } finally {
    await app.close();
  }
});

test('mixed folders and matching filenames keep separate captions and caches across datasets', async ({
  page,
}) => {
  const app = await runWorkspace({ datasetMedia: true });
  try {
    const videos = app.manifest.jobs.find(
      (job) => job.name === 'WAN acceptance 1',
    )!;
    const images = app.manifest.jobs.find(
      (job) => job.name === 'H3 images only',
    )!;
    for (const extension of ['png', 'txt']) {
      await copyFile(
        path.join(app.fixture, `datasets/wan-images/image00.${extension}`),
        path.join(app.fixture, `datasets/wan/image00.${extension}`),
      );
    }
    await writeFile(
      path.join(app.fixture, 'datasets/h3-images/image00.txt'),
      'Another character. No sound.',
    );
    await app.post(`/api/datasets/${videos.dataset_id}/scan?wait=true`);
    await app.post(`/api/datasets/${images.dataset_id}/scan?wait=true`);
    await page.goto(`${app.url}/#/jobs/${videos.id}`);
    await page
      .getByRole('combobox', { name: 'Add a dataset' })
      .selectOption(images.dataset_id);
    await page
      .getByRole('button', { name: 'Add dataset', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const review = page.getByRole('region', { name: 'Configuration review' });
    await expect(review).toContainText('Review passed', { timeout: 240_000 });
    await expect(
      review
        .getByRole('table', { name: 'Training sections' })
        .locator('tbody tr'),
    ).toHaveCount(3);
    await page
      .getByRole('button', { name: 'Prepare run', exact: true })
      .click();
    await expect
      .poll(
        async () =>
          (await app.records()).find((run) => run.job.id === videos.id)?.status,
        { timeout: 240_000 },
      )
      .toBe('prepared');
    const run = (await app.records()).find(
      (item) => item.job.id === videos.id,
    )!;
    const snapshot = JSON.parse(
      await readFile(path.join(run.directory, 'snapshot.json'), 'utf8'),
    ) as {
      cache: string;
      manifest: {
        items: {
          name: string;
          dataset_id: string;
          snapshot_path: string;
          caption: { text: string };
        }[];
      };
    };
    const copies = snapshot.manifest.items.filter(
      (item) => item.name === 'image00.png',
    );
    expect(copies).toHaveLength(2);
    expect(new Set(copies.map((item) => item.snapshot_path)).size).toBe(2);
    expect(
      copies.find((item) => item.dataset_id === images.dataset_id)?.caption
        .text,
    ).toBe('Another character. No sound.');
    for (const item of copies)
      expect(
        await readFile(item.snapshot_path.replace(/\.png$/, '.txt'), 'utf8'),
      ).toBe(item.caption.text);
    for (const [folder, count] of [
      ['dataset-1-images', 1],
      ['dataset-1', 1],
      ['dataset-2-images', 2],
    ] as const) {
      const files = await readdir(path.join(snapshot.cache, folder));
      expect(
        files.filter((file) => file.endsWith('_wan.safetensors')),
      ).toHaveLength(count);
    }
  } finally {
    await app.close();
  }
});
