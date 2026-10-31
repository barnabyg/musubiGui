import {
  useCallback,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
} from 'react';
import { createRoot } from 'react-dom/client';
import {
  ClipboardList,
  Hexagon,
  Images,
  Play,
  Settings as SettingsIcon,
  type LucideIcon,
} from 'lucide-react';
import { apiClient } from './api';
import './style.css';
import { ConfirmDialogHost, confirmDialog } from './ConfirmDialog';
import { Jobs } from './Jobs';
import { Runs } from './Runs';
import { Datasets } from './Datasets';
import { componentLabels } from './labels';
import { PathField, RootsField } from './PathField';
import {
  formatRoute,
  namesScreen,
  parseRoute,
  screens,
  type Screen,
} from './route';
import { useRunSignals } from './runSignals';
import { SetupChecklist } from './SetupChecklist';
import { StatusBadge, type Tone } from './StatusBadge';

type Settings = {
  version: number;
  checkout: string;
  python: string;
  model_roots: string[];
  dataset_roots: string[];
  models: Record<string, string>;
  output_root: string;
  cache_root: string;
};
type RootKey = 'model_roots' | 'dataset_roots';
type Report = {
  ran_at: string;
  current: boolean;
  checks: { name: string; status: string; message: string }[];
  environment: Record<string, unknown>;
};

const checkBadges: Record<string, { tone: Tone; label: string }> = {
  ok: { tone: 'success', label: 'Passed' },
  warning: { tone: 'warning', label: 'Warning' },
  error: { tone: 'failure', label: 'Failed' },
};

const screenIcons: Record<Screen, LucideIcon> = {
  Datasets: Images,
  Jobs: ClipboardList,
  Runs: Play,
  Settings: SettingsIcon,
};

const activeStatuses = [
  'pending_preparation',
  'preparing',
  'pending_training',
  'training',
  'generating',
  'stopping',
];

function setupComplete(value: Settings) {
  return !!(
    value.checkout &&
    value.python &&
    value.output_root &&
    value.cache_root &&
    value.dataset_roots.length
  );
}

function setupHealth(report: Report | null, dirty: boolean) {
  if (!report) return { state: 'unknown', label: 'Not checked' };
  const problems = report.checks.filter((check) => check.status === 'error');
  const outdated = dirty || !report.current;
  if (problems.length)
    return {
      state: 'problems',
      label: `Problems found${outdated ? ' · out of date' : ''}`,
    };
  if (outdated) return { state: 'unknown', label: 'Out of date' };
  // A warning can mean training can't run yet, such as no model paths, so it isn't Ready.
  const warnings = report.checks.filter((check) => check.status !== 'ok');
  return warnings.length
    ? { state: 'warnings', label: `${warnings.length} to check` }
    : { state: 'ready', label: 'Ready' };
}

