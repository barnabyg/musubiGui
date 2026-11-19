import { test, expect } from '@playwright/test';
import { mkdtemp, readFile, rm, unlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { localBackend, python } from './backend';
import { execute, openRunTab } from './runHarness';

type FixtureRun = {
  id: string;
  family: string;
  revision: number;
  directory: string;
};

test('Wan and H3 output playback and missing outputs retain run provenance', async ({
  page,
}) => {
  const parent = await mkdtemp(path.join(tmpdir(), 'musubi-outputs-'));
  const fixture = path.join(parent, 'fixture');
  const { url, start, stop } = await localBackend({
    state: path.join(fixture, 'state'),
    readyRoute: '/api/runs',
    args: ['--acceptance-fixture'],
  });
  try {
    await execute(python, ['-m', 'musubi_gui.output_fixture', fixture]);
    const records = JSON.parse(
      await readFile(path.join(fixture, 'output-fixture.json'), 'utf8'),
    ) as { runs: FixtureRun[] };
    const h3 = records.runs.find(
      (run) => run.family === 'h3' && run.revision === 1,
    )!;
    await start();
    await page.goto(url);
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    await page
      .getByRole('button', { name: 'h3-revision-1 · H3 · r1', exact: true })
      .click();
    await openRunTab(page, 'samples');
    await page
      .getByRole('button', { name: 'sample.mp4 · available', exact: true })
      .click();
    const video = page.getByLabel('Output video', { exact: true });
    await video.evaluate(async (element) => {
      const media = element as HTMLVideoElement;
      media.muted = true;
      await media.play();
    });
    await expect
      .poll(() =>
        video.evaluate((element) => (element as HTMLVideoElement).currentTime),
      )
      .toBeGreaterThan(0);
    await expect(
      page.getByLabel('Selected sample', { exact: true }),
    ).toContainText(h3.id.slice(0, 8));
    await expect(
      page.getByLabel('Selected sample', { exact: true }),
    ).toContainText('"audio"');
    await page
      .getByRole('button', {
        name: 'tone.wav · available',
        exact: true,
      })
      .click();
    const audio = page.getByLabel('Output audio', { exact: true });
    await audio.evaluate(async (element) => {
      const media = element as HTMLAudioElement;
      media.muted = true;
      await media.play();
    });
    await expect
      .poll(() =>
        audio.evaluate((element) => (element as HTMLAudioElement).currentTime),
      )
      .toBeGreaterThan(0);
    await openRunTab(page, 'overview');
    // Returning to Overview preserves the audio selection.
    await openRunTab(page, 'samples');
    await expect(audio).toBeVisible();
    const listed = (await (
      await page.request.get(`${url}/api/runs/${h3.id}/outputs`)
    ).json()) as { outputs: { kind: string; file_url: string }[] };
    const checkpointUrl = listed.outputs.find(
      (output) => output.kind === 'checkpoint',
    )!.file_url;
    await unlink(path.join(h3.directory, 'checkpoints', 'fixture.safetensors'));
    await openRunTab(page, 'overview');
    await page
      .getByRole('button', { name: 'Refresh outputs', exact: true })
      .click();
    await expect(
      page.getByText(/fixture\.safetensors: Output is missing or moved/),
    ).toBeVisible();
    expect((await page.request.get(url + checkpointUrl)).status()).toBe(404);
    await page
      .getByRole('button', { name: 'Back to runs', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'wan-revision-1 · WAN · r1', exact: true })
      .click();
    await openRunTab(page, 'samples');
    await page
      .getByRole('button', {
        name: 'still.png · available',
        exact: true,
      })
      .click();
    await expect
      .poll(() =>
        page
          .getByAltText('Sample still.png', { exact: true })
          .evaluate((element) => (element as HTMLImageElement).naturalWidth),
      )
      .toBe(256);
    await page
      .getByRole('button', {
        name: 'sample.mp4 · available',
        exact: true,
      })
      .click();
    await page
      .getByLabel('Output video', { exact: true })
      .evaluate(async (element) => {
        const media = element as HTMLVideoElement;
        media.muted = true;
        await media.play();
      });
    await expect
      .poll(() =>
        page
          .getByLabel('Output video', { exact: true })
          .evaluate((element) => (element as HTMLVideoElement).currentTime),
      )
      .toBeGreaterThan(0);
    await stop();
    await start();
    await page.reload();
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    await page
      .getByRole('button', { name: 'h3-revision-1 · H3 · r1', exact: true })
      .click();
    await expect(
      page.getByText(/fixture\.safetensors: Output is missing or moved/),
    ).toBeVisible();
  } finally {
    await stop();
    await rm(parent, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
  }
});
