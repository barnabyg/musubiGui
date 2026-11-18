import { useEffect, useState, type ReactNode } from 'react';
import { apiClient } from './api';
import { currentTraining, hasActiveWork } from './currentTraining';
import { formatRoute } from './route';
import { useRunHistory, type RunSummary } from './runHistory';

const read = apiClient('Setup progress could not be read.');
const dismissedKey = 'musubi.setupChecklistDismissed';

function readDismissed() {
  try {
    return window.localStorage.getItem(dismissedKey) === 'yes';
  } catch {
    return false;
  }
}

type Job = { id: string; name: string; revision: number };
type Step = {
  label: string;
  done: boolean;
  action: ReactNode;
  canRevisit?: boolean;
};

// Keep navigation on the Settings route and move keyboard focus to the actual control.
export function focusSetupControl(id: string) {
  const target = document.getElementById(id);
  if (!target) return;
  const details = target.closest('details');
  if (details) details.open = true;
  target.focus({ preventScroll: true });
  target.scrollIntoView({ block: 'center' });
}

function StartRun({ jobs, runs }: { jobs: Job[]; runs: RunSummary[] }) {
  // A prepared Run keeps its frozen inputs even after another revision runs or the job is archived.
  const startable = runs
    .filter((run) => run.status === 'prepared')
    .map((run) => ({
      href: formatRoute({ screen: 'Runs', id: run.id }),
      name: `${run.job.name} · revision ${run.job.revision} · prepared Run ${run.id.slice(0, 8)}`,
    }));
  const existing: { href: string; name: string }[] = [];
  for (const job of jobs) {
    const latest = currentTraining(runs, job.id)?.latest;
    if (latest) {
      const href = formatRoute({ screen: 'Runs', id: latest.id });
      const sameRevision = job.revision === latest.job.revision;
      const active = hasActiveWork(latest.status);
      if (active || (sameRevision && latest.status === 'completed')) {
        existing.push({
          href,
          name: active
            ? `View active Run for ${job.name}`
            : `View results for ${job.name}`,
        });
        continue;
      }
      if (latest.status === 'prepared' && sameRevision) continue;
    }
    startable.push({
      href: formatRoute({ screen: 'Jobs', id: job.id }),
      name: job.name,
    });
  }
  const choices = startable.length ? startable : existing;
  if (!choices.length) return <a href="#/jobs">Create a job to start a Run</a>;
  if (choices.length === 1)
    return (
      <a href={choices[0].href}>
        {startable.length ? 'Start a Run' : choices[0].name}
      </a>
    );
  return (
    <details className="setup-run-choices">
      <summary>
        {startable.length ? 'Start a Run' : 'View existing Runs'}
      </summary>
      <ul>
        {choices.map(({ name, href }) => (
          <li key={href}>
            <a href={href}>{name}</a>
          </li>
        ))}
      </ul>
    </details>
  );
}

