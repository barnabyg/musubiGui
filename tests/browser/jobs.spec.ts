import { test, expect, type Page } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { localBackend } from './backend';

async function workspace() {
  const root = await mkdtemp(path.join(tmpdir(), 'musubi-jobs-'));
  const { url, start, stop } = await localBackend({
    state: path.join(root, 'state'),
    readyRoute: '/api/jobs',
  });
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

async function jobAction(page: Page, name: string) {
  await page.getByRole('button', { name: 'More job actions' }).click();
  await page.getByRole('menuitem', { name, exact: true }).click();
}

test('the action bar stays visible, identity comes first and rare actions are in a keyboard menu', async ({
  page,
}) => {
  const app = await workspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    const save = page.getByRole('button', { name: 'Save draft', exact: true });
    const inView = async () => {
      const box = await save.boundingBox();
      return (
        box !== null &&
        box.y >= 0 &&
        box.y + box.height <= page.viewportSize()!.height
      );
    };
    await expect.poll(inView).toBe(true);
    await page.evaluate(() =>
      window.scrollTo(0, document.documentElement.scrollHeight),
    );
    await expect.poll(inView).toBe(true);

    // Tabbing to a field below the bar scrolls it clear of the bar.
    const seed = page.getByLabel('Seed', { exact: true });
    await seed.focus();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.getByLabel('Seed', { exact: true }).focus();
    const bar = await page.locator('.job-action-bar').boundingBox();
    const field = await seed.boundingBox();
    expect(field!.y).toBeGreaterThanOrEqual(bar!.y + bar!.height);

    // Job name and dataset lead the editor; presets follow them.
    const headings = await page
      .getByRole('heading', { level: 2 })
      .allInnerTexts();
    expect(headings.slice(0, 2)).toEqual(['Job & dataset', 'Presets']);

    const menu = page.getByRole('button', { name: 'More job actions' });
    await menu.focus();
    await page.keyboard.press('Enter');
    const items = page.getByRole('menuitem');
    await expect(items).toHaveText([
      'Duplicate job',
      'Change model to H3 T2VA',
      'Archive job',
      'Delete job',
      'Import / export configuration',
    ]);
    await expect(items.first()).toBeFocused();
    await page.keyboard.press('ArrowUp');
    await expect(items.last()).toBeFocused();
    await page.keyboard.press('ArrowDown');
    await expect(items.first()).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(items).toHaveCount(0);
    await expect(menu).toBeFocused();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('End');
    await page.keyboard.press('Enter');
    await expect(
      page.getByRole('heading', { name: 'Import / export configuration' }),
    ).toBeVisible();
    await expect.poll(inView).toBe(true);
  } finally {
    await app.close();
  }
});

test('the job table sorts, marks the selected job and keeps new draft names apart', async ({
  page,
}) => {
  const app = await workspace();
  try {
    await openJobs(page, app.url);
    const create = page.getByRole('button', {
      name: 'New Wan job',
      exact: true,
    });
    await create.click();
    const name = page.getByLabel('Job name', { exact: true });
    await expect(name).toHaveValue('New Wan job');
    await expect(name).toBeFocused();
    await create.click();
    await expect(name).toHaveValue('New Wan job 2');
    await page.getByRole('button', { name: 'New H3 job', exact: true }).click();
    await expect(name).toHaveValue('New H3 job');

    const table = page.getByRole('table', { name: 'Saved jobs' });
    const rows = table.locator('tbody tr');
    await expect(rows).toHaveCount(3);
    await expect(rows.first()).toContainText('New H3 job');
    await expect(rows.first()).toContainText('None selected');
    await expect(rows.first()).toContainText('r1');
    await expect(rows.first()).toContainText('No Runs');
    await expect(table.getByRole('columnheader')).toHaveText([
      'Name',
      'Model',
      'Dataset',
      'Revision',
      'Last Run',
      /^Last edited/,
    ]);
    await expect(
      table.getByRole('columnheader', { name: 'Last edited' }),
    ).toHaveAttribute('aria-sort', 'descending');
    await expect(rows.first()).toHaveClass('selected');
    await expect(
      rows.first().getByRole('button', { name: /^New H3 job/ }),
    ).toHaveAttribute('aria-pressed', 'true');

    await table.getByRole('button', { name: 'Name', exact: true }).click();
    await expect(rows.first()).toContainText('New H3 job');
    await expect(rows.last()).toContainText('New Wan job 2');
    await table.getByRole('button', { name: 'Name', exact: true }).click();
    await expect(rows.first()).toContainText('New Wan job 2');
  } finally {
    await app.close();
  }
});

