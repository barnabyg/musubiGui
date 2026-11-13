import { test, expect } from '@playwright/test';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import {
  mkdtemp,
  mkdir,
  realpath,
  writeFile,
  readFile,
  rm,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { helper, localBackend } from './backend';

const exec = promisify(execFile);

test('installs supported source, persists it and refuses to overwrite local changes', async ({
  page,
}) => {
  const root = await realpath(
    await mkdtemp(path.join(tmpdir(), 'musubi-managed-')),
  );
  const upstream = path.join(root, 'synthetic-upstream');
  await mkdir(upstream);
  const git = async (...args: string[]) =>
    (await exec('git', ['-C', upstream, ...args])).stdout.trim();
  await git('init');
  for (const name of [
    'pyproject.toml',
    'wan_train_network.py',
    'minimax_h3_train_network.py',
  ]) {
    await writeFile(path.join(upstream, name), '# synthetic fixture');
  }
  await git('add', '.');
  await git(
    '-c',
    'user.name=Fixture',
    '-c',
    'user.email=fixture@localhost',
    'commit',
    '-m',
    'supported',
  );
  const supported = await git('rev-parse', 'HEAD');
  await writeFile(path.join(upstream, 'new.py'), '# unsupported head');
  await git('add', '.');
  await git(
    '-c',
    'user.name=Fixture',
    '-c',
    'user.email=fixture@localhost',
    'commit',
    '-m',
    'unsupported',
  );
  const { url, start, stop } = await localBackend({
    state: path.join(root, 'state'),
    readyRoute: '/api/checkout',
    cwd: root,
    entry: [helper, 'serve-setup'],
    args: [
      '--remote',
      upstream,
      '--revision',
      supported,
      '--device',
      path.join(root, 'device.sqlite3'),
    ],
    env: { PYTHONPATH: process.cwd() },
  });
  try {
    await start();
    await page.goto(`${url}/#/settings`);
    const panel = page.locator('#settings-environment');
    await expect(panel).toContainText(supported);
    await page
      .getByRole('button', { name: 'Install supported version' })
      .click();
    await expect(
      panel.getByText('Supported version installed.', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    const installed = await exec('git', [
      '-C',
      path.join(root, 'musubi-tuner'),
      'rev-parse',
      'HEAD',
    ]);
    expect(installed.stdout.trim()).toBe(supported);
    await stop();
    await start();
    await page.reload();
    await expect(
      page.getByRole('button', { name: 'Update to supported version' }),
    ).toBeEnabled();
    const source = path.join(root, 'musubi-tuner', 'wan_train_network.py');
    await writeFile(source, '# keep this local change');
    await page
      .getByRole('button', { name: 'Update to supported version' })
      .click();
    await expect(panel).toContainText('local source changes', {
      timeout: 240_000,
    });
    expect(await readFile(source, 'utf8')).toBe('# keep this local change');
    await writeFile(source, '# synthetic fixture');
    await page
      .getByRole('button', { name: 'Update to supported version' })
      .click();
    await expect(panel).toContainText('Supported Musubi version installed.', {
      timeout: 240_000,
    });
    await page.getByRole('button', { name: 'Run diagnostics' }).click();
    await expect(page.getByText('Training entry points found.')).toBeVisible();
    await expect(
      page.getByText(/H3 fused INT8 acceleration is unavailable/),
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
