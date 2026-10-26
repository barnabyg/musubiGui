import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { Jobs } from './Jobs';
import { Runs } from './Runs';
import { Datasets } from './Datasets';

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

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = (await response.json()) as { detail?: unknown };
    throw new Error(
      typeof body.detail === 'string'
        ? body.detail
        : `Request failed (${response.status}). Check the field values.`,
    );
  }
  return response.json() as Promise<T>;
}

function App() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [status, setStatus] = useState('Loading settings…');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const [screen, setScreen] = useState('Settings');
  const [jobDirty, setJobDirty] = useState(false);
  const [runId, setRunId] = useState<string>();
  function navigate(name: string) {
    if (
      screen === 'Jobs' &&
      jobDirty &&
      !window.confirm('Leave this job and discard unsaved changes?')
    )
      return;
    setScreen(name);
  }
  useEffect(() => {
    void api<Settings>('/api/settings')
      .then((value) => {
        setSettings(value);
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
  function update(next: Settings) {
    setSettings(next);
    setDirty(true);
    setStatus('Unsaved changes');
    setReport(null);
  }
  async function save() {
    if (!settings) return;
    setBusy(true);
    setError('');
    try {
      await api<Settings>('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      });
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
      setReport(
        await api<Report>('/api/diagnostics', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        }),
      );
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
  return (
    <div className="shell">
      <aside>
        <a className="brand" href="/">
          ◈{' '}
          <span>
            musubi<small>LOCAL TRAINING STUDIO</small>
          </span>
        </a>
        <div className="workspace">WORKSPACE</div>
        <nav>
          {['Datasets', 'Jobs', 'Runs', 'Settings'].map((name) => (
            <button
              key={name}
              className={screen === name ? 'active' : ''}
              onClick={() => navigate(name)}
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
      <main>
        <header>
          <span>WORKSPACE / {screen.toUpperCase()}</span>
          <span className="badge">LoRA · Windows</span>
        </header>
        <div hidden={screen !== 'Datasets'}>
          <Datasets />
        </div>
        {screen === 'Jobs' ? (
          <Jobs
            onDirtyChange={setJobDirty}
            onRun={(id) => {
              setRunId(id);
              setScreen('Runs');
            }}
          />
        ) : screen === 'Runs' ? (
          <Runs key={runId} selectedId={runId} />
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
                  <label className="field">
                    Model roots (one per line)
                    <textarea
                      disabled={busy}
                      value={settings.model_roots.join('\n')}
                      onChange={(event) =>
                        update({
                          ...settings,
                          model_roots: event.target.value
                            .split('\n')
                            .filter(Boolean),
                        })
                      }
                      placeholder="C:\ai\models"
                    />
                    <small>
                      All component paths must be contained in one of these
                      directories.
                    </small>
                  </label>
                  <label className="field">
                    Dataset roots (one per line)
                    <textarea
                      disabled={busy}
                      value={settings.dataset_roots.join('\n')}
                      onChange={(event) =>
                        update({
                          ...settings,
                          dataset_roots: event.target.value
                            .split('\n')
                            .filter(Boolean),
                        })
                      }
                      placeholder="C:\datasets"
                    />
                    <small>
                      Registered folders and managed uploads stay within these
                      local roots. Keep them separate from output/cache
                      directories.
                    </small>
                  </label>
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
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
