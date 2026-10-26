import { expect } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createServer } from 'node:net';

export async function datasetHarness(options: { h3Audio?: boolean } = {}) {
  const root = await mkdtemp(path.join(tmpdir(), 'musubi-dataset-'));
  const fixture = path.join(root, 'fixture');
  const python = path.resolve('.venv/Scripts/python.exe');
  const creator = spawn(
    python,
    [
      '-m',
      'musubi_gui.dataset_fixture',
      fixture,
      ...(options.h3Audio ? ['--h3-audio'] : []),
    ],
    {
      stdio: 'pipe',
    },
  );
  const exitCode = await new Promise<number | null>((resolve) =>
    creator.once('exit', resolve),
  );
  if (exitCode !== 0) throw new Error('Dataset fixture generation failed');
  const server = createServer();
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (!address || typeof address !== 'object')
    throw new Error('No fixture port');
  const port = address.port;
  await new Promise<void>((resolve) => server.close(() => resolve()));
  const url = `http://127.0.0.1:${port}`;
  let backend: ChildProcess | undefined;
  const start = async () => {
    backend = spawn(
      python,
      ['-m', 'musubi_gui', '--port', String(port), '--no-browser'],
      {
        env: { ...process.env, MUSUBI_GUI_STATE: path.join(fixture, 'state') },
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
  const cleanup = async () => {
    await stop();
    await rm(root, { recursive: true, force: true });
  };
  await start();
  return {
    url,
    root,
    fixture,
    media: path.join(fixture, 'media 资料'),
    start,
    stop,
    cleanup,
  };
}
