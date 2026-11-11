import { test, expect } from '@playwright/test';
import { runWorkspace } from './runHarness';

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
    await page.getByText('Advanced controls', { exact: true }).click();
    await expect(
      page
        .getByRole('combobox', { name: 'Compatible preset', exact: true })
        .getByRole('option', {
          name: 'Smoke test · Wan · 2 steps',
          exact: true,
        }),
    ).toHaveCount(0);
    await page
      .getByRole('combobox', { name: 'Compatible preset', exact: true })
      .selectOption('smoke-wan-paired');
    await page
      .getByRole('button', { name: 'Apply preset', exact: true })
      .click();
    await expect(page.getByRole('combobox', { name: /^Recipe/ })).toHaveValue(
      'alternating',
    );
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveValue('4');
    await expect(
      page.getByLabel('Steps per expert before switching', { exact: true }),
    ).toHaveValue('1');
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
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await expect(recipe).toHaveValue('alternating');
    await expect(recipe.getByRole('option')).toHaveCount(1);
    await expect(
      page.getByLabel('Steps per expert before switching', { exact: true }),
    ).toHaveValue('30');
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
    await page
      .getByRole('combobox', { name: /^Recipe/ })
      .selectOption('alternating');
    await page
      .getByLabel('Steps per expert before switching', { exact: true })
      .fill('2');
    await page.getByLabel('Training steps', { exact: true }).fill('8');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await page.reload();
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
    await expect(schedule).toContainText('Low-noise: 4 steps');
    await expect(schedule).toContainText('High-noise: 4 steps');
    await expect(schedule).toContainText('fresh dataset pass');
    await page
      .getByRole('button', { name: 'Prepare run', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: 'Start training', exact: true }),
    ).toBeEnabled({ timeout: 240_000 });
    const pendingPair = page.getByLabel('Paired LoRA outputs');
    await expect(
      pendingPair.getByText('Checkpoint pending', { exact: true }),
    ).toHaveCount(2);
    await expect(
      page.getByText(/The paired LoRA outputs are incomplete/),
    ).toHaveCount(0);
    await page
      .getByRole('button', { name: 'Start training', exact: true })
      .click();
    await expect(page.getByLabel('Paired LoRA outputs')).toContainText(
      '4 optimizer steps',
      { timeout: 240_000 },
    );
    await expect(
      page.getByRole('link', { name: 'Download low-noise LoRA', exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('link', { name: 'Download high-noise LoRA', exact: true }),
    ).toBeVisible();
    await expect(page.getByLabel('Training progress')).toContainText(
      'Step 8/8',
    );
    await expect(
      page.getByText('State: Completed', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    for (const [expert, phase] of [
      ['low', 4],
      ['high', 6],
    ] as const) {
      const downloaded = page.waitForEvent('download');
      await page
        .getByRole('link', {
          name: `Download ${expert}-noise LoRA`,
          exact: true,
        })
        .click();
      expect((await downloaded).suggestedFilename()).toMatch(
        new RegExp(`-${expert}-phase${phase}\\.safetensors$`),
      );
    }
    const [run] = await app.records();
    const full = (await (
      await fetch(`${app.url}/api/runs/${run.id}`)
    ).json()) as {
      sample_pair: Record<string, { steps: number; path: string }>;
      stages: { name: string; args: string[]; training?: { expert: string } }[];
    };
    expect(full.sample_pair.low.steps).toBe(4);
    expect(full.sample_pair.high.steps).toBe(4);
    expect(
      full.stages
        .filter((stage) => stage.training)
        .map((stage) => stage.training!.expert),
    ).toEqual(['low', 'high', 'low', 'high']);
    await app.crash();
    await app.start();
    await page.reload();
    await expect(
      page.getByRole('link', { name: 'Download high-noise LoRA', exact: true }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Inspect saved states', exact: true })
      .click();
    const partial = page
      .locator('p')
      .filter({ hasText: /^Paired schedule step 3 ·/ });
    await expect(partial).toContainText(
      'low-noise 2 steps · high-noise 1 steps',
      { timeout: 240_000 },
    );
    await page
      .getByLabel('Additional segment steps', { exact: true })
      .fill('5');
    await partial
      .getByRole('button', { name: 'Restore as new segment', exact: true })
      .click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText(
      'Both expert states and the alternating schedule continue',
    );
    await dialog
      .getByRole('button', { name: 'Restore as new segment', exact: true })
      .click();
    await expect(page.getByLabel('Training progress')).toContainText(
      'Step 5/5',
      { timeout: 240_000 },
    );
    await expect(page.getByLabel('Paired LoRA outputs')).toContainText(
      '4 optimizer steps',
    );
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
