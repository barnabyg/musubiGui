import { formatRoute } from './route';
import { RunBadge } from './RunBadge';
import { readableTime } from './runStatus';
import { setNotificationsWanted } from './runSignals';
import type { RunSummary } from './runHistory';

// The Runs screen without a selection: the pending queue, Run history and notification opt-in.
export function RunList({
  runs,
  queue,
  selected,
  onSelect,
  moveEarlier,
  notify,
  setNotify,
}: {
  runs: RunSummary[];
  queue: RunSummary[];
  selected: string;
  onSelect?: (id?: string, replace?: boolean) => void;
  moveEarlier: (id: string) => Promise<void>;
  notify: boolean;
  setNotify: (wanted: boolean) => void;
}) {
  return (
    <div>
      {queue.length > 0 && (
        <section className="panel">
          <h2>Pending queue</h2>
          {queue.map((record, index) => (
            <p key={record.id}>
              {index + 1}. {record.job.name} <RunBadge status={record.status} />{' '}
              <button
                className="quiet"
                disabled={index === 0}
                onClick={() => void moveEarlier(record.id)}
              >
                Move earlier
              </button>
            </p>
          ))}
        </section>
      )}
      <section className="panel run-history">
        <h2>Run history</h2>
        <p>
          <a href={formatRoute({ screen: 'Jobs' })}>
            Open jobs to prepare a Run
          </a>
        </p>
        {runs.length === 0 && (
          <p>Save and review a job, then choose Prepare.</p>
        )}
        {runs.map((record) => (
          <div key={record.id}>
            <button
              aria-pressed={selected === record.id}
              onClick={() => onSelect?.(record.id)}
            >
              {record.job.name} · {record.job.family.toUpperCase()} · r
              {record.job.revision}
            </button>
            <p>
              <RunBadge status={record.status} />{' '}
              <small>{readableTime(record.created)}</small>
            </p>
          </div>
        ))}
      </section>
      <label className="job-toggle">
        <input
          type="checkbox"
          checked={notify}
          onChange={(event) =>
            void setNotificationsWanted(event.target.checked).then(setNotify)
          }
        />
        <span>Send a browser notification when a Run is ready or finished</span>
      </label>
    </div>
  );
}
