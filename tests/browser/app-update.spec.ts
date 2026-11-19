import { expect, test } from '@playwright/test';
import { execFile } from 'node:child_process';
import {
  mkdtemp,
  mkdir,
  readFile,
  realpath,
  rm,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { helper, localBackend } from './backend';

const exec = promisify(execFile);
const origin = 'https://github.com/barnabyg/musubiGui.git';

async function git(root: string, ...args: string[]) {
  return (await exec('git', ['-C', root, ...args])).stdout.trim();
}

async function commit(root: string, message: string) {
  await git(root, 'add', '.');
  await git(
    root,
    '-c',
    'user.name=Fixture',
    '-c',
    'user.email=fixture@localhost',
    'commit',
    '-m',
    message,
  );
  return git(root, 'rev-parse', 'HEAD');
}

test('updates the GUI, reconnects and diagnoses the newly supported trainer with one click', async ({
  page,
}) => {
  const root = await realpath(
    await mkdtemp(path.join(tmpdir(), 'musubi update ')),
  );
  const upstream = path.join(root, 'trainer');
  const remote = path.join(root, 'gui-remote');
  const checkout = path.join(root, 'gui');
  const pin = path.join(checkout, 'pin.txt');
  const state = path.join(root, 'state');
  await mkdir(upstream);
  await mkdir(remote);
  const { url, start, stop } = await localBackend({
    state,
    readyRoute: '/api/app-update',
    cwd: checkout,
    entry: [helper, 'supervise-setup'],
    args: [
      '--remote',
      upstream,
      '--pin-file',
      pin,
      '--device',
      path.join(root, 'device.sqlite3'),
    ],
    env: { PYTHONPATH: process.cwd() },
  });
  try {
    await git(upstream, 'init');
    for (const name of [
      'pyproject.toml',
      'wan_train_network.py',
      'minimax_h3_train_network.py',
    ]) {
      await writeFile(path.join(upstream, name), '# synthetic fixture');
    }
    const originalPin = await commit(upstream, 'original trainer');
    await writeFile(
      path.join(upstream, 'trainer.py'),
      '# newly supported trainer',
    );
    const nextPin = await commit(upstream, 'supported trainer');
    await writeFile(path.join(upstream, 'trainer.py'), '# upstream head');
    await commit(upstream, 'unsupported trainer');

    await git(remote, 'init', '--initial-branch=main');
    await writeFile(
      path.join(remote, '.gitignore'),
      '/.cache/\n/musubi-tuner/\n',
    );
    await writeFile(path.join(remote, 'pin.txt'), originalPin);
    await commit(remote, 'original GUI');
    await git(root, 'clone', remote, checkout);
    await git(checkout, 'remote', 'set-url', 'origin', origin);
    await git(
      checkout,
      'config',
      `url.${remote.replaceAll('\\', '/')}.insteadOf`,
      origin,
    );
    await writeFile(path.join(remote, 'pin.txt'), nextPin);
    const nextGui = await commit(remote, 'GUI supports new trainer');

    await start();
    await page.goto(`${url}/#/settings`);
    await page
      .getByRole('button', { name: 'Install supported version' })
      .click();
    const environment = page.locator('#settings-environment');
    await expect(
      environment.getByText('Supported version installed.', { exact: true }),
    ).toBeVisible({ timeout: 240_000 });
    expect(
      await git(path.join(checkout, 'musubi-tuner'), 'rev-parse', 'HEAD'),
    ).toBe(originalPin);

    const update = page.getByRole('button', { name: 'Update application' });
    await writeFile(pin, 'local change');
    await update.click();
    await expect(
      page.getByText(/GUI checkout has local changes/),
    ).toBeVisible();
    expect(await readFile(pin, 'utf8')).toBe('local change');
    await writeFile(pin, originalPin);

    const serverBefore = (
      await (await page.request.get(`${url}/api/app-update`)).json()
    ).server;
    await Promise.all([
      page.waitForEvent('load', { timeout: 240_000 }),
      update.click(),
    ]);
    await expect(
      page.getByText(/Update complete\. Diagnostics found problems/),
    ).toBeVisible({ timeout: 240_000 });
    await expect(
      page.getByRole('heading', { name: 'Installation diagnostics' }),
    ).toBeVisible();
    await expect(page.getByText('Training entry points found.')).toBeVisible();
    const serverAfter = (
      await (await page.request.get(`${url}/api/app-update`)).json()
    ).server;
    expect(serverAfter).not.toBe(serverBefore);
    expect(await git(checkout, 'rev-parse', 'HEAD')).toBe(nextGui);
    expect(
      await git(path.join(checkout, 'musubi-tuner'), 'rev-parse', 'HEAD'),
    ).toBe(nextPin);
    expect(
      (await readFile(path.join(checkout, '.cache/gui-builds.txt'), 'utf8'))
        .trim()
        .split(/\r?\n/),
    ).toEqual([originalPin, nextPin]);
    await page.reload();
    await expect(
      page.getByRole('heading', { name: 'Installation diagnostics' }),
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