test('review leads with the verdict, comes into view and collapses technical detail', async ({
  page,
}) => {
  const app = await workspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const review = page.getByRole('region', { name: 'Configuration review' });
    const verdict = review.locator('.review-verdict');
    await expect(verdict).toHaveClass(/failed/);
    await expect(verdict).toContainText(/^Review failed: resolve \d+ errors/);
    await expect(verdict).toContainText('0 included items');
    await expect(verdict).toContainText('Disk:');
    await expect(page.getByRole('status')).toContainText(
      /^Review failed for revision 1: \d+ errors/,
    );
    await expect(review).toBeInViewport();
    await expect(page.getByTestId('dataset-toml')).toBeHidden();
    await expect(page.getByText('Resolved paths')).toBeHidden();
    await expect(page.getByText('latent_cache arguments')).toBeHidden();
    await page.getByText('Advanced', { exact: true }).click();
    await expect(page.getByTestId('dataset-toml')).toBeVisible();
  } finally {
    await app.close();
  }
});

test('Review saves a dirty draft first, and disabled actions explain themselves', async ({
  page,
}) => {
  const app = await workspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    const prepare = page.getByRole('button', {
      name: 'Prepare run',
      exact: true,
    });
    const review = page.getByRole('button', {
      name: 'Review configuration',
      exact: true,
    });
    await expect(prepare).toBeDisabled();
    await expect(prepare).toHaveAccessibleDescription(
      'Choose Review configuration before preparing.',
    );
    await expect(
      page.getByRole('button', { name: 'Save as preset', exact: true }),
    ).toHaveAccessibleDescription('Enter a preset name to save one.');
    await expect(
      page.getByRole('button', { name: 'Apply preset', exact: true }),
    ).toHaveAccessibleDescription('Select a preset to apply it.');

    const steps = page.getByLabel('Training steps', { exact: true });
    await steps.fill('700');
    await expect(prepare).toHaveAccessibleDescription(/save these edits/);
    await expect(review).toBeEnabled();

    // A failed save stops the review and leaves the edits on screen.
    await page.route('**/api/jobs/*', async (route) => {
      if (route.request().method() === 'PUT')
        await route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'This job changed in another tab.' }),
        });
      else await route.continue();
    });
    await review.click();
    await expect(page.getByRole('alert')).toContainText('another tab');
    await expect(page.getByRole('status')).toHaveText(
      'Save failed — edits retained',
    );
    await expect(steps).toHaveValue('700');
    await expect(
      page.getByRole('region', { name: 'Configuration review' }),
    ).toHaveCount(0);
    await page.unroute('**/api/jobs/*');

    await review.click();
    await expect(page.getByRole('status')).toContainText(
      'Review failed for revision 2',
    );
    await expect(
      page.getByRole('button', { name: /^New Wan job · WAN · r2$/ }),
    ).toBeVisible();
    await expect(prepare).toHaveAccessibleDescription(
      'Resolve the review errors before preparing.',
    );
    const stored = (await (
      await page.request.get(`${app.url}/api/jobs`)
    ).json()) as { config: { steps: number } }[];
    expect(stored[0].config.steps).toBe(700);
  } finally {
    await app.close();
  }
});

test('confirmation dialogs start on the safe choice, trap focus and cancel with Escape', async ({
  page,
}) => {
  const app = await workspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    const menu = page.getByRole('button', { name: 'More job actions' });
    await jobAction(page, 'Delete job');
    const question = page.getByRole('dialog', {
      name: 'Permanently delete job New Wan job?',
    });
    await expect(question).toContainText('revision history');
    const cancel = question.getByRole('button', { name: 'Cancel' });
    const remove = question.getByRole('button', { name: 'Delete job' });
    await expect(cancel).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(remove).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(cancel).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(remove).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(question).toHaveCount(0);
    await expect(menu).toBeFocused();
    await expect(
      page.getByRole('button', { name: /^New Wan job · WAN/ }),
    ).toBeVisible();

    // Unsaved edits ask first, on both routes away from the job.
    await page.getByLabel('Training steps', { exact: true }).fill('700');
    await page.getByRole('button', { name: 'New H3 job', exact: true }).click();
    const discard = page.getByRole('dialog', {
      name: 'Discard unsaved job edits?',
    });
    await discard.getByRole('button', { name: 'Keep editing' }).click();
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveValue('700');
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    const leave = page.getByRole('dialog', { name: 'Leave this job?' });
    await leave.getByRole('button', { name: 'Keep editing' }).click();
    await expect(page.getByRole('heading', { name: 'Jobs' })).toBeVisible();
    await page.getByRole('button', { name: 'Runs', exact: true }).click();
    await leave.getByRole('button', { name: 'Discard changes' }).click();
    await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible();
  } finally {
    await app.close();
  }
});

test('Jobs names components as Settings does and leaves out internal wording', async ({
  page,
}) => {
  const app = await workspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await page.getByText('Model components', { exact: true }).click();
    const component = page.getByRole('textbox', { name: 'Wan low-noise DiT' });
    await expect(component).toBeVisible();
    await expect(page.getByText('wan_low', { exact: true })).toHaveCount(0);
    await expect(page.getByText('IMMUTABLE RUNS')).toHaveCount(0);
    await expect(page.getByText(/39 crashes/)).toHaveCount(0);
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByText('Model component paths').click();
    await expect(
      page.getByRole('textbox', { name: 'Wan low-noise DiT' }),
    ).toBeVisible();
  } finally {
    await app.close();
  }
});

