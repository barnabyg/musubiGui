import { useState } from 'react';
import { formatRoute } from './route';
import { RunBadge } from './RunBadge';
import { segmentLabel, stepRange, trainings } from './runChains';
import type { Training } from './runChains';
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
  onDelete,
  busy,
  notify,
  setNotify,
}: {
  runs: RunSummary[];
  queue: RunSummary[];
  selected: string;
  onSelect?: (id?: string, replace?: boolean) => void;
  moveEarlier: (id: string) => Promise<void>;
  onDelete: (runId: string, chain: boolean) => Promise<void>;
  busy: boolean;
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
        {trainings(runs).map((training) =>
          training.segments.length > 1 ? (
            <TrainingRow
              key={training.id}
              training={training}
              selected={selected}
              onSelect={onSelect}
              onDelete={onDelete}
              busy={busy}
            />
          ) : (
            <div key={training.id}>
              <button
                className="run-open"
                aria-pressed={selected === training.latest.id}
                onClick={() => onSelect?.(training.latest.id)}
              >
                {runName(training.latest)}
              </button>
              <p>
                <RunBadge status={training.latest.status} />{' '}
                <small>{readableTime(training.latest.created)}</small>
              </p>
            </div>
          ),
        )}
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

function runName(run: RunSummary) {
  return `${run.job.name} · ${run.job.family.toUpperCase()} · r${run.job.revision}`;
}

// One row for a training resumed in segments: it opens the latest segment, and expands to each one.
function TrainingRow({
  training,
  selected,
  onSelect,
  onDelete,
  busy,
}: {
  training: Training;
  selected: string;
  onSelect?: (id?: string, replace?: boolean) => void;
  onDelete: (runId: string, chain: boolean) => Promise<void>;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const { latest, segments } = training;
  const listId = `training-${training.id}`;
  return (
    <div
      className="run-training"
      role="group"
      aria-label={`Training ${latest.job.name}`}
    >
      <button
        className="run-open"
        aria-pressed={selected === latest.id}
        onClick={() => onSelect?.(latest.id)}
      >
        {runName(latest)}
      </button>
      <p>
        <RunBadge status={latest.status} />{' '}
        <span>{segments.length} segments</span>{' '}
        <small>{readableTime(latest.created)}</small>
      </p>
      <div className="actions">
        <button
          className="quiet"
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => setOpen(!open)}
        >
          {open ? 'Hide segments' : `Show ${segments.length} segments`}
        </button>
        <button
          className="danger"
          disabled={busy}
          onClick={() => void onDelete(latest.id, true)}
        >
          Delete training
        </button>
      </div>
      {open && (
        <ol id={listId} className="run-segments" aria-label="Segments">
          {segments.map((run) => (
            <li key={run.id}>
              <button
                className="run-open"
                aria-pressed={selected === run.id}
                onClick={() => onSelect?.(run.id)}
              >
                {segmentLabel(run)} · {stepRange(run)} · r{run.job.revision}
              </button>
              <p>
                <RunBadge status={run.status} />{' '}
                {!run.chain.on_line && <span>Side branch </span>}
                <small>{readableTime(run.created)}</small>
              </p>
              {run.chain.later_segments === 0 && (
                <button
                  className="danger"
                  disabled={busy}
                  onClick={() => void onDelete(run.id, false)}
                >
                  Delete segment
                </button>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
