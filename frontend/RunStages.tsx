import type { Run } from './runTypes';

type StepState = 'pending' | 'current' | 'completed' | 'failed' | 'stopped';
const stepLabels: Record<StepState, string> = {
  pending: 'Not started',
  current: 'In progress',
  completed: 'Done',
  failed: 'Failed',
  stopped: 'Stopped',
};

// Groups the stages into what the user is waiting for. A Run can fail outside any stage, such as
// when frozen inputs changed, so that failure lands on the first step that did not finish.
function runSteps(run: Run) {
  const groups = [
    { label: 'Caching', names: ['latent_cache', 'text_cache'] },
    { label: 'Training', names: ['train'] },
    { label: 'Sample', names: ['generate'] },
  ];
  const steps = groups.flatMap(({ label, names }) => {
    const stages = run.stages.filter((stage) => names.includes(stage.name));
    if (stages.length === 0) return [];
    const has = (...values: string[]) =>
      stages.some((stage) => values.includes(stage.status));
    const state: StepState = has('failed')
      ? 'failed'
      : has('stopped', 'interrupted')
        ? 'stopped'
        : has('running')
          ? 'current'
          : stages.every((stage) =>
                ['completed', 'reused'].includes(stage.status),
              )
            ? 'completed'
            : 'pending';
    const failed =
      state === 'failed' && stages.find((stage) => stage.exit_code);
    const detail = failed
      ? `Exit code ${failed.exit_code}`
      : stages.some((stage) => stage.reused_from)
        ? 'verified cache reused'
        : stepLabels[state];
    return [{ label, state, detail }];
  });
  if (
    run.status === 'failed' &&
    !steps.some((step) => step.state === 'failed')
  ) {
    const step =
      steps.find((item) => item.state !== 'completed') ?? steps.at(-1);
    if (step) Object.assign(step, { state: 'failed', detail: 'Failed' });
  }
  const done: StepState = run.status === 'completed' ? 'completed' : 'pending';
  return [...steps, { label: 'Done', state: done, detail: stepLabels[done] }];
}

// What the Run is waiting for, as one step per group of stages.
export function RunStages({ run }: { run: Run }) {
  if (run.stages.length === 0) return null;
  return (
    <ol className="run-stepper" aria-label="Run stages">
      {runSteps(run).map((step) => (
        <li
          key={step.label}
          className={step.state}
          aria-current={step.state === 'current' ? 'step' : undefined}
        >
          <strong>{step.label}</strong>
          <small>{step.detail}</small>
        </li>
      ))}
    </ol>
  );
}

// Whether each cache was rebuilt or reused, and what checkpoint retention pruned.
export function RunCaching({ run }: { run: Run }) {
  return (
    <section className="panel" aria-label="Cache preparation results">
      <h2>Caching</h2>
      <ul>
        {run.stages
          .filter((stage) =>
            ['latent_cache', 'text_cache'].includes(stage.name),
          )
          .map((stage) => (
            <li key={stage.name}>
              {stage.name === 'latent_cache' ? 'Latents' : 'Text embeddings'}:{' '}
              {stage.status === 'reused'
                ? 'Verified compatible cache reused'
                : stage.status === 'completed'
                  ? 'Cache rebuilt'
                  : stage.status}
              {stage.cache_reason && <small> · {stage.cache_reason}</small>}
            </li>
          ))}
      </ul>
      {run.retention && (
        <section aria-label="Checkpoint retention">
          <p>
            Checkpoint retention: {run.retention.removed?.length ?? 0} artifacts
            pruned; {run.retention.protected?.length ?? 0} protected artifacts
            retained beyond the limit.
          </p>
          {run.retention.errors?.map((error) => (
            <p className="error" key={error}>
              {error}
            </p>
          ))}
          {!!run.retention.pending?.length && (
            <p>
              Pruning will retry for {run.retention.pending.length} artifacts at
              the next checkpoint check.
            </p>
          )}
        </section>
      )}
    </section>
  );
}
