import { test, expect } from '@playwright/test';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import path from 'node:path';
import { helper, python } from './backend';
import { execute, PNG, runWorkspace, type RunRecord } from './runHarness';

// Registering returns before its scan ends, and the Jobs screen shows the counts it loads.
async function registerScanned(
  app: Awaited<ReturnType<typeof runWorkspace>>,
  name: string,
  folder: string,
) {
  const { id } = (await app.post('/api/datasets', {
    name,
    path: folder,
  })) as { id: string };
  await expect
    .poll(
      async () =>
        (
          (await (await fetch(`${app.url}/api/datasets/${id}`)).json()) as {
            scan: { state: string };
          }
        ).scan.state,
      { timeout: 240_000 },
    )
    .toBe('complete');
  return id;
}

test('a Wan job combines an image dataset and a video dataset through review, restart and preparation', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const datasets = path.join(app.fixture, 'datasets');
    const [stills, clips] = [
      path.join(datasets, 'stills'),
      path.join(datasets, 'clips'),
    ];
    await mkdir(stills);
    await writeFile(path.join(stills, 'a.png'), PNG);
    await writeFile(path.join(stills, 'a.txt'), 'A still.');
    await writeFile(path.join(stills, 'b.png'), PNG);
    await writeFile(path.join(stills, 'b.txt'), 'Another still.');
    await execute(python, [
      helper,
      'video',
      path.join(clips, 'c.mp4'),
      '33',
      '16',
    ]);
    await writeFile(path.join(clips, 'c.txt'), 'A clip.');
    for (const [name, folder] of [
      ['Susan images', stills],
      ['Susan videos', clips],
    ])
      await registerScanned(app, name, folder);
    await page.goto(`${app.url}/#/jobs`);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();

    await page.getByRole('tab', { name: 'Presets', exact: true }).click();
    await page
      .getByRole('combobox', { name: 'Compatible preset' })
      .selectOption({ label: 'Smoke test · Wan alternating · 4 steps' });
    await page
      .getByRole('button', { name: 'Apply preset', exact: true })
      .click();
    await expect(page.getByRole('status')).toContainText('Preset applied');

    // The library lists by name: H3 acceptance, the mixed dataset, then Susan images.
    // By keyboard: arrow to the dataset, Tab to the button, Enter. Focus returns to add another.
    const add = page.getByRole('combobox', { name: 'Add a dataset' });
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await add.focus();
    await add.press('ArrowDown');
    await add.press('ArrowDown');
    await add.press('ArrowDown');
    await expect(add).not.toHaveValue('');
    await page.keyboard.press('Tab');
    const addButton = page.getByRole('button', {
      name: 'Add dataset',
      exact: true,
    });
    await expect(addButton).toBeFocused();
    await page.keyboard.press('Enter');
    const rows = page.getByRole('list', { name: 'Datasets in this job' });
    await expect(rows.getByRole('listitem')).toHaveCount(1);
    await expect(add).toBeFocused();
    await expect(rows).toContainText('Susan images');
    await expect(rows).toContainText('2 images');
    // A listed dataset is no longer offered.
    await expect(add.getByRole('option', { name: /Susan images/ })).toHaveCount(
      0,
    );
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await add.selectOption({ label: 'Susan videos (1 video)' });
    await addButton.click();
    await expect(rows.getByRole('listitem')).toHaveCount(2);

    // Remove by keyboard returns focus to where another can be added.
    await page.getByRole('button', { name: 'Remove Susan videos' }).focus();
    await page.keyboard.press('Enter');
    await expect(rows.getByRole('listitem')).toHaveCount(1);
    await expect(add).toBeFocused();
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await add.selectOption({ label: 'Susan videos (1 video)' });
    await addButton.click();

    const imageRepeats = page.getByLabel('Repeats for Susan images');
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(imageRepeats).toHaveAttribute('placeholder', '1');
    await imageRepeats.fill('2');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const sections = page.getByRole('table', { name: 'Training sections' });
    await expect(sections.locator('tbody tr')).toHaveCount(2);
    await expect(
      sections.locator('tbody tr').first().locator('th, td'),
    ).toHaveText([
      'Susan images',
      'Images',
      '256 × 256',
      '2',
      '2',
      '4',
      '80.0%',
    ]);
    await expect(
      sections.locator('tbody tr').last().locator('th, td'),
    ).toHaveText([
      'Susan videos',
      'Videos, 16 fps',
      '256 × 256',
      '1',
      '1',
      '1',
      '20.0%',
    ]);
    await page.setViewportSize({ width: 760, height: 900 });
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth,
      ),
    ).toBe(true);
    await page.setViewportSize({ width: 1280, height: 720 });
    await page
      .getByRole('button', { name: 'Back to jobs', exact: true })
      .click();
    await expect(
      page
        .getByRole('table', { name: 'Saved jobs' })
        .locator('tbody tr')
        .first(),
    ).toContainText('Susan images +1');
    await page.getByRole('button', { name: /^New Wan job · WAN · r/ }).click();

    await app.crash();
    await app.start();
    await page.reload();
    await expect(rows.getByRole('listitem')).toHaveCount(2);
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(page.getByLabel('Repeats for Susan images')).toHaveValue('2');
    await expect(page.getByLabel('Repeats for Susan videos')).toHaveValue('');

    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await expect(page.getByRole('status')).toContainText('Review passed');
    await page
      .getByRole('button', { name: 'Prepare only', exact: true })
      .click();
    await expect(
      page.getByRole('heading', { name: 'Run details' }),
    ).toBeVisible();
    let prepared: RunRecord | undefined;
    await expect
      .poll(
        async () => {
          prepared = (await app.records()).find(
            (run) => run.job.name === 'New Wan job',
          );
          return prepared?.status;
        },
        { timeout: 240_000 },
      )
      .toBe('prepared');
    const toml = await readFile(
      path.join(prepared!.directory, 'dataset.toml'),
      'utf8',
    );
    expect(toml).toContain('media/set-1"');
    expect(toml).toContain('dataset-1-images"');
    expect(toml).toContain('num_repeats = 2');
    expect(toml).toContain('media/set-2"');
    expect(toml).toContain('dataset-2"');
    expect(prepared!.manifest.items).toHaveLength(3);
  } finally {
    await app.close();
  }
});

