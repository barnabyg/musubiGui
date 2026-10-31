import { test, expect } from '@playwright/test';
import { execFile } from 'node:child_process';
import { mkdtemp, readFile, rm, unlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { localBackend } from './backend';

type FixtureRun = {
  id: string;
  family: string;
  revision: number;
  directory: string;
};

test('Wan and H3 output playback comparison download and missing states retain run provenance', async ({
  page,
}) => {
  const parent = await mkdtemp(path.join(tmpdir(), 'musubi-outputs-'));
  const fixture = path.join(parent, 'fixture');
  const python = path.resolve('.venv/Scripts/python.exe');
  const { url, start, stop } = await localBackend({
    state: path.join(fixture, 'state'),
    readyRoute: '/api/runs',
    args: ['--acceptance-fixture'],
  });
  try {
    await promisify(execFile)(python, [
      '-m',
      'musubi_gui.output_fixture',
      fixture,
    ]);
    const records = JSON.parse(
      await readFile(path.join(fixture, 'output-fixture.json'), 'utf8'),
    ) as { runs: FixtureRun[] };
    const h3 = records.runs.find(
      (run) => run.family === 'h3' && run.revision === 1,
    )!;
    const nextH3 = records.runs.find(
      (run) => run.family === 'h3' && run.revision === 2,
    )!;
    await start();
    await page.goto(url);
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    await page
      .getByRole('button', { name: 'h3-revision-1 · H3 · r1', exact: true })
      .click();
    await expect(
      page.getByRole('heading', { name: 'Run outputs', exact: true }),
    ).toBeVisible();
    await page
      .getByRole('button', {
        name: 'samples/sample.mp4 · available',
        exact: true,
      })
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
      page.getByLabel('Selected output', { exact: true }),
    ).toContainText(h3.id);
    await expect(
      page.getByLabel('Selected output', { exact: true }),
    ).toContainText('"audio"');
    await page
      .getByRole('button', {
        name: 'samples/tone.wav · available',
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
    await page
      .getByRole('button', {
        name: 'checkpoints/fixture.safetensors · available',
        exact: true,
      })
      .click();
    await expect(
      page.getByLabel('Selected output', { exact: true }),
    ).toContainText('"training_state": false');
    const download = page.waitForEvent('download');
    await page
      .getByRole('link', { name: 'Download output', exact: true })
      .click();
    expect((await download).suggestedFilename()).toBe(
      'h3-revision-1-r1.safetensors',
    );
    await page
      .getByRole('combobox', { name: 'Compare with run', exact: true })
      .selectOption(nextH3.id);
    await page
      .getByRole('button', { name: 'Compare inputs', exact: true })
      .click();
    const comparison = page.getByLabel('Run comparison', { exact: true });
    await expect(comparison).toContainText('job.config.rank');
    await expect(comparison).toContainText(
      'dataset.items.fixture-item.caption.text',
    );
    await expect(comparison).toContainText('provenance.upstream_revision');
    const listed = (await (
      await page.request.get(`${url}/api/runs/${h3.id}/outputs`)
    ).json()) as { outputs: { kind: string; file_url: string }[] };
    const checkpointUrl = listed.outputs.find(
      (output) => output.kind === 'checkpoint',
    )!.file_url;
    await unlink(path.join(h3.directory, 'checkpoints', 'fixture.safetensors'));
    await page
      .getByRole('button', { name: 'Refresh outputs', exact: true })
      .click();
    await expect(
      page.getByRole('button', {
        name: 'checkpoints/fixture.safetensors · missing',
        exact: true,
      }),
    ).toBeVisible();
    expect((await page.request.get(url + checkpointUrl)).status()).toBe(404);
    await page
      .getByRole('button', { name: 'wan-revision-1 · WAN · r1', exact: true })
      .click();
    await page
      .getByRole('button', {
        name: 'samples/still.png · available',
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
        name: 'samples/sample.mp4 · available',
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
      page.getByRole('button', {
        name: 'checkpoints/fixture.safetensors · missing',
        exact: true,
      }),
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
