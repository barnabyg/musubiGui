import type { Run } from './runTypes';

export function RunRecords({
  run,
  logText,
  performanceAvailable,
}: {
  run: Run;
  logText?: string;
  performanceAvailable?: boolean;
}) {
  return (
    <details className="panel" open={run.status === 'failed'}>
      <summary>
        <h2>Stage logs and diagnostics</h2>
      </summary>
      <p className="run-output-path">
        Output folder <code aria-label="Output folder">{run.directory}</code>
      </p>
      {run.job.family === 'h3' && performanceAvailable && (
        <p>
          <a href={`/api/runs/${run.id}/performance`} download>
            Download performance diagnostics
          </a>
        </p>
      )}
      {run.retention?.errors?.map((error) => (
        <p className="error" key={error}>
          Checkpoint retention: {error}
        </p>
      ))}
      {!!run.retention?.pending?.length && (
        <p>
          Retrying checkpoint cleanup for {run.retention.pending.length}{' '}
          artifacts at the next checkpoint check.
        </p>
      )}
      <pre aria-label="Run logs">{logText || 'Waiting for stage output…'}</pre>
    </details>
  );
}