test('a review error about an empty dataset opens its gallery, and a deleted dataset can be removed', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const empty = path.join(app.fixture, 'datasets', 'empty');
    await mkdir(empty);
    const dataset = { id: await registerScanned(app, 'Nothing here', empty) };
    await page.goto(`${app.url}/#/jobs`);
    await page
      .getByRole('button', { name: 'New Wan job', exact: true })
      .click();
    const add = page.getByRole('combobox', { name: 'Add a dataset' });
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await add.selectOption(dataset.id);
    await page
      .getByRole('button', { name: 'Add dataset', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    const repeats = page.getByLabel('Repeats for Nothing here');
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await expect(repeats).not.toHaveAttribute('aria-invalid', 'true');
    await page
      .getByRole('link', { name: /^Dataset "Nothing here": no valid/ })
      .press('Enter');
    await expect(
      page.getByRole('heading', { name: 'Nothing here' }),
    ).toBeVisible();
    await page
      .getByRole('button', { name: 'Return to job and review' })
      .click();
    await expect(
      page.getByRole('link', { name: /^Dataset "Nothing here": no valid/ }),
    ).toBeVisible();

    const deleted = await page.request.delete(
      `${app.url}/api/datasets/${dataset.id}`,
      { data: {} },
    );
    expect(deleted.status()).toBe(200);
    await page.reload();
    const rows = page.getByRole('list', { name: 'Datasets in this job' });
    await expect(rows).toContainText('Deleted dataset');
    await expect(rows).toContainText('No longer in the library');
    await page
      .getByRole('button', { name: 'Review configuration', exact: true })
      .click();
    await page.getByRole('button', { name: /no longer registered/ }).click();
    const remove = page.getByRole('button', { name: 'Remove deleted dataset' });
    await expect(remove).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(rows).toHaveCount(0);
  } finally {
    await app.close();
  }
});

