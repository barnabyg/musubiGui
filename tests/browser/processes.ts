import { execFile } from 'node:child_process';
import path from 'node:path';

export async function stopBackendTree(pid: number): Promise<void> {
  if (!Number.isSafeInteger(pid) || pid <= 0)
    throw new Error('Missing task-owned backend PID');
  const alive = () => {
    try {
      process.kill(pid, 0);
      return true;
    } catch (reason) {
      if ((reason as NodeJS.ErrnoException).code === 'ESRCH') return false;
      throw reason;
    }
  };
  if (!alive()) return;
  if (process.platform === 'win32') {
    const executable = path.join(
      process.env.SystemRoot ?? 'C:\\Windows',
      'System32',
      'taskkill.exe',
    );
    await new Promise<void>((resolve, reject) =>
      execFile(
        executable,
        ['/PID', String(pid), '/T', '/F'],
        { windowsHide: true },
        (error) => {
          if (error && alive()) reject(error);
          else resolve();
        },
      ),
    );
  } else process.kill(pid, 'SIGTERM');
  for (let attempt = 0; attempt < 50; attempt++) {
    if (!alive()) return;
    await new Promise<void>((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(
    `Task-owned backend ${pid} did not exit after process-tree termination`,
  );
}
