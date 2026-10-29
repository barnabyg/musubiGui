import { test, expect } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
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
import { createServer } from 'node:net';
import { stopBackendTree } from './processes';

async function settingsBackend(root: string) {
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
        cwd: root,
        env: {
          ...process.env,
          PYTHONPATH: process.cwd(),
          MUSUBI_GUI_STATE: path.join(root, 'state'),
        },
        stdio: 'pipe',
      },
    );
    await expect
      .poll(async () => {
        try {
          return (await fetch(`${url}/api/settings`)).status;
        } catch {
          return 0;
        }
      })
      .toBe(200);
  };
  const stop = async () => {
    if (backend && backend.exitCode === null && backend.signalCode === null) {
      const exited = new Promise<void>((resolve) =>
        backend!.once('exit', () => resolve()),
      );
      if (!backend.pid) throw new Error('Missing backend PID');
      await stopBackendTree(backend.pid);
      await exited;
    }
  };
  return { url, start, stop };
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
      .getByLabel('musubi-tuner checkout')
      .fill(path.join(root, 'missing'));
    await page
      .getByLabel('Training Python executable')
      .fill(path.resolve('.venv/Scripts/python.exe'));
    await page.getByLabel('Output root').fill(path.join(root, 'output'));
    await page.getByLabel('Cache root').fill(path.join(root, 'cache'));
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
    await page.getByRole('button', { name: 'Create managed dataset' }).click();
    await expect(page.getByText('Scan: complete 0/0')).toBeVisible();
    expect(await readdir(defaultDatasets)).toHaveLength(1);
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.getByRole('button', { name: 'Run diagnostics' }).click();
    await expect(
      page.getByText(
        'Select a musubi-tuner checkout containing both training entry points.',
      ),
    ).toBeVisible();
    await page.getByLabel('musubi-tuner checkout').fill(checkout);
    await page.getByRole('button', { name: 'Save settings' }).click();
    await expect(page.getByRole('status')).toHaveText('Settings saved');
    await stop();
    await start();
    await page.reload();
    await expect(page.getByLabel('Dataset roots (one per line)')).toHaveValue(
      defaultDatasets,
    );
    await expect(page.getByLabel('musubi-tuner checkout')).toHaveValue(
      checkout,
    );
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
      .locator('.check.warning')
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
