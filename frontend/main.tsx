import {
  useCallback,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
} from 'react';
import { createRoot } from 'react-dom/client';
import { apiClient } from './api';
import './style.css';
import { ConfirmDialogHost, confirmDialog } from './ConfirmDialog';
import { Jobs } from './Jobs';
import { Runs } from './Runs';
import { Datasets } from './Datasets';
import { formatRoute, parseRoute, screens, type Screen } from './route';
import { useRunSignals } from './runSignals';

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
  checks: { name: string; status: string; message: string }[];
  environment: Record<string, unknown>;
};
const components = [
  ['wan_low', 'Wan low-noise DiT'],
  ['wan_high', 'Wan high-noise DiT'],
  ['wan_vae', 'Wan 2.1 VAE'],
  ['wan_t5', 'Wan T5 encoder'],
  ['h3_dit', 'H3 DiT'],
  ['h3_video_vae', 'H3 video VAE'],
  ['h3_audio_vae', 'H3 audio VAE'],
  ['h3_text', 'H3 Qwen text encoder'],
];

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
  const [screen, setScreen] = useState<Screen>(initialRoute.screen);
  // Each screen's selection, kept while elsewhere so that returning reopens it.
  const [selection, setSelection] = useState<Partial<Record<Screen, string>>>(
    initialRoute.id ? { [initialRoute.screen]: initialRoute.id } : {},
  );
  // Set before the browser reports the change, so only history moves look external.
  const currentHash = useRef(formatRoute(initialRoute));
  const [jobDirty, setJobDirty] = useState(false);
  const [datasetEditor, setDatasetEditor] = useState(false);
  useRunSignals(screen);
  const go = useCallback((target: Screen, id?: string, replace = false) => {
    setScreen(target);
    setSelection((value) => ({ ...value, [target]: id }));
    const hash = formatRoute({ screen: target, id });
    if (hash === currentHash.current) return;
    currentHash.current = hash;
    if (replace) window.history.replaceState(null, '', hash);
    else window.location.hash = hash;
  }, []);
  const selectDataset = useCallback(
    (id?: string, replace?: boolean) => go('Datasets', id, replace),
    [go],
  );
  const selectJob = useCallback(
    (id?: string, replace?: boolean) => go('Jobs', id, replace),
    [go],
  );
  const selectRun = useCallback(
    (id?: string, replace?: boolean) => go('Runs', id, replace),
    [go],
  );
  async function navigate(name: Screen) {
    if (screen === 'Jobs' && jobDirty && !(await confirmLeaveJob())) return;
    go(name, selection[name]);
  }
  const followHistory = useEffectEvent(async () => {
    if (window.location.hash === currentHash.current) return;
    const next = parseRoute(window.location.hash);
    if (screen === 'Jobs' && jobDirty) {
      // The browser has already moved. Put this job's URL back while the question is open.
      window.history.pushState(null, '', currentHash.current);
      if (!(await confirmLeaveJob())) return;
      setJobDirty(false);
    }
    go(next.screen, next.id);
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
    void api<Settings>('/api/settings')
      .then((value) => {
        load(value);
        setStatus('Settings loaded');
      })
      .catch((reason: Error) => {
        setError(reason.message);
        setStatus('Settings could not be loaded');
      });
  }, []);
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
    setReport(null);
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
    setReport(null);
    try {
      setReport(await api<Report>('/api/diagnostics', 'POST', {}));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function field(
    label: string,
    key: 'checkout' | 'python' | 'output_root' | 'cache_root',
    help: string,
  ) {
    return (
      <label className="field">
        {label}
        <input
          value={settings?.[key] ?? ''}
          disabled={busy}
          placeholder="C:\…"
          onChange={(event) =>
            settings && update({ ...settings, [key]: event.target.value })
          }
        />
        <small>{help}</small>
      </label>
    );
  }
  function rootsField(
    label: string,
    key: RootKey,
    placeholder: string,
    help: string,
  ) {
    return (
      <label className="field">
        {label}
        <textarea
          disabled={busy}
          value={rootText[key]}
          onChange={(event) => {
            setRootText({ ...rootText, [key]: event.target.value });
            changed();
          }}
          placeholder={placeholder}
        />
        <small>{help}</small>
      </label>
    );
  }
  return (
    <div
      className={`shell ${screen === 'Datasets' && datasetEditor ? 'dataset-editor-active' : ''}`}
    >
      <aside>
        <a className="brand" href="/">
          ◈{' '}
          <span>
            musubi<small>LOCAL TRAINING STUDIO</small>
          </span>
        </a>
        <div className="workspace">WORKSPACE</div>
        <nav>
          {screens.map((name) => (
            <button
              key={name}
              className={screen === name ? 'active' : ''}
              onClick={() => void navigate(name)}
            >
              <span aria-hidden="true">
                {name === 'Datasets' ? '▦' : name === 'Jobs' ? '▷' : '⚙'}
              </span>
              {name}
            </button>
          ))}
        </nav>
        <div className="local">
          <i /> Local connection<small>Training stays on your machine</small>
        </div>
      </aside>
      <main
        className={
          screen === 'Datasets' && datasetEditor ? 'dataset-editor-shell' : ''
        }
      >
        <header>
          <span>WORKSPACE / {screen.toUpperCase()}</span>
          <span className="badge">LoRA · Windows</span>
        </header>
        <div hidden={screen !== 'Datasets'}>
          <Datasets
            onEditorChange={setDatasetEditor}
            visible={screen === 'Datasets'}
            selectedId={selection.Datasets}
            onSelect={selectDataset}
            defaultFolder={settings?.dataset_roots[0] ?? ''}
          />
        </div>
        {screen === 'Jobs' ? (
          <Jobs
            selectedId={selection.Jobs}
            onSelect={selectJob}
            onDirtyChange={setJobDirty}
            onRun={selectRun}
          />
        ) : screen === 'Runs' ? (
          <Runs selectedId={selection.Runs} onSelect={selectRun} />
        ) : screen === 'Datasets' ? null : (
          <>
            <section className="title">
              <p className="eyebrow">YOUR MACHINE, YOUR MODELS</p>
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
                <section className="panel">
                  <div className="section-heading">
                    <span className="number">01</span>
                    <div>
                      <h2>Training environment</h2>
                      <p>
                        The selected environment runs training. Setup never
                        installs or upgrades it.
                      </p>
                    </div>
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
                  )}
                </section>
                <section className="panel">
                  <div className="section-heading">
                    <span className="number">02</span>
                    <div>
                      <h2>Storage & model components</h2>
                      <p>
                        Use absolute local paths. Keep generated files outside
                        the training checkout and model roots.
                      </p>
                    </div>
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
                    'model_roots',
                    'C:\\ai\\models',
                    'All component paths must be contained in one of these directories.',
                  )}
                  {rootsField(
                    'Dataset roots (one per line)',
                    'dataset_roots',
                    'C:\\datasets',
                    'Registered folders and managed uploads stay within these local roots. Keep them separate from output/cache directories.',
                  )}
                  <details>
                    <summary>
                      Model component paths <span>Wan 2.2 · MiniMax H3</span>
                    </summary>
                    <div className="columns">
                      {components.map(([key, label]) => (
                        <label className="field" key={key}>
                          {label}
                          <input
                            disabled={busy}
                            value={settings.models[key] ?? ''}
                            onChange={(event) =>
                              update({
                                ...settings,
                                models: {
                                  ...settings.models,
                                  [key]: event.target.value,
                                },
                              })
                            }
                          />
                        </label>
                      ))}
                    </div>
                  </details>
                </section>
                <div className="actions">
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
                  <section className="panel">
                    <h2>Installation diagnostics</h2>
                    <p>
                      These checks do not establish model support. Each
                      reference workflow must pass on the target hardware.
                    </p>
                    <div className="checks">
                      {report.checks.map((check) => (
                        <div
                          className={`check ${check.status}`}
                          key={check.name}
                        >
                          <span>{check.status === 'ok' ? '✓' : '!'}</span>
                          <div>
                            <strong>{check.name}</strong>
                            <p>{check.message}</p>
                          </div>
                          <small>{check.status}</small>
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