test('job actions explain themselves with no job open, and conversion names fields readably', async ({
  page,
}) => {
  const app = await workspace();
  try {
    await openJobs(page, app.url);
    await page.getByRole('button', { name: 'More job actions' }).click();
    await expect(page.getByRole('menu')).toContainText(
      'Open a job to use the job actions.',
    );
    await page.getByRole('menuitem', { name: /^Import/ }).click();
    await expect(page.getByText('Open a job to export it.')).toBeVisible();
    await expect(
      page.getByText('Choose a file or paste JSON to import it.'),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await jobAction(page, 'Change model to H3 T2VA');
    const dialog = page.getByRole('dialog', {
      name: 'Confirm model conversion',
    });
    await expect(dialog).toContainText('blocks to swap');
    await expect(dialog).not.toContainText('blocks_to_swap');
  } finally {
    await app.close();
  }
});

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
      'resolution = [320, 480]',
    );
    await expect(page.getByTestId('dataset-toml')).toContainText(
      'target_frames = [33]',
    );
    await page.getByText('Advanced', { exact: true }).click();
    await page.getByText('train arguments', { exact: true }).click();
    await expect(
      page
        .locator('details details')
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

test('a new draft starts realistic, warns about low steps and offers the smoke-test preset', async ({
  page,
}) => {
  const app = await workspace();
  try {
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    await expect(
      page.getByRole('spinbutton', { name: /^Width\b/ }),
    ).toHaveValue('832');
    await expect(
      page.getByLabel('Training steps', { exact: true }),
    ).toHaveValue('600');
    await expect(
      page.getByRole('textbox', { name: 'Sample prompt' }),
    ).toHaveValue('');
    await page.getByLabel('Training steps', { exact: true }).fill('2');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await expect(page.getByText(/^Training steps: 2 steps/)).toBeVisible();
    await expect(page.getByText(/^Sampling: enter a prompt/)).toBeVisible();
    await page
      .getByRole('combobox', { name: 'Compatible preset', exact: true })
      .selectOption({ label: 'Smoke test · Wan · 2 steps' });
    await page
      .getByRole('button', { name: 'Apply preset', exact: true })
      .click();
    await expect(
      page.getByRole('spinbutton', { name: /^Width\b/ }),
    ).toHaveValue('256');
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
        { timeout: 240_000 },
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
    await jobAction(page, 'Duplicate job');
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
    await jobAction(page, 'Change model to H3 T2VA');
    await expect(
      page.getByRole('dialog', { name: 'Confirm model conversion' }),
    ).toContainText('frames: 33 → 124');
    await page
      .getByRole('button', { name: 'Cancel conversion', exact: true })
      .click();
    await expect(page.getByRole('combobox', { name: /^Recipe/ })).toHaveValue(
      'low-noise',
    );
    await jobAction(page, 'Import / export configuration');
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
    await jobAction(page, 'Change model to H3 T2VA');
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

test('job actions wait for the initial load so it cannot replace new drafts', async ({
  page,
}) => {
  const app = await workspace();
  try {
    let release = () => {};
    const held = new Promise<void>((resolve) => (release = resolve));
    await page.route('**/api/jobs/presets', async (route) => {
      await held;
      await route.continue();
    });
    await openJobs(page, app.url);
    const create = page.getByRole('button', {
      name: 'New Wan job',
      exact: true,
    });
    await expect(create).toBeDisabled();
    release();
    await create.click();
    await page.getByLabel('Job name', { exact: true }).fill('After load');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByRole('status')).toContainText('Draft saved');
    await expect(
      page.getByRole('button', { name: 'After load · WAN · r2', exact: true }),
    ).toBeVisible();
  } finally {
    await app.close();
  }
});

test('the job form uses short fields for short values and shares rows between related ones', async ({
  page,
}) => {
  const app = await workspace();
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openJobs(page, app.url);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    const box = async (label: string) =>
      (await page
        .getByRole('spinbutton', { name: new RegExp(`^${label}`) })
        .boundingBox())!;
    for (const label of ['Blocks to swap', 'Width', 'Training steps'])
      expect((await box(label)).width).toBeLessThan(200);
    const width = await box('Width');
    expect((await box('Height')).y).toBe(width.y);
    expect((await box('Frames')).y).toBe(width.y);
    // The header and its repeated title are gone.
    await expect(page.locator('main header')).toHaveCount(0);
    // It was 3381 px with the header, full-width number fields and larger gaps.
    expect(
      await page.evaluate(() => document.documentElement.scrollHeight),
    ).toBeLessThan(3200);
  } finally {
    await app.close();
  }
});
