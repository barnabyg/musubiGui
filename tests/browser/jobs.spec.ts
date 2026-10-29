import { test, expect, type Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createServer } from 'node:net';
import { stopBackendTree } from './processes';

async function workspace() {
  const root = await mkdtemp(path.join(tmpdir(), 'musubi-jobs-'));
  const reservation = createServer();
  await new Promise<void>((resolve) =>
    reservation.listen(0, '127.0.0.1', resolve),
  );
  const address = reservation.address();
  if (typeof address !== 'object' || !address) throw new Error('No test port');
  const port = address.port;
  await new Promise<void>((resolve) => reservation.close(() => resolve()));
  const url = `http://127.0.0.1:${port}`;
  let backend: ChildProcess | undefined;
  const start = async () => {
    backend = spawn(
      path.resolve('.venv/Scripts/python.exe'),
      ['-m', 'musubi_gui', '--port', String(port), '--no-browser'],
      {
        env: { ...process.env, MUSUBI_GUI_STATE: path.join(root, 'state') },
        stdio: 'pipe',
      },
    );
    await expect
      .poll(async () => {
        try {
          return (await fetch(`${url}/api/jobs`)).status;
        } catch {
          return 0;
        }
      })
      .toBe(200);
  };
  const stop = async () => {
    if (backend && backend.exitCode === null) {
      const exited = new Promise<void>((resolve) =>
        backend!.once('exit', () => resolve()),
      );
      await stopBackendTree(backend.pid!);
      await exited;
    }
  };
  await mkdir(path.join(root, 'models'));
  await start();
  const settings = (await (
    await fetch(`${url}/api/settings`)
  ).json()) as Record<string, unknown>;
  const configured = await fetch(`${url}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...settings,
      output_root: path.join(root, 'output'),
      cache_root: path.join(root, 'cache'),
      model_roots: [path.join(root, 'models')],
      models: Object.fromEntries(
        [
          'wan_low',
          'wan_high',
          'wan_vae',
          'wan_t5',
          'h3_dit',
          'h3_video_vae',
          'h3_audio_vae',
          'h3_text',
        ].map((key) => [key, path.join(root, 'models', `${key}.safetensors`)]),
      ),
    }),
  });
  expect(configured.status).toBe(200);
  return {
    root,
    url,
    start,
    stop,
    close: async () => {
      await stop();
      await rm(root, {
        recursive: true,
        force: true,
        maxRetries: 5,
        retryDelay: 150,
      });
    },
  };
}

async function openJobs(page: Page, url: string) {
  await page.goto(url);
  await page.getByRole('button', { name: 'Jobs', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Jobs', exact: true }),
  ).toBeVisible();
}

test('Wan draft controls, validation and reviewed native configuration survive service restart', async ({
  page,
}) => {
  const app = await workspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await page.getByLabel('Job name', { exact: true }).fill('Browser Wan');
    await page.getByLabel('LoRA rank', { exact: true }).fill('16');
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
    await page.getByRole('spinbutton', { name: /^Frames\b/ }).fill('33');
    await page.getByLabel('Width', { exact: false }).fill('320');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await expect(page.getByTestId('dataset-toml')).toContainText(
      'resolution = [320, 256]',
    );
    await expect(page.getByTestId('dataset-toml')).toContainText(
      'target_frames = [33]',
    );
    await page.getByText('train arguments', { exact: true }).click();
    await expect(
      page
        .locator('details')
        .filter({ has: page.getByText('train arguments', { exact: true }) })
        .locator('pre'),
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
    await expect(
      page.getByRole('spinbutton', { name: /^Frames\b/ }),
    ).toHaveValue('33');
    await expect(page.getByLabel('LoRA rank', { exact: true })).toHaveValue(
      '16',
    );
  } finally {
    await app.close();
  }
});

test('Wan frame extraction controls persist and review counts cached windows after resampling', async ({
  page,
}) => {
  const app = await workspace();
  try {
    const clips = path.join(app.root, 'clips');
    const creator = spawn(
      path.resolve('.venv/Scripts/python.exe'),
      [
        '-c',
        'import sys; from fractions import Fraction; from pathlib import Path; from musubi_gui.acceptance import synthetic_video; synthetic_video(Path(sys.argv[1]) / "thirty.mp4", 90, rate=Fraction(30))',
        clips,
      ],
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
        { timeout: 45_000 },
      )
      .toBe(1);
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await page
      .getByRole('combobox', { name: /^Dataset\b/ })
      .selectOption(dataset.id);
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
  const app = await workspace();
  try {
    await openJobs(page, app.url);
    await page.getByRole('button', { name: 'New H3 job', exact: true }).click();
    await expect(
      page.getByRole('spinbutton', { name: /^Frames\b/ }),
    ).toHaveValue('124');
    await expect(
      page.getByLabel('H2D-only block swapping', { exact: false }),
    ).toBeChecked();
    await expect(
      page.getByLabel('Runtime FP8 scaling', { exact: false }),
    ).toHaveCount(0);
    await expect(page.getByLabel(/negative prompt/i)).toHaveCount(0);
    await page.getByLabel('Width', { exact: false }).fill('272');
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
    await page.getByText('text_cache arguments', { exact: true }).click();
    const args = page
      .locator('details')
      .filter({ has: page.getByText('text_cache arguments', { exact: true }) })
      .locator('pre');
    await expect(args).toContainText('"--task",\n  "t2va"');
    await expect(args).toContainText('--uncond_output');
  } finally {
    await app.close();
  }
});

test('duplication presets cancelled conversion and versioned export import keep independent drafts', async ({
  page,
}) => {
  const app = await workspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await page.getByLabel('Job name', { exact: true }).fill('Reusable');
    await page.getByLabel('LoRA rank', { exact: true }).fill('16');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page.getByLabel('Preset name', { exact: true }).fill('Sixteen');
    await page
      .getByRole('button', { name: 'Save as preset', exact: true })
      .click();
    await expect(page.getByRole('status')).toHaveText('Preset saved');
    await page
      .getByRole('button', { name: 'Duplicate job', exact: true })
      .click();
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      'Reusable copy',
    );
    await page.getByLabel('LoRA rank', { exact: true }).fill('32');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page
      .getByRole('combobox', { name: 'Compatible preset', exact: true })
      .selectOption({ label: 'Sixteen' });
    await page
      .getByRole('button', { name: 'Apply preset', exact: true })
      .click();
    await expect(page.getByLabel('LoRA rank', { exact: true })).toHaveValue(
      '16',
    );
    await page.getByLabel('Training steps', { exact: true }).fill('800');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page
      .getByRole('combobox', { name: 'Compatible preset', exact: true })
      .selectOption({
        label: 'Measured · Wan 832×480 · 33 frames · rank 16',
      });
    await page
      .getByRole('button', { name: 'Apply preset', exact: true })
      .click();
    await expect(page.getByRole('status')).toHaveText(
      'Preset applied as a new revision',
    );
    await expect(
      page.getByRole('spinbutton', { name: /^Width\b/ }),
    ).toHaveValue('832');
    await expect(
      page.getByRole('spinbutton', { name: /^Frames\b/ }),
    ).toHaveValue('33');
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveValue('800');
    await page
      .getByRole('button', { name: 'Change model to H3 T2VA', exact: true })
      .click();
    await expect(
      page.getByRole('dialog', { name: 'Confirm model conversion' }),
    ).toContainText('frames: 33 → 124');
    await page
      .getByRole('button', { name: 'Cancel conversion', exact: true })
      .click();
    await expect(page.getByRole('combobox', { name: /^Recipe/ })).toHaveValue(
      'low-noise',
    );
    await page
      .getByText('Import / export configuration', { exact: true })
      .click();
    const download = page.waitForEvent('download');
    await page
      .getByRole('button', { name: 'Export configuration', exact: true })
      .click();
    expect((await download).suggestedFilename()).toMatch(/^musubi-job-/);
    const exported = JSON.parse(
      await page
        .getByRole('textbox', { name: 'Configuration JSON', exact: true })
        .inputValue(),
    ) as { version: number; draft: { name: string } };
    expect(exported.version).toBe(1);
    exported.draft.name = 'Imported draft';
    await page
      .getByRole('textbox', { name: 'Configuration JSON', exact: true })
      .fill(JSON.stringify(exported));
    await page
      .getByRole('button', { name: 'Import configuration', exact: true })
      .click();
    await expect(page.getByLabel('Job name', { exact: true })).toHaveValue(
      'Imported draft',
    );
    await page.getByRole('button', { name: /Reusable · WAN/ }).click();
    await expect(page.getByLabel('LoRA rank', { exact: true })).toHaveValue(
      '16',
    );
    await page
      .getByRole('button', { name: 'Change model to H3 T2VA', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Accept conversion', exact: true })
      .click();
    await expect(
      page.getByRole('spinbutton', { name: /^Frames\b/ }),
    ).toHaveValue('124');
  } finally {
    await app.close();
  }
});
