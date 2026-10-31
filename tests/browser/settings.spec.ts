import { test, expect } from '@playwright/test';
import {
  mkdtemp,
  mkdir,
  readFile,
  readdir,
  realpath,
  rm,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { localBackend } from './backend';

function settingsBackend(root: string) {
  return localBackend({
    state: path.join(root, 'state'),
    readyRoute: '/api/settings',
    cwd: root,
    env: { PYTHONPATH: process.cwd() },
  });
}

test('settings survive backend restart and invalid installations explain corrections', async ({
  page,
}) => {
  // Windows CI's TEMP can use an 8.3 alias; the backend returns resolved paths.
  const root = await realpath(
    await mkdtemp(path.join(tmpdir(), 'musubi-setup-')),
  );
  const checkout = path.join(root, '安装 with spaces');
  await mkdir(checkout);
  await writeFile(
    path.join(checkout, 'wan_train_network.py'),
    '# fixture checkout',
  );
  await writeFile(
    path.join(checkout, 'minimax_h3_train_network.py'),
    '# fixture checkout',
  );
  const { url, start, stop } = await settingsBackend(root);
  try {
    await start();
    await page.goto(url);
    const defaultDatasets = path.join(root, 'datasets');
    await expect(page.getByLabel('Dataset roots (one per line)')).toHaveValue(
      defaultDatasets,
    );
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page.getByRole('button', { name: 'New dataset' }).click();
    await expect(
      page.getByLabel('Dataset folder', { exact: true }),
    ).toHaveValue(defaultDatasets);
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page
      .getByLabel('musubi-tuner checkout', { exact: true })
      .fill(path.join(root, 'missing'));
    await page
      .getByLabel('Training Python executable', { exact: true })
      .fill(path.resolve('.venv/Scripts/python.exe'));
    await page
      .getByLabel('Output root', { exact: true })
      .fill(path.join(root, 'output'));
    await page
      .getByLabel('Cache root', { exact: true })
      .fill(path.join(root, 'cache'));
    await page.getByRole('button', { name: 'Save settings' }).click();
    await expect(page.getByRole('status')).toHaveText('Settings saved');
    expect(
      JSON.parse(await readFile(path.join(root, 'state/settings.json'), 'utf8'))
        .dataset_roots,
    ).toEqual([defaultDatasets]);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page
      .getByLabel('Dataset name', { exact: true })
      .fill('Default location');
    await page
      .getByRole('button', { name: 'Create a new folder for uploads' })
      .click();
    await expect(page.getByText('Scan: complete 0/0')).toBeVisible();
    expect(await readdir(defaultDatasets)).toHaveLength(1);
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('button', { name: 'Run diagnostics' }).click();
    await expect(
      page.getByText(
        'Select a musubi-tuner checkout containing both training entry points.',
      ),
    ).toBeVisible();
    await page
      .getByLabel('musubi-tuner checkout', { exact: true })
      .fill(checkout);
    await page.getByRole('button', { name: 'Save settings' }).click();
    await expect(page.getByRole('status')).toHaveText('Settings saved');
    await stop();
    await start();
    await page.reload();
    await expect(page.getByLabel('Dataset roots (one per line)')).toHaveValue(
      defaultDatasets,
    );
    await expect(
      page.getByLabel('musubi-tuner checkout', { exact: true }),
    ).toHaveValue(checkout);
    await page.getByRole('button', { name: 'Run diagnostics' }).click();
    await expect(page.getByText('Training entry points found.')).toBeVisible();
    await expect(
      page.getByText(
        'Install PyTorch in the selected training environment; the GUI will not change it.',
      ),
    ).toBeVisible();
  } finally {
    await stop();
    await rm(root, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
  }
});

test('diagnostics persist, drive the sidebar and go out of date when settings change', async ({
  page,
}) => {
  const root = await realpath(
    await mkdtemp(path.join(tmpdir(), 'musubi-health-')),
  );
  const { url, start, stop } = await settingsBackend(root);
  try {
    await start();
    await page.goto(url);
    const health = page.getByRole('button', { name: /^Setup:/ });
    await expect(health).toContainText('Setup: Not checked');
    // Sections aren't numbered, because they aren't a sequence.
    await expect(
      page.getByRole('heading', { name: 'Training environment' }),
    ).toBeVisible();
    await expect(page.getByText(/^0[12]$/)).toHaveCount(0);
    await page.getByRole('button', { name: 'Run diagnostics' }).click();
    await expect(health).toContainText('Setup: Problems found');
    const report = page.locator('#diagnostics-report');
    await expect(report).toContainText(
      'These checks do not establish model support',
    );
    await expect(report).toContainText('Last run');

    await stop();
    await start();
    await page.reload();
    await expect(health).toContainText('Setup: Problems found');
    await expect(report).toContainText('Last run');

    await page.getByRole('button', { name: 'Jobs', exact: true }).click();
    await expect(report).toHaveCount(0);
    await health.click();
    await expect(report).toBeInViewport();

    await page
      .getByLabel('Cache root', { exact: true })
      .fill(path.join(root, 'cache'));
    await expect(health).toContainText('out of date');
    await expect(report).toContainText('Out of date');
    await page.getByRole('button', { name: 'Save settings' }).click();
    await expect(page.getByRole('status')).toHaveText('Settings saved');
    await expect(report).toContainText('Out of date');
    await page.reload();
    await expect(health).toContainText('out of date');

    // Only warnings isn't Ready: with no model paths, training can't run yet.
    await page.route('**/api/diagnostics', async (route) => {
      await route.fulfill({
        json: {
          ran_at: '2026-10-31T00:00:00+00:00',
          current: true,
          environment: {},
          checks: [{ name: 'Models', status: 'warning', message: 'None set.' }],
        },
      });
    });
    await page.reload();
    await expect(health).toContainText('Setup: 1 to check');
    await page.unroute('**/api/diagnostics');
  } finally {
    await stop();
    await rm(root, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
  }
});

test('diagnostic rows share one layout and state their status in a badge', async ({
  page,
}) => {
  const root = await realpath(
    await mkdtemp(path.join(tmpdir(), 'musubi-checks-')),
  );
  const { url, start, stop } = await settingsBackend(root);
  try {
    await start();
    await page.route('**/api/diagnostics', async (route) => {
      await route.fulfill({
        json: {
          ran_at: '2026-10-31T00:00:00+00:00',
          current: true,
          environment: {},
          checks: [
            { name: 'Python', status: 'ok', message: 'Found.' },
            { name: 'Roots', status: 'warning', message: 'None set.' },
            { name: 'PyTorch', status: 'error', message: 'Not installed.' },
          ],
        },
      });
    });
    await page.goto(url);
    const rows = page.locator('.check');
    await expect(rows).toHaveCount(3);
    await expect(rows.locator('.status-badge')).toHaveText([
      'Passed',
      'Warning',
      'Failed',
    ]);
    const layout = await rows.evaluateAll((elements) =>
      elements.map((element) => {
        const style = getComputedStyle(element);
        return {
          left: element.getBoundingClientRect().left,
          textLeft: element.querySelector('strong')!.getBoundingClientRect()
            .left,
          badgeRight: element
            .querySelector('.status-badge')!
            .getBoundingClientRect().right,
          background: style.backgroundColor,
          padding: style.paddingLeft,
        };
      }),
    );
    expect(layout[1]).toEqual(layout[0]);
    expect(layout[2]).toEqual(layout[0]);
  } finally {
    await stop();
    await rm(root, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
  }
});

test('path fields warn before saving, browse local folders and show the full path', async ({
  page,
}) => {
  const root = await realpath(
    await mkdtemp(path.join(tmpdir(), 'musubi-paths-')),
  );
  const chosen = path.join(root, 'chosen folder');
  await mkdir(chosen);
  await writeFile(path.join(root, 'plain.txt'), 'not a folder');
  const { url, start, stop } = await settingsBackend(root);
  try {
    await start();
    await page.goto(url);
    const output = page.getByLabel('Output root', { exact: true });
    await output.fill(path.join(root, 'missing'));
    await expect(
      page.getByText("This folder doesn't exist.", { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole('status')).toHaveText('Unsaved changes');
    expect(
      await page.request
        .get(`${url}/api/settings`)
        .then((response) => response.json()),
    ).toMatchObject({ output_root: '' });

    // Warnings arriving after typing leave the buttons below where they were.
    const save = page.getByRole('button', { name: 'Save settings' });
    const pageTop = (button: typeof save) =>
      button.evaluate(
        (element) => element.getBoundingClientRect().top + window.scrollY,
      );
    const saveAt = () => pageTop(save);
    const before = await saveAt();
    await output.fill(chosen);
    await expect(
      page.locator('.path-field', { has: output }).getByRole('alert'),
    ).toHaveCount(0);
    expect(await saveAt()).toBe(before);
    await page
      .getByLabel('Dataset roots (one per line)')
      .fill(
        `${path.join(root, 'a')}\n${path.join(root, 'b')}\n${path.join(root, 'c')}`,
      );
    await expect(
      page.getByText(`${path.join(root, 'c')}: This folder doesn't exist.`),
    ).toBeAttached();
    expect(await saveAt()).toBe(before);
    await page.getByLabel('Dataset roots (one per line)').fill(root);
    await page.getByRole('button', { name: 'Datasets', exact: true }).click();
    await page.getByRole('button', { name: 'New dataset' }).click();
    const folder = page.getByLabel('Dataset folder', { exact: true });
    const useExisting = page.getByRole('button', {
      name: 'Use existing folder',
    });
    await folder.fill(path.join(root, 'datasets', 'missing'));
    await expect(
      page.getByText("This folder doesn't exist yet."),
    ).toBeVisible();
    const buttonAt = await pageTop(useExisting);
    await folder.fill('');
    await expect(page.getByText("This folder doesn't exist yet.")).toHaveCount(
      0,
    );
    expect(await pageTop(useExisting)).toBe(buttonAt);
    await page.getByRole('button', { name: 'Settings', exact: true }).click();

    // The wrong kind of thing is named before saving, too.
    await output.fill(path.join(root, 'plain.txt'));
    await expect(page.getByText('This is not a folder.')).toBeVisible();

    // The whole path stays readable however narrow the field is.
    await output.focus();
    await expect(output).toHaveAttribute('title', path.join(root, 'plain.txt'));
    await expect(page.locator('.path-full')).toHaveText(
      path.join(root, 'plain.txt'),
    );

    await page.getByRole('button', { name: 'Browse for Output root' }).click();
    const browser = page.getByRole('dialog', { name: 'Choose Output root' });
    await expect(browser.locator('.path-location')).toHaveText(root);
    await expect(browser.getByText('plain.txt')).toHaveCount(0);
    await browser.getByRole('button', { name: /chosen folder/ }).click();
    await expect(browser.locator('.path-location')).toHaveText(chosen);
    await browser.getByRole('button', { name: 'Up', exact: true }).click();
    await expect(browser.locator('.path-location')).toHaveText(root);
    await browser.getByRole('button', { name: /chosen folder/ }).click();
    await browser.getByRole('button', { name: 'Choose this folder' }).click();
    await expect(output).toHaveValue(chosen);
    await expect(page.getByText('This is not a folder.')).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Browse for Output root' }),
    ).toBeFocused();

    // Typing still works, and a component outside its root is called out.
    await page.getByLabel('Model roots (one per line)').fill(chosen);
    await page.getByText('Model component paths').click();
    await page
      .getByLabel('Wan low-noise DiT', { exact: true })
      .fill(path.join(root, 'plain.txt'));
    await expect(
      page.getByText('This is outside the configured roots.'),
    ).toBeVisible();

    // A path Browse can't start from still leaves the drives to choose from.
    await output.fill('not absolute');
    await page.getByRole('button', { name: 'Browse for Output root' }).click();
    const recovered = page.getByRole('dialog', { name: 'Choose Output root' });
    await expect(recovered.getByRole('alert')).toContainText('absolute');
    await expect(recovered.locator('.path-location')).toHaveText('Drives');
    await expect(
      recovered
        .getByRole('list', { name: 'Folder contents' })
        .getByRole('button'),
    ).not.toHaveCount(0);
    await recovered.getByRole('button', { name: 'Cancel' }).click();

    // The same root twice is one line to check.
    const errors: string[] = [];
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    await page
      .getByLabel('Model roots (one per line)')
      .fill(`${chosen}\n${chosen}`);
    await expect(page.getByLabel('Model roots (one per line)')).toHaveValue(
      `${chosen}\n${chosen}`,
    );
    expect(errors).toEqual([]);

    // File fields choose a file.
    await page
      .getByRole('button', { name: 'Browse for Training Python executable' })
      .click();
    const files = page.getByRole('dialog', {
      name: 'Choose Training Python executable',
    });
    await files.getByRole('button', { name: 'Cancel' }).click();
    await expect(files).toHaveCount(0);
  } finally {
    await stop();
    await rm(root, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
  }
});

test('typed Enter separates model and dataset roots and diagnostics name missing roots', async ({
  page,
}) => {
  const root = await realpath(
    await mkdtemp(path.join(tmpdir(), 'musubi-roots-')),
  );
  const firstModels = path.join(root, 'models');
  const secondModels = path.join(root, 'derived');
  const datasets = path.join(root, 'datasets');
  const sampleVids = path.join(root, 'sample_vids');
  for (const folder of [firstModels, secondModels, datasets])
    await mkdir(folder);
  const { url, start, stop } = await settingsBackend(root);
  try {
    await start();
    await page.goto(url);
    const modelRoots = page.getByLabel('Model roots (one per line)');
    const datasetRoots = page.getByLabel('Dataset roots (one per line)');
    await expect(datasetRoots).toHaveValue(datasets);
    await modelRoots.pressSequentially(firstModels);
    await modelRoots.press('Enter');
    await modelRoots.pressSequentially(secondModels);
    await expect(modelRoots).toHaveValue(`${firstModels}\n${secondModels}`);
    await datasetRoots.press('ControlOrMeta+A');
    await datasetRoots.press('Delete');
    await datasetRoots.pressSequentially(`  ${datasets}`);
    await datasetRoots.press('Enter');
    await datasetRoots.press('Enter');
    await datasetRoots.pressSequentially(`${sampleVids}  `);
    await datasetRoots.press('Enter');
    await expect(datasetRoots).toHaveValue(
      `  ${datasets}\n\n${sampleVids}  \n`,
    );
    await page.getByRole('button', { name: 'Save settings' }).click();
    await expect(page.getByRole('status')).toHaveText('Settings saved');
    await expect(datasetRoots).toHaveValue(`${datasets}\n${sampleVids}`);
    const saved = (await (await fetch(`${url}/api/settings`)).json()) as {
      model_roots: string[];
      dataset_roots: string[];
    };
    expect(saved.model_roots).toEqual([firstModels, secondModels]);
    expect(saved.dataset_roots).toEqual([datasets, sampleVids]);
    await page.reload();
    await expect(modelRoots).toHaveValue(`${firstModels}\n${secondModels}`);
    await expect(datasetRoots).toHaveValue(`${datasets}\n${sampleVids}`);
    await page.getByRole('button', { name: 'Run diagnostics' }).click();
    const warning = page
      .locator('.check-warning')
      .filter({ hasText: 'Dataset roots' });
    await expect(warning).toContainText(
      `Not an existing folder: ${sampleVids}.`,
    );
    await expect(warning).not.toContainText(datasets);
    await expect(page.getByText('Model roots', { exact: true })).toHaveCount(0);
  } finally {
    await stop();
    await rm(root, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
  }
});