export function SetupChecklist({
  installed,
  missingFolder,
  dirty,
  busy,
  setupRunning,
  diagnostics,
  firstProblem,
  diagnose,
}: {
  installed: boolean;
  missingFolder?: string;
  dirty: boolean;
  busy: boolean;
  setupRunning: boolean;
  diagnostics: 'unchecked' | 'stale' | 'blocked' | 'checked';
  firstProblem?: string;
  diagnose: () => Promise<void>;
}) {
  const [library, setLibrary] = useState<{ datasets: boolean; jobs: Job[] }>();
  const [loadError, setLoadError] = useState('');
  const [dismissed, setDismissed] = useState(readDismissed);
  const history = useRunHistory();
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      read<unknown[]>('/api/datasets', 'GET', undefined, controller.signal),
      read<Job[]>('/api/jobs', 'GET', undefined, controller.signal),
    ])
      .then(([datasets, jobs]) => {
        if (!controller.signal.aborted)
          setLibrary({ datasets: !!datasets.length, jobs });
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setLoadError(reason.message);
      });
    return () => controller.abort();
  }, []);

  function jump(label: string, id: string) {
    return (
      <button className="quiet" onClick={() => focusSetupControl(id)}>
        {label}
      </button>
    );
  }
  const folders = !missingFolder && !dirty;
  const folderAction = missingFolder
    ? jump('Configure folders', missingFolder)
    : jump('Save folder settings', 'settings-save');
  const installAction = jump('Go to installation', 'settings-install');
  function diagnosticAction() {
    if (missingFolder)
      return jump('Configure missing folders before checking', missingFolder);
    if (dirty) return jump('Save changes before checking', 'settings-save');
    if (!installed)
      return jump('Install the trainer before checking', 'settings-install');
    if (diagnostics === 'blocked')
      return jump('Review failed checks', 'diagnostics-problem');
    return (
      <button disabled={busy || setupRunning} onClick={() => void diagnose()}>
        {busy
          ? 'Working…'
          : diagnostics === 'stale'
            ? 'Check setup again'
            : 'Check setup now'}
      </button>
    );
  }
  const steps: Step[] = [
    {
      label: 'Install the supported Musubi version',
      done: installed,
      action: dirty
        ? jump('Save before installation', 'settings-save')
        : installAction,
    },
    {
      label: 'Configure storage and model folders',
      done: folders,
      action: folderAction,
    },
    {
      label:
        diagnostics === 'blocked'
          ? `Fix diagnostic problems${firstProblem ? ` · ${firstProblem}` : ''}`
          : diagnostics === 'stale'
            ? 'Refresh out-of-date diagnostics'
            : 'Check the installation',
      done: diagnostics === 'checked',
      action: diagnosticAction(),
    },
  ];
  if (library && history.runs) {
    steps.push(
      {
        label: 'Add a dataset',
        done: library.datasets,
        action: <a href="#/datasets">Add a dataset</a>,
      },
      {
        label: 'Create a job',
        done: library.jobs.length > 0,
        action: <a href="#/jobs">Create a job</a>,
      },
      {
        label: 'Start a Run',
        canRevisit: true,
        done: history.runs.some((run) =>
          run.stages.some(
            (stage) => stage.name === 'train' && stage.status !== 'pending',
          ),
        ),
        action: <StartRun jobs={library.jobs} runs={history.runs} />,
      },
    );
  }
  const pending = steps.filter((step) => !step.done);
  const done = steps.filter((step) => step.done);
  const complete = !!library && !!history.runs && !pending.length;
  if (dismissed && complete) return null;
  return (
    <section className="panel setup-checklist" aria-label="Setup checklist">
      <h2>
        {pending.length
          ? 'Next step'
          : complete
            ? 'Setup complete'
            : 'Getting started'}
      </h2>
      {pending.length > 0 && (
        <>
          <p>
            <strong>{pending[0].label}</strong>
          </p>
          {pending[0].action}
          {pending.length > 1 && (
            <details>
              <summary>Other steps ({pending.length - 1})</summary>
              <ol>
                {pending.slice(1).map((step) => (
                  <li key={step.label}>{step.action}</li>
                ))}
              </ol>
            </details>
          )}
        </>
      )}
      {done.length > 0 && (
        <details>
          <summary>Completed steps ({done.length})</summary>
          <ul>
            {done.map((step) => (
              <li key={step.label}>
                {step.canRevisit ? step.action : step.label}
              </li>
            ))}
          </ul>
        </details>
      )}
      {(loadError || history.error) && (
        <p role="alert" className="error">
          {loadError || history.error}
        </p>
      )}
      {complete && (
        <button
          onClick={() => {
            setDismissed(true);
            try {
              window.localStorage.setItem(dismissedKey, 'yes');
            } catch {
              /* Private windows can refuse storage. */
            }
          }}
        >
          Dismiss checklist
        </button>
      )}
    </section>
  );
}
