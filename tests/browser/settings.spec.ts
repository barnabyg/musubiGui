import { test, expect } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createServer } from 'node:net';

test('settings survive backend restart and invalid installations explain corrections', async ({
  page,
}) => {
  const root = await mkdtemp(path.join(tmpdir(), 'musubi-setup-'));
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
          return (await fetch(`${url}/api/settings`)).status;
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
      backend.kill();
      await exited;
    }
  };
  try {
    await start();
    await page.goto(url);
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
    await rm(root, { recursive: true, force: true });
  }
});