test('removing one row of a dataset listed twice keeps the other', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const job = app.manifest.jobs.find((item) => item.family === 'wan')!;
    const saved = (await (
      await fetch(`${app.url}/api/jobs/${job.id}`)
    ).json()) as { datasets: { dataset_id: string }[] } & Record<
      string,
      unknown
    >;
    const twice = await fetch(`${app.url}/api/jobs/${job.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...saved,
        version: undefined,
        id: undefined,
        datasets: [
          { dataset_id: saved.datasets[0].dataset_id, repeats: 2 },
          { dataset_id: saved.datasets[0].dataset_id, repeats: 3 },
        ],
      }),
    });
    expect(twice.ok).toBe(true);
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    const rows = page.getByRole('list', { name: 'Datasets in this job' });
    await expect(rows.getByRole('listitem')).toHaveCount(2);
    await rows
      .getByRole('button', { name: /^Remove/ })
      .first()
      .click();
    await expect(rows.getByRole('listitem')).toHaveCount(1);
    await expect(rows.getByRole('spinbutton')).toHaveValue('3');
  } finally {
    await app.close();
  }
});

test('an H3 job adds an image dataset, warns that it is experimental and uses the one-frame flags', async ({
  page,
}) => {
  const app = await runWorkspace();
  try {
    const stills = path.join(app.fixture, 'datasets', 'stills');
    await mkdir(stills);
    await writeFile(path.join(stills, 'a.png'), PNG);
    await writeFile(path.join(stills, 'a.txt'), 'A silent still.');
    await registerScanned(app, 'Susan images', stills);
    const job = app.manifest.jobs.find((item) => item.family === 'h3')!;
    await page.goto(`${app.url}/#/jobs/${job.id}`);
    const add = page.getByRole('combobox', { name: 'Add a dataset' });
    await expect(add.getByRole('option', { name: /Susan images/ })).toHaveCount(
      1,
    );
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await add.selectOption({ label: 'Susan images (1 image)' });
    await page
      .getByRole('button', { name: 'Add dataset', exact: true })
      .click();
    const review = page.getByRole('button', {
      name: 'Review configuration',
      exact: true,
    });
    await review.click();
    await expect(page.getByRole('status')).toContainText('Review passed');
    const panel = page.getByRole('region', { name: 'Configuration review' });
    await expect(panel).toContainText('experimental');
    await expect(panel).toContainText('every other model using it');
    await expect(panel).toContainText('1 supervised / 1 included');
    await page.getByText('Configuration details', { exact: true }).click();
    const args = (name: string) =>
      panel
        .locator('details details')
        .filter({ has: page.getByText(`${name} arguments`, { exact: true }) })
        .locator('pre');
    await page.getByText('train arguments', { exact: true }).click();
    await expect(args('train')).toContainText('--one_frame');
    await expect(args('train')).not.toContainText('--video_only');

    // Without the video dataset, training also turns off audio supervision.
    await page.getByRole('tab', { name: 'Job & dataset', exact: true }).click();
    await page
      .getByRole('button', { name: 'Remove H3 acceptance', exact: true })
      .click();
    await review.click();
    await expect(page.getByRole('status')).toContainText('Review passed');
    await expect(
      panel.getByRole('heading', { name: 'Audio coverage', exact: true }),
    ).toHaveCount(0);
    await page.getByText('Configuration details', { exact: true }).click();
    await page.getByText('train arguments', { exact: true }).click();
    await expect(args('train')).toContainText('--video_only');
  } finally {
    await app.close();
  }
});
