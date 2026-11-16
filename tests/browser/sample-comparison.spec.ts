import { test, expect, type Locator, type Page } from '@playwright/test';
import { mkdtemp, readFile, rm, unlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { localBackend, python } from './backend';
import { stopBackendTree } from './processes';
import {
  execute,
  openRunTab,
  runPanel,
  runPanelLabelled,
  runTabPanel,
} from './runHarness';

// The Run picker sizes to its content, capped so long Job names cannot stretch it to the
// section width, with Compare inputs beside it at desktop width. On a phone they stack
// without horizontal scrolling.
async function expectCompareControls(page: Page, panel: Locator) {
  const select = panel.getByLabel('Compare with run', { exact: true });
  const button = panel.getByRole('button', {
    name: 'Compare inputs',
    exact: true,
  });
  await expect(button).toBeVisible();
  // Measures the section, picker and button in one layout pass. Content above the panel
  // can still be loading, and shifts it between separate boundingBox calls.
  const boxes = () =>
    panel.evaluate((root) => {
      const rect = (element: Element) => {
        const { x, y, width, height } = element.getBoundingClientRect();
        return { x, y, width, height };
      };
      return {
        section: rect(root),
        picker: rect(root.querySelector('.run-compare-picker select')!),
        action: rect(root.querySelector('.run-compare-picker button')!),
      };
    });
  await page.setViewportSize({ width: 1366, height: 768 });
  let { section, picker, action } = await boxes();
  expect(picker.width).toBeLessThan(section.width / 2);
  expect(action.x).toBeGreaterThanOrEqual(picker.x + picker.width);
  expect(action.y).toBeLessThan(picker.y + picker.height);
  expect(picker.y).toBeLessThan(action.y + action.height);
  // A long Job name truncates in the closed picker instead of widening it.
  await select.evaluate((element) => {
    const option = document.createElement('option');
    option.className = 'long-run-name';
    option.textContent = 'a-very-long-job-name-'.repeat(10);
    element.append(option);
  });
  ({ section, picker } = await boxes());
  expect(picker.width).toBeLessThan(section.width / 2);
  await select.evaluate((element) =>
    element.querySelector('.long-run-name')!.remove(),
  );
  await page.setViewportSize({ width: 375, height: 812 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  ({ picker, action } = await boxes());
  expect(picker.x).toBeGreaterThanOrEqual(0);
  expect(picker.x + picker.width).toBeLessThanOrEqual(375);
  expect(picker.width).toBeGreaterThan(150);
  expect(action.y).toBeGreaterThanOrEqual(picker.y + picker.height);
  await page.setViewportSize({ width: 1366, height: 768 });
}

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
    const generation = execute(python, [
      '-m',
      'musubi_gui.output_fixture',
      fixture,
    ]);
    let deadline: NodeJS.Timeout | undefined;
    let termination: Promise<void> | undefined;
    try {
      await Promise.race([
        generation,
        new Promise<never>((_, reject) => {
          deadline = setTimeout(() => {
            const pid = generation.child.pid;
            termination = pid ? stopBackendTree(pid) : Promise.resolve();
            void termination.then(
              () =>
                reject(
                  new Error('Output fixture generation exceeded five minutes.'),
                ),
              reject,
            );
          }, 300_000);
        }),
      ]);
    } finally {
      clearTimeout(deadline);
      await termination;
    }
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
    // No samples have ever been indexed for this Run.
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
      // Compare lives on Overview, never on the samples-only Samples tab.
      await openRunTab(page, 'samples');
      await expect(runPanelLabelled(page, 'compare')).toBeHidden();
      await openRunTab(page, 'overview');
      const comparePanel = runPanel(runTabPanel(page, 'overview'), 'compare');
      await comparePanel
        .getByLabel('Compare with run', { exact: true })
        .selectOption(after.id);
      await expectCompareControls(page, comparePanel);
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
