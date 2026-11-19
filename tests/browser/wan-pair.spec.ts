import { stat } from 'node:fs/promises';
import path from 'node:path';
import { test, expect } from '@playwright/test';
import {
  openRunDiagnostics,
  openRunTab,
  runResume,
  runWorkspace,
} from './runHarness';

test('Wan offers only alternating training for new jobs and presets', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    const recipe = page.getByRole('combobox', { name: /^Recipe/ });
    await expect(
      recipe.getByRole('option', { name: /low-noise expert/ }),
    ).toHaveCount(0);
    await expect(
      recipe.getByRole('option', {
        name: 'Choose alternating high/low training',
      }),
    ).toHaveAttribute('disabled', '');

    await expect(
      page
        .getByRole('combobox', { name: 'Compatible preset', exact: true })
        .getByRole('option', {
          name: 'Smoke test · Wan · 2 steps',
          exact: true,
        }),
    ).toHaveCount(0);
    await page.getByRole('tab', { name: 'Presets', exact: true }).click();
    await page
      .getByRole('combobox', { name: 'Compatible preset', exact: true })
      .selectOption('smoke-wan-paired');
    await page
      .getByRole('button', { name: 'Apply preset', exact: true })
      .click();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(page.getByRole('combobox', { name: /^Recipe/ })).toHaveValue(
      'alternating',
    );
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveValue('4');
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(
      page.getByLabel('Steps per expert before switching', { exact: true }),
    ).toHaveValue('1');
    await page.getByRole('tab', { name: 'Advanced', exact: true }).click();
    await expect(
      page.getByRole('checkbox', { name: /^Save optimizer\/training state/ }),
    ).toBeChecked();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await expect(page.getByLabel('Configuration review')).toContainText(
      'Review passed.',
    );
    await expect(
      recipe.getByRole('option', {
        name: 'Choose alternating high/low training',
      }),
    ).toHaveCount(0);
    await page
      .getByRole('button', { name: 'Back to jobs', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(recipe).toHaveValue('alternating');
    await expect(recipe.getByRole('option')).toHaveCount(1);
    await expect(
      page.getByLabel('Steps per expert before switching', { exact: true }),
    ).toHaveValue('100');
    await page.getByRole('tab', { name: 'Advanced', exact: true }).click();
    await expect(
      page.getByRole('checkbox', { name: /^Save optimizer\/training state/ }),
    ).toBeChecked();
  } finally {
    await app.close();
  }
});

test('alternating Wan settings persist, train both experts and restore the paired schedule through the GUI', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await page
      .getByRole('combobox', { name: /^Recipe/ })
      .selectOption('alternating');
    await page
      .getByLabel('Steps per expert before switching', { exact: true })
      .fill('2');
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await page.getByLabel('Training steps', { exact: true }).fill('8');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page.reload();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(page.getByRole('combobox', { name: /^Recipe/ })).toHaveValue(
      'alternating',
    );
    await expect(
      page.getByLabel('Steps per expert before switching', { exact: true }),
    ).toHaveValue('2');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const schedule = page.getByRole('region', {
      name: 'Expert training schedule',
    });
    await expect(schedule).toContainText('Low-noise: 8 steps');
    await expect(schedule).toContainText('High-noise: 8 steps');
    await expect(schedule).toContainText('fresh dataset pass');
    await page
      .getByRole('button', { name: 'Prepare only', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: 'Start training', exact: true }),
    ).toBeEnabled({ timeout: 240_000 });
    await expect(
      page.getByText(/The paired LoRA outputs are incomplete/),
    ).toHaveCount(0);
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(page.getByLabel('Training progress')).toContainText(
      'Step 8/8',
      { timeout: 240_000 },
    );
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const [run] = await app.records();
    const full = (await (
      await fetch(`${app.url}/api/runs/${run.id}`)
    ).json()) as {
      sample_pair: Record<string, { steps: number; path: string }>;
      stages: { name: string; args: string[]; training?: { expert: string } }[];
    };
    await openRunDiagnostics(page);
    await expect(page.getByLabel('Output folder', { exact: true })).toHaveText(
      run.directory,
    );
    for (const expert of ['low', 'high']) {
      const file = full.sample_pair[expert].path;
      expect(path.relative(run.directory, file)).toMatch(
        new RegExp('^.*-' + expert + '-step0*8\\.safetensors$'),
      );
      expect((await stat(file)).size).toBeGreaterThan(0);
    }
    expect(full.sample_pair.low.steps).toBe(8);
    expect(full.sample_pair.high.steps).toBe(8);
    expect(
      full.stages
        .filter((stage) => stage.training)
        .map((stage) => stage.training!.expert),
    ).toEqual(
      Array.from({ length: 16 }, (_, index) => (index % 2 ? 'high' : 'low')),
    );
    await app.crash();
    await app.start();
    await page.reload();
    await openRunDiagnostics(page);
    await expect(page.getByLabel('Output folder', { exact: true })).toHaveText(
      run.directory,
    );
    const resume = runResume(page);
    await resume.getByRole('button', { name: 'Resume', exact: true }).click();
    await resume.getByText('Choose an earlier save', { exact: true }).click();
    const savedState = resume.getByLabel('Saved state');
    const partial = savedState.getByRole('option', {
      name: /Paired recovery: low 2 \/ high 1$/,
    });
    await savedState.selectOption((await partial.getAttribute('value'))!);
    await expect(resume.getByText(/^Selected save/)).toContainText(
      'low-noise 2 steps · high-noise 1 steps',
    );
    // Both experts train until step 7: low continues from 2 and high catches up from 1.
    await resume.getByLabel('Train until step', { exact: true }).fill('7');
    await resume
      .getByRole('button', { name: 'Review resume', exact: true })
      .click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText(
      'Both experts continue from the selected save.',
    );
    await dialog
      .getByRole('button', { name: 'Restore as new segment', exact: true })
      .click();
    await expect(page.getByLabel('Training progress')).toContainText(
      'Step 7/7',
      { timeout: 240_000 },
    );
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const segment = (await app.records()).find((item) => item.id !== run.id)!;
    await openRunDiagnostics(page);
    await expect(page.getByLabel('Output folder', { exact: true })).toHaveText(
      segment.directory,
    );
    await openRunTab(page, 'samples');
    // The source Run's final sample comes first in the training's merged list.
    await page
      .getByRole('button', { name: /sample.mp4.*available/ })
      .last()
      .click();
    await expect(page.getByLabel('Output video')).toBeVisible();
    const video = page.getByLabel('Output video');
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
  } finally {
    await app.close();
  }
});
