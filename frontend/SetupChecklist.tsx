import { useEffect, useState, type MouseEvent } from 'react';
import { Circle, CircleCheck, TriangleAlert } from 'lucide-react';

const dismissedKey = 'musubi.setupChecklistDismissed';

function readDismissed() {
  try {
    return window.localStorage.getItem(dismissedKey) === 'yes';
  } catch {
    return false;
  }
}

function saveDismissed() {
  try {
    window.localStorage.setItem(dismissedKey, 'yes');
  } catch {
    // Private windows can refuse storage; the checklist just comes back next time.
  }
}

type Progress = { datasets: boolean; jobs: boolean; runs: boolean };
type RunRecord = { stages: { name: string; status: string }[] };

// A Run counts once training has actually begun, not when it was only prepared.
function startedTraining(run: RunRecord) {
  return run.stages.some(
    (stage) => stage.name === 'train' && stage.status !== 'pending',
  );
}

export function SetupChecklist({
  configured,
  connected,
  diagnosed,
  problemsFound,
}: {
  configured: boolean;
  connected: boolean;
  diagnosed: boolean;
  problemsFound: boolean;
}) {
  const [progress, setProgress] = useState<Progress>({
    datasets: false,
    jobs: false,
    runs: false,
  });
  const [dismissed, setDismissed] = useState(readDismissed);
  useEffect(() => {
    let current = true;
    const list = <T,>(url: string) =>
      fetch(url).then((response) =>
        response.ok ? (response.json() as Promise<T[]>) : [],
      );
    void Promise.all([
      list('/api/datasets'),
      list('/api/jobs'),
      list<RunRecord>('/api/runs'),
    ])
      .then(([datasets, jobs, runs]) => {
        if (current)
          setProgress({
            datasets: datasets.length > 0,
            jobs: jobs.length > 0,
            runs: runs.some(startedTraining),
          });
      })
      // The checklist is a guide; with no answer its items just stay to do.
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, []);
  function jump(id: string) {
    return (event: MouseEvent) => {
      event.preventDefault();
      document.getElementById(id)?.scrollIntoView({ block: 'center' });
    };
  }
  const items = [
    {
      label: 'Connect your musubi-tuner installation',
      done: connected,
      note: undefined,
      href: '#/settings',
      onClick: jump('settings-environment'),
    },
    {
      label: 'Check it with diagnostics',
      done: diagnosed,
      note: diagnosed && problemsFound ? 'Problems found' : undefined,
      href: '#/settings',
      onClick: jump('settings-actions'),
    },
    {
      label: 'Add a dataset',
      done: progress.datasets,
      note: undefined,
      href: '#/datasets',
    },
    {
      label: 'Create a job',
      done: progress.jobs,
      note: undefined,
      href: '#/jobs',
    },
    {
      label: 'Start a Run',
      done: progress.runs,
      note: undefined,
      href: '#/runs',
    },
  ];
  if (dismissed && configured) return null;
  const complete = items.every((item) => item.done);
  return (
    <section className="panel setup-checklist" aria-label="Setup checklist">
      <h2>Getting started</h2>
      <p>Training works in this order: Dataset, then Job, then Run.</p>
      <ol>
        {items.map(({ label, done, note, href, onClick }) => (
          <li key={label} className={note ? 'noted' : done ? 'done' : ''}>
            {note ? <TriangleAlert /> : done ? <CircleCheck /> : <Circle />}
            <a href={href} onClick={onClick}>
              {label}
            </a>
            <small>{note ?? (done ? 'Done' : 'To do')}</small>
          </li>
        ))}
      </ol>
      {complete && (
        <button
          onClick={() => {
            saveDismissed();
            setDismissed(true);
          }}
        >
          Dismiss checklist
        </button>
      )}
    </section>
  );
}
