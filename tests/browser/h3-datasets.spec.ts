import { test, expect } from '@playwright/test';
import path from 'node:path';
import { datasetHarness } from './datasetHarness';

test('H3 inspector plays resolved audio and identifies missing, ambiguous, malformed and misaligned sources', async ({
  page,
}) => {
  const app = await datasetHarness({ h3Audio: true });
  try {
    await page.goto(app.url);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('H3 audio library');
    await page.getByLabel('Dataset folder', { exact: true }).fill(app.media);
    await page.getByRole('button', { name: 'Register folder' }).click();
    // Eleven media probes can exceed the default DOM assertion deadline on CI.
    await expect(page.getByText('Scan: complete 11/11')).toBeVisible({
      timeout: 45_000,
    });
    const inspect = async (name: string) => {
      const gallery = page.getByLabel('Dataset gallery');
      await gallery.evaluate((element) => {
        element.scrollTop = 0;
      });
      const tile = page.getByRole('button', { name: `${name} video` });
      await expect
        .poll(async () => {
          if (await tile.count()) return true;
          await gallery.evaluate((element) => {
            element.scrollTop = element.scrollHeight;
          });
          return Boolean(await tile.count());
        })
        .toBe(true);
      await tile.click();
      await page
        .getByText('H3 audio and media inspector', { exact: true })
        .click();
      await page.getByRole('button', { name: 'Inspect H3 media' }).click();
      await expect(page.getByLabel('H3 inspection result')).toBeVisible();
    };
    await inspect('sidecar.mp4');
    await expect(page.getByText('Audio source: sidecar')).toBeVisible();
    await expect(page.getByText('Audio coverage: 100.0%')).toBeVisible();
    await page
      .getByLabel('Resolved H3 audio')
      .evaluate(async (audio: HTMLAudioElement) => {
        audio.muted = true;
        await audio.play();
      });
    await expect
      .poll(() =>
        page
          .getByLabel('Resolved H3 audio')
          .evaluate((audio: HTMLAudioElement) => audio.currentTime),
      )
      .toBeGreaterThan(0);
    await inspect('embedded.mp4');
    await expect(page.getByText('Audio source: embedded')).toBeVisible();
    await inspect('missing.mp4');
    await expect(page.getByText('Audio source: missing')).toBeVisible();
    await expect(page.getByText('Audio coverage: 0.0%')).toBeVisible();
    await inspect('ambiguous.mp4');
    await expect(page.getByRole('alert')).toContainText('Multiple same-stem');
    await page
      .getByLabel('Explicit audio path')
      .fill(path.join(app.media, 'chosen.wav'));
    await page.getByRole('button', { name: 'Apply audio source' }).click();
    await expect(page.getByText('Audio source: explicit')).toBeVisible();
    await inspect('malformed.mp4');
    await expect(page.getByRole('alert')).toContainText('decode');
    await inspect('late.mp4');
    await expect(page.getByRole('alert')).toContainText(
      'materially short or misaligned',
    );
  } finally {
    await app.cleanup();
  }
});
