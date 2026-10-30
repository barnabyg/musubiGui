import { execFile, spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createServer } from 'node:net';
import { promisify } from 'node:util';
import { expect } from '@playwright/test';

const execute = promisify(execFile);

export type RunRecord = {
  id: string;
  status: string;
  fingerprint: string;
  error?: string;
  job: { id: string; name: string; family: string; revision: number };
  directory: string;
  manifest: { items: { caption: { text: string }; absolute_path: string }[] };
  stages: { name: string; status: string }[];
};

export async function runWorkspace(options: { freeBytes?: number } = {}) {
  const parent = await mkdtemp(path.join(tmpdir(), 'musubi-runs-'));
  const fixture = path.join(parent, 'fixture');
  const python = path.resolve('.venv/Scripts/python.exe');
  await execute(python, [
    '-m',
    'musubi_gui.acceptance',
    '--workspace',
    fixture,
    '--prepare-only',
  ]);
  const manifest = JSON.parse(
    await readFile(path.join(fixture, 'acceptance.json'), 'utf8'),
  ) as {
    state: string;
    jobs: { id: string; name: string; family: string; dataset_id: string }[];
  };
  const identityFile = path.join(parent, 'backend.json');
  const reservation = createServer();
  await new Promise<void>((resolve) =>
    reservation.listen(0, '127.0.0.1', resolve),
  );
  const address = reservation.address();
  if (!address || typeof address !== 'object')
    throw new Error('No fixture port');
  const port = address.port;
  await new Promise<void>((resolve) => reservation.close(() => resolve()));
  const url = `http://127.0.0.1:${port}`;
  let backend: ChildProcess | undefined;
  let output = '';
  const start = async () => {
    backend = spawn(
      python,
      [
        '-c',
        "import os,json,psutil;from pathlib import Path;import uvicorn;from musubi_gui.app import create_app;p=psutil.Process();Path(os.environ['MUSUBI_TEST_IDENTITY']).write_text(json.dumps({'pid':p.pid,'created':p.create_time(),'exe':p.exe()}));uvicorn.run(create_app(Path(os.environ['MUSUBI_GUI_STATE']),fixture_mode=True,device_path=Path(os.environ['MUSUBI_TEST_DEVICE'])),host='127.0.0.1',port=int(os.environ['MUSUBI_TEST_PORT']),access_log=False)",
      ],
      {
        env: {
          ...process.env,
          MUSUBI_GUI_STATE: manifest.state,
          MUSUBI_TEST_DEVICE: path.join(parent, 'device.sqlite'),
          MUSUBI_TEST_PORT: String(port),
          MUSUBI_TEST_IDENTITY: identityFile,
          ...(options.freeBytes === undefined
            ? {}
            : { MUSUBI_TEST_FREE_BYTES: String(options.freeBytes) }),
        },
        stdio: 'pipe',
      },
    );
    backend.stdout?.on('data', (chunk: Buffer) => {
      output += chunk.toString();
    });
    backend.stderr?.on('data', (chunk: Buffer) => {
      output += chunk.toString();
    });
    await expect
      .poll(
        async () => {
          try {
            return (await fetch(`${url}/api/runs`)).status;
          } catch {
            return output.includes('Traceback') ? output : 0;
          }
        },
        { timeout: 15000 },
      )
      .toBe(200);
  };
  const crash = async () => {
    if (backend && backend.exitCode === null) {
      const exited = new Promise<void>((resolve) =>
        backend!.once('exit', () => resolve()),
      );
      // Kill the recorded service, preserving its training children for recovery.
      await execute(python, [
        '-c',
        "import json,sys,psutil;from pathlib import Path;i=json.loads(Path(sys.argv[1]).read_text());p=psutil.Process(i['pid']);assert p.create_time()==i['created'] and p.exe()==i['exe'];p.kill()",
        identityFile,
      ]);
      await exited;
    }
  };
  const post = async (route: string, body: object = {}) => {
    const result = await fetch(url + route, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const value: unknown = await result.json();
    if (!result.ok) throw new Error(JSON.stringify(value));
    return value;
  };
  const records = async () =>
    (await (await fetch(`${url}/api/runs`)).json()) as RunRecord[];
  const close = async () => {
    try {
      if (!backend || backend.exitCode !== null) await start();
      for (const run of await records()) {
        if (['training', 'generating', 'stopping'].includes(run.status))
          await post(`/api/runs/${run.id}/stop`, {
            force: true,
            confirmed: true,
          });
        if (
          ['preparing', 'pending_preparation', 'pending_training'].includes(
            run.status,
          )
        )
          await post(`/api/runs/${run.id}/cancel`);
        if (['interrupted', 'orphaned', 'uncertain'].includes(run.status)) {
          const current = (await post(
            `/api/runs/${run.id}/reconcile`,
          )) as RunRecord;
          if (['orphaned', 'uncertain'].includes(current.status))
            await post(`/api/runs/${run.id}/terminate-orphan`, {
              confirmed: true,
            });
        }
      }
      await expect
        .poll(async () => {
          const active = await records();
          return active.filter((run) =>
            ['preparing', 'training', 'generating', 'stopping'].includes(
              run.status,
            ),
          ).length;
        })
        .toBe(0);
    } finally {
      await crash();
      await rm(parent, {
        recursive: true,
        force: true,
        maxRetries: 5,
        retryDelay: 150,
      });
    }
  };
  // Another instance owning the GPU leaves new work pending, whatever the fixture's speed.
  const leaseGpu = async (action: 'acquire' | 'release') => {
    const call =
      action === 'acquire'
        ? "acquire('other-work',{})"
        : "release('other-work')";
    await execute(python, [
      '-c',
      `import sys;from pathlib import Path;from musubi_gui.run_store import DeviceLease;DeviceLease(Path(sys.argv[1]),Path(sys.argv[2])).${call}`,
      path.join(parent, 'other-instance'),
      path.join(parent, 'device.sqlite'),
    ]);
  };
  await start();
  return {
    parent,
    fixture,
    manifest,
    url,
    start,
    crash,
    post,
    records,
    leaseGpu,
    close,
  };
}
