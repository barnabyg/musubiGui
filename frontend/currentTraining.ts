import { useEffect, useState } from 'react';
import { apiClient } from './api';
import { trainings, type Training } from './runChains';
import { useRunHistory } from './runHistory';

// The backend's view of a Job's current training (musubi_gui/resume_compat.py `job_training`).
export type JobTrainingInfo = {
  // The latest segment of the Job's current training.
  run_id: string | null;
  chain_id: string | null;
  status: string | null;
  // What Start resumes: the ended training's latest saved state. Its step comes from the state
  // folder's name until the states are inspected, and is unknown for paired Wan states.
  resume: {
    run_id: string;
    run_status: string;
    saved_step: number | null;
  } | null;
  // Fields a resumed segment takes at any value, and those it takes only at the listed values;
  // editing any other field needs a new Run.
  resumable_fields: string[];
  resumable_values: Record<string, unknown[]>;
};

export type Resumable = Pick<
  JobTrainingInfo,
  'resumable_fields' | 'resumable_values'
>;

const request = apiClient('The Job’s training could not be read.');

// The Job's current training: of its Runs, the training whose latest segment was created most
// recently. Live from Run history, so it follows a new segment or a fresh start at once.
export function currentTraining(
  runs: Parameters<typeof trainings>[0],
  jobId: string | undefined,
): Training | undefined {
  if (!jobId) return undefined;
  const own = runs.filter(
    (run) => run.job.id === jobId && run.status !== 'deleting',
  );
  return trainings(own).reduce<Training | undefined>(
    (newest, training) =>
      !newest || training.latest.created > newest.latest.created
        ? training
        : newest,
    undefined,
  );
}

export function hasEndedTraining(status: string | null | undefined) {
  return [
    'completed',
    'stopped',
    'failed',
    'cancelled',
    'interrupted',
  ].includes(status ?? '');
}

export function hasActiveWork(status: string | null | undefined) {
  return [
    'pending_preparation',
    'preparing',
    'pending_training',
    'training',
    'generating',
    'stopping',
    'orphaned',
    'uncertain',
  ].includes(status ?? '');
}

export function useCurrentTraining(jobId: string | undefined) {
  const history = useRunHistory();
  const training = currentTraining(history.runs ?? [], jobId);
  const latest = training?.latest;
  const [info, setInfo] = useState<{
    key: string;
    value: JobTrainingInfo;
  }>();
  const [refresh, setRefresh] = useState(0);
  // Read again when the latest segment changes or ends: that is when what Start resumes changes.
  const key = `${jobId}:${latest?.id}:${latest?.status}`;
  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController();
    request<JobTrainingInfo>(
      `/api/jobs/${jobId}/training`,
      'GET',
      undefined,
      controller.signal,
    )
      .then((value) => {
        if (!controller.signal.aborted) setInfo({ key, value });
      })
      .catch(() => {
        // The banner and hints wait for the next change; Start reads the training itself.
      });
    return () => controller.abort();
  }, [jobId, key, refresh]);
  const current = info?.key === key ? info.value : undefined;
  return {
    training,
    latest,
    loaded: history.runs !== null,
    info: current,
    // A resumable training: Start resumes it when the revision allows.
    resume: current?.resume ?? null,
    reload: () => setRefresh((value) => value + 1),
  };
}

export type CurrentTraining = ReturnType<typeof useCurrentTraining>;
