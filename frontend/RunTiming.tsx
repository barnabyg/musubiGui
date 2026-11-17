import { HelpLink } from './Help';
import { useEffect, useState } from 'react';
import type { CompletionEstimate, Progress, Run } from './runTypes';

// Elapsed time, training progress and the completion forecast for one Run.
function remainingDuration(seconds: number | null | undefined) {
  return seconds == null
    ? 'unknown'
    : `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m ${seconds % 60}s`;
}

export function RunCompletion({
  run,
  estimate,
}: {
  run: Run;
  estimate?: CompletionEstimate;
}) {
  if (!['training', 'generating'].includes(run.status)) return null;
  const seconds = estimate?.remaining_seconds;
  return (
    <div className="run-progress" aria-label="Run completion estimate">
      <p>Estimated Run remaining: {remainingDuration(seconds)}</p>
      <p>
        Estimated finish:{' '}
        {estimate?.finish_at
          ? new Date(estimate.finish_at).toLocaleString()
          : 'unknown'}
      </p>
      {seconds == null && estimate?.accounted_remaining_seconds != null && (
        <>
          <p>
            Partial remaining estimate:{' '}
            {remainingDuration(estimate.accounted_remaining_seconds)} (measured
            components only)
          </p>
          <p>
            Conditional finish for estimated components:{' '}
            {estimate.accounted_finish_at
              ? new Date(estimate.accounted_finish_at).toLocaleString()
              : 'unknown'}
          </p>
        </>
      )}
      {estimate?.preview_remaining_seconds != null && (
        <p>
          Outstanding preview budget:{' '}
          {remainingDuration(estimate.preview_remaining_seconds)}
        </p>
      )}
      {estimate && (
        <p>
          Previews: {estimate.completed_previews} completed ·{' '}
          {estimate.outstanding_previews} outstanding · Final samples
          outstanding: {estimate.outstanding_final_samples}
        </p>
      )}
      <small>
        <HelpLink topic="Time estimates" />{' '}
        {estimate?.unknown_components.length
          ? `Unestimated or assumed: ${estimate.unknown_components.join('; ')}.`
          : 'Timings vary between executions.'}
      </small>
    </div>
  );
}

function duration(from: string, to?: string) {
  if (
    !Number.isFinite(Date.parse(from)) ||
    (to && !Number.isFinite(Date.parse(to)))
  )
    return 'unknown';
  const seconds = Math.max(
    0,
    Math.round(((to ? Date.parse(to) : Date.now()) - Date.parse(from)) / 1000),
  );
  const minutes = Math.floor(seconds / 60);
  const pad = (value: number) => String(value).padStart(2, '0');
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}:${pad(minutes % 60)}:${pad(seconds % 60)}`
    : `${pad(minutes)}:${pad(seconds % 60)}`;
}

export function RunElapsed({ run }: { run: Run }) {
  const terminal = [
    'completed',
    'failed',
    'stopped',
    'cancelled',
    'interrupted',
  ].includes(run.status);
  const uncertain =
    run.execution_end_unknown ||
    ['interrupted', 'orphaned', 'uncertain'].includes(run.status);
  // Old records did not distinguish automatic preparation from a separate Prepare.
  const started =
    run.execution_started === undefined
      ? run.stages.find((stage) => stage.name === 'train' && stage.started)
          ?.started
      : run.execution_started;
  const active = Boolean(started && !terminal && !uncertain);
  const [, tick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => tick((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, [active]);
  const elapsed = !started
    ? 'not started'
    : uncertain || (terminal && !run.finished)
      ? 'unknown (execution end unconfirmed)'
      : duration(started, terminal ? run.finished : undefined);
  return (
    <p aria-label="Run elapsed">
      Run elapsed {elapsed}
      {run.execution_started === undefined && started
        ? ' (legacy: from first training start)'
        : ''}
    </p>
  );
}

export function TrainingProgress({
  run,
  progress,
}: {
  run: Run;
  progress: Progress | null;
}) {
  const training = run.stages.filter(
    (stage) => stage.name === 'train' && stage.started,
  );
  const train = training.at(-1);
  const ended =
    train?.finished ??
    (['completed', 'failed', 'stopped', 'cancelled', 'interrupted'].includes(
      run.status,
    )
      ? run.finished
      : undefined);
  const trainingActive = Boolean(train && !ended);
  const [, tick] = useState(0);
  useEffect(() => {
    if (!trainingActive) return;
    const timer = setInterval(() => tick((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, [trainingActive]);
  if (!train) return null;
  const elapsed = training[0].started
    ? duration(training[0].started, ended)
    : 'unknown';
  if (!progress)
    return (
      <div className="run-progress" aria-label="Training progress">
        <p>Training elapsed {elapsed}</p>
        <p>Step progress unknown: training has not reported a step yet.</p>
      </div>
    );
  return (
    <div className="run-progress" aria-label="Training progress">
      <p>
        <strong>
          Step {progress.step}/{progress.total}
        </strong>
      </p>
      {progress.expert_steps && (
        <p>
          Active expert: {progress.expert}-noise · Phase {progress.phase_step}/
          {progress.phase_total} · Low-noise {progress.expert_steps.low} steps ·
          High-noise {progress.expert_steps.high} steps
        </p>
      )}
      <progress
        aria-label="Training steps"
        max={progress.total}
        value={progress.step}
      />
      <p>
        Training elapsed {elapsed} ·{' '}
        {progress.eta_scope === 'phase' ? 'Phase ETA' : 'Training ETA'}{' '}
        {progress.eta ?? 'unknown'} · Loss {progress.loss ?? 'unknown'}
      </p>
      <p>
        Seconds per step:{' '}
        {progress.seconds_per_step == null
          ? 'unknown'
          : `${progress.seconds_per_step.toFixed(2)} s/step (trainer reported)`}
      </p>
      <p>
        Estimated training remaining to max steps:{' '}
        {remainingDuration(progress.estimated_remaining_seconds)}
      </p>
      <small>
        <HelpLink topic="Time estimates" />
      </small>
    </div>
  );
}