// Root textareas keep raw text while editing so Enter can start an empty line.
function rootLines(text: string) {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

function confirmLeaveJob() {
  return confirmDialog({
    title: 'Leave this job?',
    body: <p>Your unsaved changes to this job will be discarded.</p>,
    confirmLabel: 'Discard changes',
    cancelLabel: 'Keep editing',
    danger: true,
  });
}

const api = apiClient(
  (status) => `Request failed (${status}). Check the field values.`,
);

function App() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [rootText, setRootText] = useState<Record<RootKey, string>>({
    model_roots: '',
    dataset_roots: '',
  });
  const [status, setStatus] = useState('Loading settings…');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const [initialRoute] = useState(() => parseRoute(window.location.hash));
  // A bare address waits for the settings to choose its screen, rather than flashing the wrong one.
  const [landing, setLanding] = useState(
    () => !namesScreen(window.location.hash),
  );
  const landingPending = useRef(landing);
  const [screen, setScreen] = useState<Screen>(initialRoute.screen);
  // Each screen's selection, kept while elsewhere so that returning reopens it.
  const [selection, setSelection] = useState<Partial<Record<Screen, string>>>(
    initialRoute.id ? { [initialRoute.screen]: initialRoute.id } : {},
  );
  // Set before the browser reports the change, so only history moves look external.
  const currentHash = useRef(landing ? '' : formatRoute(initialRoute));
  const currentScreen = useRef(initialRoute.screen);
  const leaving = useRef(false);
  // Where this page sits in the tab's history, stored on each entry so that a move back or
  // forward can be undone exactly. It survives a reload along with the entry.
  const [initialPosition] = useState(() => {
    const stored = (window.history.state as { position?: number } | null)
      ?.position;
    window.history.replaceState({ position: stored ?? 0 }, '');
    return stored ?? 0;
  });
  const position = useRef(initialPosition);
  const [jobDirty, setJobDirty] = useState(false);
  const [datasetEditor, setDatasetEditor] = useState(false);
  useRunSignals(screen);
  const go = useCallback((target: Screen, id?: string, replace = false) => {
    currentScreen.current = target;
    setScreen(target);
    setSelection((value) => ({ ...value, [target]: id }));
    const hash = formatRoute({ screen: target, id });
    if (hash === currentHash.current) return;
    currentHash.current = hash;
    if (replace) {
      window.history.replaceState({ position: position.current }, '', hash);
    } else {
      window.location.hash = hash;
      position.current += 1;
      window.history.replaceState({ position: position.current }, '');
    }
  }, []);
  // A screen's work can finish after the user has moved on: it updates that screen's selection
  // without taking them back there.
  const selectOn = useCallback(
    (target: Screen, id?: string, replace?: boolean) => {
      if (currentScreen.current === target) go(target, id, replace);
      else setSelection((value) => ({ ...value, [target]: id }));
    },
    [go],
  );
  const selectDataset = useCallback(
    (id?: string, replace?: boolean) => selectOn('Datasets', id, replace),
    [selectOn],
  );
  const selectJob = useCallback(
    (id?: string, replace?: boolean) => selectOn('Jobs', id, replace),
    [selectOn],
  );
  const selectRun = useCallback(
    (id?: string, replace?: boolean) => selectOn('Runs', id, replace),
    [selectOn],
  );
  const openRun = useCallback((id: string) => go('Runs', id), [go]);
  async function navigate(name: Screen) {
    landingPending.current = false;
    setLanding(false);
    if (screen === 'Jobs' && jobDirty && !(await confirmLeaveJob())) return;
    go(name, selection[name]);
  }
  const followHistory = useEffectEvent(async () => {
    if (window.location.hash === currentHash.current) return;
    landingPending.current = false;
    setLanding(false);
    const next = parseRoute(window.location.hash);
    const state = window.history.state as { position?: number } | null;
    // An address typed by hand is a new entry, with no position recorded yet.
    const arrival = state?.position ?? position.current + 1;
    const moved = arrival - position.current;
    if (state?.position === undefined)
      window.history.replaceState({ position: arrival }, '');
    if (screen === 'Jobs' && jobDirty && !leaving.current) {
      // The browser has already moved. Step back to this job while the question is open, which
      // leaves the rest of the history as it was.
      window.history.go(-moved);
      if (!(await confirmLeaveJob())) return;
      leaving.current = true;
      setJobDirty(false);
      window.history.go(moved);
      return;
    }
    leaving.current = false;
    position.current = arrival;
    currentHash.current = window.location.hash;
    currentScreen.current = next.screen;
    setScreen(next.screen);
    setSelection((value) => ({ ...value, [next.screen]: next.id }));
  });
  useEffect(() => {
    const listener = () => void followHistory();
    window.addEventListener('hashchange', listener);
    return () => window.removeEventListener('hashchange', listener);
  }, []);
  function load(value: Settings) {
    setSettings(value);
    setRootText({
      model_roots: value.model_roots.join('\n'),
      dataset_roots: value.dataset_roots.join('\n'),
    });
  }
  useEffect(() => {
    void Promise.all([
      api<Settings>('/api/settings'),
      api<Report | null>('/api/diagnostics'),
    ])
      .then(async ([value, latest]) => {
        load(value);
        setReport(latest);
        setStatus('Settings loaded');
        if (!landingPending.current) return;
        const runs = await api<{ id: string; status: string }[]>(
          '/api/runs',
        ).catch(() => []);
        if (!landingPending.current) return;
        const active = runs.find((run) => activeStatuses.includes(run.status));
        if (!setupComplete(value)) go('Settings', undefined, true);
        else if (active) go('Runs', active.id, true);
        else go('Datasets', undefined, true);
      })
      .catch((reason: Error) => {
        setError(reason.message);
        setStatus('Settings could not be loaded');
      })
      .finally(() => {
        landingPending.current = false;
        setLanding(false);
      });
  }, [go]);
  useEffect(() => {
    const protect = (event: BeforeUnloadEvent) => {
      if (dirty) event.preventDefault();
    };
    window.addEventListener('beforeunload', protect);
    return () => window.removeEventListener('beforeunload', protect);
  }, [dirty]);
  function changed() {
    setDirty(true);
    setStatus('Unsaved changes');
  }
  function update(next: Settings) {
    setSettings(next);
    changed();
  }
  async function save() {
    if (!settings) return;
    setBusy(true);
    setError('');
    try {
      load(
        await api<Settings>('/api/settings', 'PUT', {
          ...settings,
          model_roots: rootLines(rootText.model_roots),
          dataset_roots: rootLines(rootText.dataset_roots),
        }),
      );
      setDirty(false);
      setReport(await api<Report | null>('/api/diagnostics'));
      setStatus('Settings saved');
    } catch (reason) {
      setError((reason as Error).message);
      setStatus('Save failed — edits retained');
    } finally {
      setBusy(false);
    }
  }
  async function diagnose() {
    setBusy(true);
    setError('');
    try {
      setReport(await api<Report>('/api/diagnostics', 'POST', {}));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function showReport() {
    await navigate('Settings');
    window.requestAnimationFrame(() =>
      document.getElementById('diagnostics-report')?.scrollIntoView(),
    );
  }
  const health = setupHealth(report, dirty);
  function field(
    label: string,
    key: 'checkout' | 'python' | 'output_root' | 'cache_root',
    help: string,
    kind: 'folder' | 'file' = 'folder',
  ) {
    return (
      <PathField
        label={label}
        kind={kind}
        value={settings?.[key] ?? ''}
        disabled={busy}
        placeholder="C:\…"
        help={help}
        onChange={(value) => settings && update({ ...settings, [key]: value })}
      />
    );
  }
  function rootsField(
    label: string,
    browseLabel: string,
    key: RootKey,
    placeholder: string,
    help: string,
  ) {
    return (
      <RootsField
        label={label}
        browseLabel={browseLabel}
        disabled={busy}
        value={rootText[key]}
        placeholder={placeholder}
        help={help}
        onChange={(value) => {
          setRootText({ ...rootText, [key]: value });
          changed();
        }}
      />
    );
  }
  return (
    <div
      className={`shell ${screen === 'Datasets' && datasetEditor ? 'dataset-editor-active' : ''}`}
    >
      <aside>
        <a className="brand" href="/">
          <Hexagon />
          <span>musubi</span>
        </a>
        <div className="workspace">WORKSPACE</div>
        <nav>
          {screens.map((name) => {
            const Icon = screenIcons[name];
            return (
              <button
                key={name}
                className={!landing && screen === name ? 'active' : ''}
                onClick={() => void navigate(name)}
              >
                <Icon />
                {name}
              </button>
            );
          })}
        </nav>
        <button
          className={`local ${health.state}`}
          onClick={() => void showReport()}
        >
          <i /> Setup: {health.label}
          <small>Training stays on your machine</small>
        </button>
      </aside>
      <main
        className={
          screen === 'Datasets' && datasetEditor ? 'dataset-editor-shell' : ''
        }
      >
        <div hidden={landing || screen !== 'Datasets'}>
          <Datasets
            onEditorChange={setDatasetEditor}
            visible={!landing && screen === 'Datasets'}
            selectedId={selection.Datasets}
            onSelect={selectDataset}
            defaultFolder={settings?.dataset_roots[0] ?? ''}
            datasetRoots={settings?.dataset_roots}
          />
        </div>
        {landing ? null : screen === 'Jobs' ? (
          <Jobs
            selectedId={selection.Jobs}
            onSelect={selectJob}
            onDirtyChange={setJobDirty}
            onRun={openRun}
          />
        ) : screen === 'Runs' ? (
          <Runs selectedId={selection.Runs} onSelect={selectRun} />
        ) : screen === 'Datasets' ? null : (
          <>
            <section className="title">
              <h1>Settings</h1>
              <p>
                Connect an existing musubi-tuner installation and check that it
                is ready.
              </p>
            </section>
            {error && (
              <div role="alert" className="error">
                {error}
              </div>
            )}
            {settings && (
              <>
                <SetupChecklist
                  connected={!dirty && setupComplete(settings)}
                  diagnosed={!!report && report.current && !dirty}
                />
                <section className="panel" id="settings-environment">
                  <div className="section-heading">
                    <h2>Training environment</h2>
                    <p>
                      The selected environment runs training. Setup never
                      installs or upgrades it.
                    </p>
                  </div>
                  {field(
                    'musubi-tuner checkout',
                    'checkout',
                    'Existing repository containing the Wan and MiniMax H3 entry points.',
                  )}
                  {field(
                    'Training Python executable',
                    'python',
                    'Select the python.exe inside your existing training environment.',
                    'file',
                  )}
                </section>
                <section className="panel">
                  <div className="section-heading">
                    <h2>Storage & model components</h2>
                    <p>
                      Use absolute local paths. Keep generated files outside the
                      training checkout and model roots.
                    </p>
                  </div>
                  <div className="columns">
                    {field(
                      'Output root',
                      'output_root',
                      'Reference evidence, checkpoints and generated samples.',
                    )}
                    {field(
                      'Cache root',
                      'cache_root',
                      'Dedicated location for prepared training caches.',
                    )}
                  </div>
                  {rootsField(
                    'Model roots (one per line)',
                    'a model root',
                    'model_roots',
                    'C:\\ai\\models',
                    'All component paths must be contained in one of these directories.',
                  )}
                  {rootsField(
                    'Dataset roots (one per line)',
                    'a dataset root',
                    'dataset_roots',
                    'C:\\datasets',
                    'Registered folders and managed uploads stay within these local roots. Keep them separate from output/cache directories.',
                  )}
                  <details>
                    <summary>
                      Model component paths <span>Wan 2.2 · MiniMax H3</span>
                    </summary>
                    <div className="columns">
                      {Object.entries(componentLabels).map(([key, label]) => (
                        <PathField
                          key={key}
                          label={label}
                          kind="file"
                          roots={rootLines(rootText.model_roots)}
                          disabled={busy}
                          value={settings.models[key] ?? ''}
                          onChange={(value) =>
                            update({
                              ...settings,
                              models: { ...settings.models, [key]: value },
                            })
                          }
                        />
                      ))}
                    </div>
                  </details>
                </section>
                <div className="actions" id="settings-actions">
                  <span role="status">{status}</span>
                  <button
                    disabled={busy || dirty}
                    onClick={() => void diagnose()}
                  >
                    {busy ? 'Working…' : 'Run diagnostics'}
                  </button>
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() => void save()}
                  >
                    Save settings
                  </button>
                </div>
                {dirty && (
                  <p className="hint">
                    Save settings before running diagnostics.
                  </p>
                )}
                {report && (
                  <section className="panel" id="diagnostics-report">
                    <h2>Installation diagnostics</h2>
                    <p>
                      Last run {new Date(report.ran_at).toLocaleString()}.
                      {(dirty || !report.current) && (
                        <strong>
                          {' '}
                          Out of date: settings have changed since this ran. Run
                          diagnostics again.
                        </strong>
                      )}
                    </p>
                    <p>
                      These checks do not establish model support. Each
                      reference workflow must pass on the target hardware.
                    </p>
                    <div className="checks">
                      {report.checks.map((check) => (
                        <div
                          className={`check check-${check.status}`}
                          key={check.name}
                        >
                          <div>
                            <strong>{check.name}</strong>
                            <p>{check.message}</p>
                          </div>
                          <StatusBadge
                            tone={checkBadges[check.status]?.tone ?? 'neutral'}
                          >
                            {checkBadges[check.status]?.label ?? check.status}
                          </StatusBadge>
                        </div>
                      ))}
                    </div>
                    <details>
                      <summary>Recorded environment</summary>
                      <pre>{JSON.stringify(report.environment, null, 2)}</pre>
                    </details>
                  </section>
                )}
              </>
            )}
          </>
        )}
      </main>
      <ConfirmDialogHost />
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
