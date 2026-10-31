import { execFile } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import { expect } from '@playwright/test';
import { helper, localBackend, python } from './backend';

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
  await execute(python, [
    '-m',
    'musubi_gui.acceptance',
    '--workspace',
    fixture,
    '--prepare-only',
    '--skip-library-scan',
  ]);
  const manifest = JSON.parse(
    await readFile(path.join(fixture, 'acceptance.json'), 'utf8'),
  ) as {
    state: string;
    jobs: { id: string; name: string; family: string; dataset_id: string }[];
  };
  const identityFile = path.join(parent, 'backend.json');
  const device = path.join(parent, 'device.sqlite');
  const backend = await localBackend({
    state: manifest.state,
    readyRoute: '/api/runs',
    entry: [helper, 'serve', '--identity', identityFile, '--device', device],
    env:
      options.freeBytes === undefined
        ? undefined
        : { MUSUBI_TEST_FREE_BYTES: String(options.freeBytes) },
    readyTimeout: 240_000,
  });
  const { url, start } = backend;
  const crash = async () => {
    await backend.end(() => execute(python, [helper, 'kill', identityFile]));
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
      if (!backend.running()) await start();
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
    await execute(python, [
      helper,
      'lease',
      action,
      path.join(parent, 'other-instance'),
      device,
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
