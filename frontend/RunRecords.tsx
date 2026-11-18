import type { ReactNode } from 'react';
import type { Run } from './runTypes';

// The Inputs and output folder panel and the Stage logs of one Run.
export function RunRecords({
  run,
  logText,
  performanceAvailable,
  refreshOutputs,
}: {
  run: Run;
  logText?: string;
  performanceAvailable?: boolean;
  refreshOutputs: ReactNode;
}) {
  return (
    <>
      <section className="panel" aria-label="Inputs and output folder">
        <h2>Inputs and output folder</h2>
        <details>
          <summary>
            Inputs saved with this Run ({run.manifest.items.length} included
            items)
          </summary>
          <pre>{JSON.stringify(run.manifest, null, 2)}</pre>
        </details>
        <p>Output: {run.directory}</p>
        {refreshOutputs}
        {run.job.family === 'h3' && performanceAvailable && (
          <p>
            <a href={`/api/runs/${run.id}/performance`} download>
              Download performance diagnostics
            </a>{' '}
            — timestamped GPU, memory and step measurements.
          </p>
        )}
        <details>
          <summary>Full Run ID</summary>
          <code>{run.id}</code>
        </details>
      </section>
      <details className="panel" open={run.status === 'failed'}>
        <summary>
          <h2>Stage logs</h2>
        </summary>
        <pre aria-label="Run logs">
          {logText || 'Waiting for stage output…'}
        </pre>
      </details>
    </>
  );
}
