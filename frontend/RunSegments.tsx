import { formatRoute } from './route';
import { RunBadge } from './RunBadge';
import { segmentLabel, stepRange } from './runChains';
import type { RunSummary } from './runHistory';
import type { Progress } from './runTypes';

// Run details for a training resumed in segments: where it has got to, and each segment's steps,
// status and Job revision, linking to that segment's own details.
export function RunSegments({
  runId,
  segments,
  progress,
}: {
  runId: string;
  segments: RunSummary[];
  progress: Progress | null;
}) {
  const latestId = segments[0].chain.latest;
  const latest = segments.find((run) => run.id === latestId) ?? segments[0];
  const { start, target, end } = latest.chain.steps;
  // Live progress already counts global steps; otherwise the latest segment's last known step.
  const reached =
    runId === latest.id && progress ? progress.step : (end ?? start);
  const missing = segments[0].chain.missing_source;
  return (
    <section className="panel" aria-label="Segments">
      <h2>Segments</h2>
      <p>
        This training was resumed {segments.length - 1} time
        {segments.length > 2 ? 's' : ''}. Steps count across all its segments,
        and its Samples and Checkpoints list every segment’s files in step
        order.
      </p>
      <div className="run-progress" aria-label="Progress across segments">
        <p>
          Step {reached} of {target} across {segments.length} segments ·{' '}
          <RunBadge status={latest.status} />
        </p>
        <progress max={Math.max(target, 1)} value={Math.min(reached, target)} />
      </div>
      <ol className="segment-strip">
        {segments.map((run) => (
          <li key={run.id} aria-current={run.id === runId}>
            <a href={formatRoute({ screen: 'Runs', id: run.id })}>
              {segmentLabel(run)}
            </a>
            <small>{stepRange(run)}</small>
            <small>
              <RunBadge status={run.status} /> · r{run.job.revision}
              {!run.chain.on_line && ' · side branch'}
            </small>
          </li>
        ))}
      </ol>
      {missing && (
        <p>
          The Run this training started from was deleted, so steps before{' '}
          {segments[0].chain.steps.start} have no files here.
        </p>
      )}
      {segments.some((run) => !run.chain.on_line) && (
        <p>
          A side branch resumed the same saved step again. The training follows
          the most recently created segment; a side branch keeps its own files.
        </p>
      )}
    </section>
  );
}
