import { test, expect } from '@playwright/test';
import { execFile } from 'node:child_process';
import { mkdtemp, readFile, rm, unlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { localBackend, python } from './backend';

test('comparison plays both Runs and keeps provenance and downloads when a sample is missing', async ({
  page,
}) => {
  const parent = await mkdtemp(path.join(tmpdir(), 'musubi-compare-'));
  const fixture = path.join(parent, 'fixture');
  const backend = await localBackend({
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
    const { runs } = JSON.parse(
      await readFile(path.join(fixture, 'output-fixture.json'), 'utf8'),
    ) as {
      runs: {
        id: string;
        family: string;
        revision: number;
        directory: string;
      }[];
    };
    // No samples have ever been indexed for this Run: it represents sampling disabled.
    const noSample = runs.find(
      (run) => run.family === 'wan' && run.revision === 2,
    )!;
    for (const name of ['sample.mp4', 'still.png']) {
      await unlink(path.join(noSample.directory, 'samples', name));
    }
    await backend.start();
    let mutations = 0;
    page.on('request', (request) => {
      if (request.method() !== 'GET') mutations += 1;
    });
    for (const family of ['wan', 'h3']) {
      const before = runs.find(
        (run) => run.family === family && run.revision === 1,
      )!;
      const after = runs.find(
        (run) => run.family === family && run.revision === 2,
      )!;
      await page.goto(`${backend.url}/#/runs/${before.id}`);
      await page
        .getByLabel('Compare with run', { exact: true })
        .selectOption(after.id);
      await page
        .getByRole('button', { name: 'Compare inputs', exact: true })
        .click();
      const comparison = page.getByLabel('Run comparison', { exact: true });
      const current = comparison.getByRole('region', {
        name: 'Current Run samples',
        exact: true,
      });
      const compared = comparison.getByRole('region', {
        name: 'Compared Run samples',
        exact: true,
      });
      for (const [side, record] of [
        [current, before],
        [compared, after],
      ] as const) {
        await expect(side).toContainText(
          `${family}-revision-${record.revision}`,
        );
        await expect(side).toContainText(`revision ${record.revision}`);
        await expect(side).toContainText(record.id);
        await expect(side).toContainText('Recorded sample prompt');
        await expect(side).toContainText('Recorded seed: 42');
        if (record.id === noSample.id) {
          await expect(side).toContainText(
            'No generated samples recorded for this Run.',
          );
          await expect(side.getByLabel('Sample', { exact: true })).toHaveCount(
            0,
          );
        } else {
          const video = side.getByLabel('Output video', { exact: true });
          await video.evaluate(async (element) => {
            const media = element as HTMLVideoElement;
            media.muted = true;
            await media.play();
          });
          await expect
            .poll(() =>
              video.evaluate(
                (element) => (element as HTMLVideoElement).currentTime,
              ),
            )
            .toBeGreaterThan(0);
        }
        const download = page.waitForEvent('download');
        await side
          .getByRole('link', { name: 'Download final LoRA', exact: true })
          .click();
        expect((await download).suggestedFilename()).toBe(
          `${family}-revision-${record.revision}-r${record.revision}.safetensors`,
        );
      }
      await expect(comparison).toContainText('job.config.rank');
      if (family === 'h3') {
        await current
          .getByLabel('Sample', { exact: true })
          .selectOption({ label: 'tone.wav · available' });
        const audio = current.getByLabel('Output audio', { exact: true });
        await audio.evaluate(async (element) => {
          const media = element as HTMLAudioElement;
          media.muted = true;
          await media.play();
        });
        await expect
          .poll(() =>
            audio.evaluate(
              (element) => (element as HTMLAudioElement).currentTime,
            ),
          )
          .toBeGreaterThan(0);
      }
      await page.setViewportSize({ width: 390, height: 844 });
      expect(
        await comparison.evaluate(
          (element) => element.scrollWidth <= element.clientWidth,
        ),
      ).toBe(true);
      await page.setViewportSize({ width: 1366, height: 768 });
      if (after.id === noSample.id) continue;
      for (const name of [
        'sample.mp4',
        'still.png',
        ...(family === 'h3' ? ['tone.wav'] : []),
      ])
        await unlink(path.join(after.directory, 'samples', name));
      await page
        .getByRole('button', { name: 'Compare inputs', exact: true })
        .click();
      await compared
        .getByLabel('Sample', { exact: true })
        .selectOption({ label: 'sample.mp4 · missing' });
      await expect(compared).toContainText('missing');
      await expect(
        compared.getByLabel('Output video', { exact: true }),
      ).toHaveCount(0);
      await expect(current.getByLabel('Sample', { exact: true })).toBeEnabled();
    }
    expect(mutations).toBe(0);
  } finally {
    await backend.stop();
    await rm(parent, {
      recursive: true,
      force: true,
      maxRetries: 5,
      retryDelay: 150,
    });
  }
});
