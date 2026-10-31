import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { localBackend } from './backend';

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
  const { url, start, stop } = await localBackend({
    state: path.join(fixture, 'state'),
    readyRoute: '/api/settings',
  });
  const cleanup = async () => {
    await stop();
    await rm(root, {
      recursive: true,
      force: true,
      maxRetries: 10,
      retryDelay: 100,
    });
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
