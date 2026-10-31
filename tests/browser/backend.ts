import { expect } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { createServer } from 'node:net';
import path from 'node:path';
import { stopBackendTree } from './processes';

export const python = path.resolve('.venv/Scripts/python.exe');
export const helper = path.resolve('tests/browser/helper.py');

async function reservePort(): Promise<number> {
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
  // What Python runs; the port and args follow it. Defaults to the real entry point.
  entry?: string[];
  args?: string[];
  cwd?: string;
  env?: Record<string, string>;
  readyTimeout?: number;
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
      python,
      [
        ...(options.entry ?? ['-m', 'musubi_gui', '--no-browser']),
        '--port',
        String(port),
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
    // Reading the output keeps a full pipe from stalling the backend, and leaves evidence if it dies.
    let output = '';
    const keep = (chunk: Buffer) => {
      output = (output + chunk.toString()).slice(-4000);
    };
    backend.stdout?.on('data', keep);
    backend.stderr?.on('data', keep);
    const started = backend;
    started.once('exit', (code, signal) => {
      if (backend === started)
        console.error(
          `Backend on port ${port} exited unexpectedly (code ${code}, signal ${signal}):\n${output}`,
        );
    });
    await expect.poll(status, { timeout: options.readyTimeout }).toBe(200);
  };
  const running = () =>
    backend !== undefined &&
    backend.exitCode === null &&
    backend.signalCode === null;
  // Ends the backend on purpose, so its exit isn't reported as unexpected.
  const end = async (terminate: (pid: number) => Promise<unknown>) => {
    const current = backend;
    const wasRunning = running();
    backend = undefined;
    if (!current || !wasRunning) return false;
    const exited = new Promise<void>((resolve) =>
      current.once('exit', () => resolve()),
    );
    if (!current.pid) throw new Error('Missing backend PID');
    await terminate(current.pid);
    await exited;
    return true;
  };
  const stop = async () => {
    if (await end(stopBackendTree)) await expect.poll(status).toBe(0);
  };
  return { url, start, stop, end, running };
}
