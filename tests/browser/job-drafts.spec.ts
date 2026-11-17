import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { helper, python } from './backend';
import { jobWorkspace, openJobs } from './jobHarness';

test('checkpoint retention persists and review explains protected saves', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();

    const retention = page.getByLabel('Maximum checkpoint saves to keep', {
      exact: true,
    });
    await page.getByRole('tab', { name: 'Checkpoints', exact: true }).click();
    await expect(retention).toHaveValue('0');
    await retention.fill('2');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page.reload();

    await page.getByRole('tab', { name: 'Checkpoints', exact: true }).click();
    await expect(retention).toHaveValue('2');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await page.getByText('Estimated disk use', { exact: true }).click();
    await expect(page.getByTestId('disk-estimate')).toContainText(
      'Keep 2 historical saves per expert',
    );
    await expect(page.getByTestId('disk-estimate')).toContainText('protected');
  } finally {
    await app.close();
  }
});

test('Wan draft controls, validation and reviewed native configuration survive service restart', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await page.getByLabel('Job name', { exact: true }).fill('Browser Wan');

    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await page.getByLabel('LoRA rank', { exact: true }).fill('16');
    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await page.getByRole('spinbutton', { name: /^Frames\b/ }).fill('18');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await expect(
      page.getByText('Frames: Wan requires 4*n+1 frames.', { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Prepare run', exact: true }),
    ).toBeDisabled();
    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await page.getByRole('spinbutton', { name: /^Frames\b/ }).fill('33');
    await page
      .getByLabel('Default training width', { exact: true })
      .fill('320');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await expect(page.getByTestId('dataset-toml')).toContainText(
      'resolution = [320, 480]',
    );
    await expect(page.getByTestId('dataset-toml')).toContainText(
      'target_frames = [33]',
    );
    await page.getByText('Advanced', { exact: true }).click();
    await page.getByText('train arguments', { exact: true }).first().click();
    await expect(
      page
        .locator('details details')
        .filter({ has: page.getByText('train arguments', { exact: true }) })
        .locator('pre')
        .first(),
    ).toContainText('"--network_dim",\n  "16"');
    const jobs = (await (
      await page.request.get(`${app.url}/api/jobs`)
    ).json()) as { id: string; revision: number }[];
    const stored = JSON.parse(
      await readFile(
        path.join(
          app.root,
          'state',
          'jobs',
          jobs[0].id,
          `${jobs[0].revision}.json`,
        ),
        'utf8',
      ),
    ) as { config: { frames: number } };
    expect(stored.config.frames).toBe(33);
    await app.stop();
    await app.start();
    await openJobs(page, app.url);
    await page.getByRole('button', { name: /Browser Wan · WAN/ }).click();

    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await expect(
      page.getByRole('spinbutton', { name: /^Frames\b/ }),
    ).toHaveValue('33');
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await expect(page.getByLabel('LoRA rank', { exact: true })).toHaveValue(
      '16',
    );
  } finally {
    await app.close();
  }
});

test('a new draft starts realistic, warns about low steps and offers the smoke-test preset', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();

    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await expect(
      page.getByRole('spinbutton', { name: /^Default training width\b/ }),
    ).toHaveValue('832');
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveValue('2000');
    await page.getByRole('tab', { name: 'Samples', exact: true }).click();
    await expect(
      page.getByRole('textbox', { name: 'Sample prompt' }),
    ).toHaveValue('');
    await page.getByRole('tab', { name: 'Training', exact: true }).click();
    await page.getByLabel('Training steps', { exact: true }).fill('2');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await expect(page.getByText(/^Training steps: 2 steps/)).toBeVisible();
    await expect(page.getByText(/^Sampling: enter a prompt/)).toBeVisible();
    await page.getByRole('tab', { name: 'Presets', exact: true }).click();
    await page
      .getByRole('combobox', { name: 'Compatible preset', exact: true })
      .selectOption({ label: 'Smoke test · Wan alternating · 4 steps' });
    await page
      .getByRole('button', { name: 'Apply preset', exact: true })
      .click();
    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await expect(
      page.getByRole('spinbutton', { name: /^Default training width\b/ }),
    ).toHaveValue('256');
    await page.getByRole('tab', { name: 'Samples', exact: true }).click();
    await expect(
      page.getByRole('textbox', { name: 'Sample prompt' }),
    ).toHaveValue(/red square/);
  } finally {
    await app.close();
  }
});

