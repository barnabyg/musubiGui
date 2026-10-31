import { expect } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { createServer } from 'node:net';
import path from 'node:path';
import { stopBackendTree } from './processes';

export async function reservePort(): Promise<number> {
  const reservation = createServer();
  await new Promise<void>((resolve) =>
    reservation.listen(0, '127.0.0.1', resolve),
  );
  const address = reservation.address();
  if (typeof address !== 'object' || !address) throw new Error('No test port');
  await new Promise<void>((resolve) => reservation.close(() => resolve()));
  return address.port;
}

export async function localBackend(options: {
  state: string;
  readyRoute: string;
  args?: string[];
  cwd?: string;
  env?: Record<string, string>;
}) {
  const port = await reservePort();
  const url = `http://127.0.0.1:${port}`;
  const status = async () => {
    try {
      return (await fetch(url + options.readyRoute)).status;
    } catch {
      return 0;
    }
  };
  let backend: ChildProcess | undefined;
  const start = async () => {
    backend = spawn(
      path.resolve('.venv/Scripts/python.exe'),
      [
        '-m',
        'musubi_gui',
        '--port',
        String(port),
        '--no-browser',
        ...(options.args ?? []),
      ],
      {
        cwd: options.cwd,
        env: {
          ...process.env,
          ...options.env,
          MUSUBI_GUI_STATE: options.state,
        },
        stdio: 'pipe',
      },
    );
    await expect.poll(status).toBe(200);
  };
  const stop = async () => {
    const current = backend;
    backend = undefined;
    if (current && current.exitCode === null && current.signalCode === null) {
      const exited = new Promise<void>((resolve) =>
        current.once('exit', () => resolve()),
      );
      if (!current.pid) throw new Error('Missing backend PID');
      await stopBackendTree(current.pid);
      await exited;
      await expect.poll(status).toBe(0);
    }
  };
  return { url, start, stop };
}
