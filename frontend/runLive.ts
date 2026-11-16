import { useEffect, useEffectEvent, useState } from 'react';
import { apiClient } from './api';
import type { CompletionEstimate, Progress, Run } from './runTypes';

const request = apiClient('Run request failed.');

// Statuses whose log and progress keep changing, so they are read every second.
const LIVE = [
  'preparing',
  'training',
  'generating',
  'stopping',
  'orphaned',
  'uncertain',
];

export type RunLive = {
  runId: string;
  text: string;
  progress: Progress | null;
  performance_available?: boolean;
  run_estimate?: CompletionEstimate;
};

// A Run's full record, read again whenever its history summary changes (`changeKey`) or
// `refresh` does. Run details and the Job page both read a Run this way, so they agree.
export function useRunDetail(
  runId: string | undefined,
  changeKey: string | undefined,
  refresh = 0,
  onMissing?: () => void,
) {
  const [detail, setDetail] = useState<Run>();
  const [failure, setFailure] = useState<{ runId: string; message: string }>();
  // Deleted, perhaps from another tab, or never there.
  const missing = useEffectEvent(() => onMissing?.());
  useEffect(() => {
    const controller = new AbortController();
    if (!runId) return;
    let timer: number;
    async function read() {
      try {
        const record = await request<Run>(
          `/api/runs/${runId}`,
          'GET',
          undefined,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setDetail(record);
        setFailure(undefined);
      } catch (reason) {
        if (controller.signal.aborted) return;
        const message = (reason as Error).message;
        if (message === 'Run not found.') missing();
        else {
          setFailure({ runId: runId!, message });
          timer = window.setTimeout(() => void read(), 1000);
        }
      }
    }
    void read();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [runId, changeKey, refresh]);
  return {
    run: detail?.id === runId ? detail : undefined,
    error: failure && failure.runId === runId ? failure.message : '',
  };
}

// A Run's log, step progress and completion forecast: every second while it is live, once otherwise.
export function useRunLogs(
  runId: string | undefined,
  status: string | undefined,
) {
  const [live, setLive] = useState<RunLive | null>(null);
  const [failure, setFailure] = useState<{ runId: string; message: string }>();
  useEffect(() => {
    if (!runId || !status || status === 'deleting') return;
    const controller = new AbortController();
    let timer: number;
    const active = LIVE.includes(status);
    async function read() {
      let succeeded = false;
      try {
        const result = await request<Omit<RunLive, 'runId'>>(
          `/api/runs/${runId}/logs`,
          'GET',
          undefined,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setLive({ runId: runId!, ...result });
          setFailure(undefined);
          succeeded = true;
        }
      } catch (reason) {
        if (!controller.signal.aborted)
          setFailure({ runId: runId!, message: (reason as Error).message });
      } finally {
        if ((active || !succeeded) && !controller.signal.aborted)
          timer = window.setTimeout(() => void read(), 1000);
      }
    }
    void read();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [runId, status]);
  return {
    live: live?.runId === runId ? live : null,
    error: failure && failure.runId === runId ? failure.message : '',
  };
}
