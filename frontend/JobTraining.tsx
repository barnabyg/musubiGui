import { useState } from 'react';
import { apiClient } from './api';
import type { CurrentTraining } from './currentTraining';
import { LatestOutputs, useRunOutputs } from './Outputs';
import { formatRoute } from './route';
import { RunBadge } from './RunBadge';
import { RunControls } from './RunControls';
import { refreshRunHistory } from './runHistory';
import { useRunDetail, useRunLogs } from './runLive';
import { runStatus } from './runStatus';
import { TrainingProgress } from './RunTiming';
import type { Run } from './runTypes';

const request = apiClient('Run request failed.');
const ENDED = ['completed', 'stopped', 'failed', 'cancelled', 'interrupted'];
const CONTROLLED = ['training', 'generating', 'stopping'];

// Where the training reached and where Start would resume it from.
export function ResumeBanner({
  current,
  reached,
}: {
  current: CurrentTraining;
  reached: number | undefined;
}) {
  const { resume, latest } = current;
  if (!resume || !latest) return null;
  const segment = resume.run_id !== latest.id;
  return (
    <div className="resume-banner" role="group" aria-label="Resumable training">
      <p>
        <strong>
          Training {runStatus(latest.status).label.toLowerCase()}
          {reached === undefined ? '' : ` at step ${reached}`}.
        </strong>{' '}
        {resume.saved_step === null
          ? 'Its saved states haven’t been checked yet.'
          : `Its last state was saved at step ${resume.saved_step}${segment ? `, in an earlier segment (Run ${resume.run_id.slice(0, 8)})` : ''}.`}
      </p>
      <p>
        Prepare and start resumes from that save with this Job’s reviewed
        revision, after any edits to the sample prompt, steps and other fields a
        resumed training can take. Fields marked “needs a new Run” start a fresh
        training instead, after you confirm.
      </p>
    </div>
  );
}

// The Job page's view of its current training: status, step progress, the controls Run details
// offers while it trains, and its newest preview and checkpoint.
export function JobTraining({ current }: { current: CurrentTraining }) {
  const { training, latest } = current;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const detail = useRunDetail(latest?.id, latest?.change_key, refresh);
  const run: Run | undefined = detail.run;
  const logs = useRunLogs(run?.id, run?.status);
  const progress = logs.live?.progress ?? null;
  const segments = training?.segments ?? [];
  const outputs = useRunOutputs(
    latest?.id,
    latest
      ? [
          latest.status,
          ...latest.stages.map((stage) => stage.status),
          ...segments.map((segment) => `${segment.id}:${segment.status}`),
        ].join()
      : '',
    true,
  );
  if (!latest) return null;
  async function action(name: string, body: object = {}) {
    if (!run) return;
    setBusy(true);
    setError('');
    try {
      await request<Run>(`/api/runs/${run.id}/${name}`, 'POST', body);
      await refreshRunHistory();
      setRefresh((value) => value + 1);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const ended = ENDED.includes(latest.status);
  // Where the latest segment got to: its live or last reported step, else its recorded end.
  const reached = ended
    ? (progress?.step ?? latest.chain.steps.end ?? undefined)
    : undefined;
  return (
    <section className="panel job-training" aria-label="Current training">
      <h2>Current training</h2>
      <p>
        State: <RunBadge status={run?.status ?? latest.status} />
      </p>
      <p>
        {segments.length > 1 &&
          `Segment ${latest.chain.position} of ${segments.length} · `}
        Revision {latest.job.revision} ·{' '}
        <a href={formatRoute({ screen: 'Runs', id: latest.id })}>
          Open Run details
        </a>
      </p>
      {run?.error && <p className="error">{run.error}</p>}
      {(error || detail.error || logs.error) && (
        <p role="alert" className="error">
          {error || detail.error || logs.error}
        </p>
      )}
      <ResumeBanner current={current} reached={reached} />
      {run && <TrainingProgress run={run} progress={progress} />}
      {run && CONTROLLED.includes(run.status) && (
        <RunControls
          run={run}
          progress={progress}
          busy={busy}
          action={action}
        />
      )}
      <LatestOutputs outputs={outputs} />
    </section>
  );
}
