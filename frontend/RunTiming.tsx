import { useEffect, useState } from 'react';
import type { CompletionEstimate, Progress, Run } from './runTypes';
import './runs.css';

const ended = ['completed', 'failed', 'stopped', 'cancelled', 'interrupted'];

function remainingDuration(seconds: number | null | undefined) {
  return seconds == null
    ? 'Unknown'
    : `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m ${seconds % 60}s`;
}

function duration(from: string, now: number, to?: string) {
  const seconds = Math.max(
    0,
    Math.round(((to ? Date.parse(to) : now) - Date.parse(from)) / 1000),
  );
  if (!Number.isFinite(seconds)) return 'Unknown';
  const minutes = Math.floor(seconds / 60);
  const pad = (value: number) => String(value).padStart(2, '0');
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}:${pad(minutes % 60)}:${pad(seconds % 60)}`
    : `${pad(minutes)}:${pad(seconds % 60)}`;
}

type TimingRow = [label: string, value: string];

function TimingRows({ rows, label }: { rows: TimingRow[]; label: string }) {
  if (!rows.length) return null;
  return (
    <dl className="run-timing" aria-label={label}>
      {rows.map(([name, value]) => (
        <div key={name} role="group" aria-label={name}>
          <dt>{name}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function useElapsed(run: Run) {
  const terminal = ended.includes(run.status);
  const uncertain =
    run.execution_end_unknown ||
    ['interrupted', 'orphaned', 'uncertain'].includes(run.status);
  const training = run.stages.filter(
    (stage) => stage.name === 'train' && stage.started,
  );
  const firstTrain = training[0]?.started;
  const lastTrain = training.at(-1)?.finished;
  // Older records measure elapsed time from the first training stage.
  const legacy = run.execution_started === undefined;
  const started = legacy ? firstTrain : run.execution_started;
  const active = Boolean((started || firstTrain) && !terminal && !uncertain);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  const unknownEnd = 'Unknown (execution end unconfirmed)';
  return {
    run: !started
      ? 'Not started'
      : uncertain || (terminal && !run.finished)
        ? unknownEnd
        : duration(started, now, terminal ? run.finished : undefined) +
          (legacy ? ' (from first training start)' : ''),
    training: !firstTrain
      ? undefined
      : lastTrain
        ? duration(firstTrain, now, lastTrain)
        : uncertain || (terminal && !run.finished)
          ? unknownEnd
          : duration(firstTrain, now, terminal ? run.finished : undefined),
  };
}

function trainingRows(
  run: Run,
  progress: Progress | null,
  elapsed?: string,
): TimingRow[] {
  const rows: TimingRow[] = [];
  if (elapsed) rows.push(['Training elapsed', elapsed]);
  if (!progress) return rows;
  rows.push([
    'Seconds per step',
    progress.seconds_per_step == null
      ? 'Unknown'
      : `${progress.seconds_per_step.toFixed(2)} s/step`,
  ]);
  if (run.status === 'training') {
    rows.push(
      [
        progress.eta_scope === 'phase' ? 'Phase ETA' : 'Training ETA',
        progress.eta ?? 'Unknown',
      ],
      [
        'Estimated training remaining',
        remainingDuration(progress.estimated_remaining_seconds),
      ],
    );
  }
  return rows;
}

export function RunTiming({
  run,
  progress,
  estimate,
}: {
  run: Run;
  progress: Progress | null;
  estimate?: CompletionEstimate;
}) {
  const elapsed = useElapsed(run);
  const rows: TimingRow[] = [
    ['Run elapsed', elapsed.run],
    ...trainingRows(run, progress, elapsed.training),
  ];
  if (['training', 'generating'].includes(run.status)) {
    const finish = (at: string | null | undefined) =>
      at ? new Date(at).toLocaleString() : 'Unknown';
    const partial =
      estimate?.remaining_seconds == null &&
      estimate?.accounted_remaining_seconds != null;
    rows.push(
      [
        'Estimated Run remaining',
        partial
          ? `${remainingDuration(estimate.accounted_remaining_seconds)} (partial)`
          : remainingDuration(estimate?.remaining_seconds),
      ],
      [
        'Estimated finish',
        partial && estimate.accounted_finish_at
          ? `${finish(estimate.accounted_finish_at)} (partial)`
          : finish(estimate?.finish_at),
      ],
    );
    if (estimate?.preview_remaining_seconds != null)
      rows.push([
        'Outstanding preview budget',
        remainingDuration(estimate.preview_remaining_seconds),
      ]);
  }
  return <TimingRows rows={rows} label="Run timing" />;
}

export function TrainingTiming({
  run,
  progress,
}: {
  run: Run;
  progress: Progress | null;
}) {
  const elapsed = useElapsed(run);
  return (
    <TimingRows
      rows={trainingRows(run, progress, elapsed.training)}
      label="Training timing"
    />
  );
}

export function TrainingProgress({
  run,
  progress,
}: {
  run: Run;
  progress: Progress | null;
}) {
  if (
    !progress &&
    !run.stages.some((stage) => stage.name === 'train' && stage.started)
  )
    return null;
  if (!progress)
    return (
      <div className="run-progress" aria-label="Training progress">
        <p>Waiting for the first training step.</p>
      </div>
    );
  const loss = `Loss ${progress.loss ?? 'unknown'}`;
  return (
    <div className="run-progress" aria-label="Training progress">
      <p>
        <strong>
          Step {progress.step}/{progress.total}
        </strong>
        {!progress.expert_steps && ` · ${loss}`}
      </p>
      {progress.expert_steps && (
        <p>
          Active expert: {progress.expert}-noise · Phase {progress.phase_step}/
          {progress.phase_total} · {loss} · Low-noise{' '}
          {progress.expert_steps.low} steps · High-noise{' '}
          {progress.expert_steps.high} steps
        </p>
      )}
      <progress
        aria-label="Training steps"
        max={progress.total}
        value={progress.step}
      />
    </div>
  );
}