test('Wan frame extraction controls persist and review counts cached windows after resampling', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    const clips = path.join(app.root, 'clips');
    const creator = spawn(
      python,
      [helper, 'video', path.join(clips, 'thirty.mp4'), '90', '30'],
      { stdio: 'pipe' },
    );
    expect(
      await new Promise<number | null>((resolve) =>
        creator.once('exit', resolve),
      ),
    ).toBe(0);
    const settings = (await (
      await page.request.get(`${app.url}/api/settings`)
    ).json()) as { dataset_roots: string[] };
    expect(
      (
        await page.request.put(`${app.url}/api/settings`, {
          data: { ...settings, dataset_roots: [clips] },
        })
      ).status(),
    ).toBe(200);
    const dataset = (await (
      await page.request.post(`${app.url}/api/datasets`, {
        data: { name: 'Thirty fps', path: clips },
      })
    ).json()) as { id: string };
    await expect
      .poll(
        async () =>
          (
            (await (
              await page.request.get(
                `${app.url}/api/datasets/${dataset.id}/items`,
              )
            ).json()) as { valid: boolean }[]
          ).filter((item) => item.valid).length,
        { timeout: 240_000 },
      )
      .toBe(1);
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await page
      .getByRole('combobox', { name: 'Add a dataset' })
      .selectOption(dataset.id);
    await page
      .getByRole('button', { name: 'Add dataset', exact: true })
      .click();

    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await page.getByRole('spinbutton', { name: /^Frames\b/ }).fill('17');
    await page
      .getByRole('combobox', { name: /^Frame extraction\b/ })
      .selectOption('slide');
    await page.getByLabel('Slide stride', { exact: true }).fill('8');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const toml = page.getByTestId('dataset-toml');
    await expect(toml).toContainText(
      'frame_extraction = "slide"\nframe_stride = 8\nsource_fps = 30.0',
    );
    // 90 frames at 30 fps keep 48 at 16 fps: windows start at 0, 8, 16 and 24.
    const windows = page.getByTestId('video-windows');
    await expect(windows).toContainText('4 cached video windows');
    await expect(windows).toContainText('thirty.mp4: 30 fps, 90 → 48 frames');
    await expect(
      page.getByText(/thirty\.mp4: 30 fps is resampled to 16 fps/),
    ).toBeVisible();
    await page.reload();
    await openJobs(page, app.url);
    await page.getByRole('button', { name: /New Wan job · WAN/ }).click();

    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await expect(
      page.getByRole('combobox', { name: /^Frame extraction\b/ }),
    ).toHaveValue('slide');
    await expect(page.getByLabel('Slide stride', { exact: true })).toHaveValue(
      '8',
    );
  } finally {
    await app.close();
  }
});

test('H3 editor keeps T2VA guidance stages and identifies invalid geometry', async ({
  page,
}) => {
  const app = await jobWorkspace();
  try {
    await openJobs(page, app.url);
    await page.getByRole('button', { name: 'New H3 job', exact: true }).click();

    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await expect(
      page.getByRole('spinbutton', { name: /^Frames\b/ }),
    ).toHaveValue('124');
    await page.getByRole('tab', { name: 'Memory', exact: true }).click();
    await expect(
      page.getByLabel('H2D-only block swapping', { exact: false }),
    ).toBeChecked();
    await expect(
      page.getByLabel('Runtime FP8 scaling', { exact: false }),
    ).toHaveCount(0);
    await expect(page.getByLabel(/negative prompt/i)).toHaveCount(0);
    await page
      .getByRole('tab', { name: 'Dataset preparation', exact: true })
      .click();
    await page
      .getByLabel('Default training width', { exact: true })
      .fill('272');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await expect(
      page.getByText(
        'Geometry: H3 width and height must be at least 64 and multiples of 32.',
        { exact: true },
      ),
    ).toBeVisible();
    await page.getByText('Advanced', { exact: true }).click();
    await page.getByText('text_cache arguments', { exact: true }).click();
    const args = page
      .locator('details details')
      .filter({ has: page.getByText('text_cache arguments', { exact: true }) })
      .locator('pre');
    await expect(args).toContainText('"--task",\n  "t2va"');
    await expect(args).toContainText('--uncond_output');
  } finally {
    await app.close();
  }
});
