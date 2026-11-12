import { useEffect, useState } from 'react';
import { apiClient } from './api';

type Checkout = {
  checkout: string;
  python: string;
  supported_revision: string;
  installed_revision: string;
  ready: boolean;
  fixture?: boolean;
  operation: { state: string; message: string };
};

const api = apiClient('Could not read Musubi setup status.');

export function ManagedCheckout({
  disabled,
  onChange,
  onRunning,
  onReady,
}: {
  disabled: boolean;
  onChange: () => void;
  onRunning: (running: boolean) => void;
  onReady: (ready: boolean) => void;
}) {
  const [checkout, setCheckout] = useState<Checkout | null>(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const running = checkout?.operation.state === 'running';
  useEffect(() => {
    let current = true;
    const refresh = () => {
      void api<Checkout>('/api/checkout').then(
        (value) => {
          if (current) {
            setCheckout(value);
            onRunning(value.operation.state === 'running');
            onReady(value.ready);
          }
        },
        (error: Error) => {
          if (current) setError(error.message);
        },
      );
    };
    refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [onRunning, onReady]);

  async function update() {
    setStarting(true);
    onRunning(true);
    setError('');
    try {
      const value = await api<Checkout>('/api/checkout/update', 'POST', {});
      setCheckout(value);
      onReady(value.ready);
      onRunning(value.operation.state === 'running');
      onChange();
    } catch (error) {
      setError((error as Error).message);
      onRunning(false);
    } finally {
      setStarting(false);
    }
  }

  return (
    <section className="panel" id="settings-environment">
      <div className="section-heading">
        <h2>Training environment</h2>
        <p>
          Musubi Tuner lives inside this project. Install or update to the
          latest version supported by this GUI. Setup downloads source and
          training dependencies, including CUDA 13.0 PyTorch; model weights are
          separate.
        </p>
      </div>
      {checkout && (
        <>
          <p>
            Checkout: <code>{checkout.checkout}</code>
          </p>
          <p>
            Training Python: <code>{checkout.python}</code>
          </p>
          <p>
            Installed revision:{' '}
            <code>{checkout.installed_revision || 'Not installed'}</code>
          </p>
          <p>
            Latest supported revision:{' '}
            <code>{checkout.supported_revision}</code>
          </p>
          <p>
            {checkout.ready
              ? 'Supported version installed.'
              : 'Setup required.'}
          </p>
          <p aria-live="polite">{checkout.operation.message}</p>
          <button
            disabled={disabled || starting || running || checkout.fixture}
            onClick={() => void update()}
          >
            {starting || running
              ? 'Installing Musubi…'
              : checkout.ready
                ? 'Update to supported version'
                : 'Install supported version'}
          </button>
          <p className="hint">
            Finish or cancel queued and active Runs first. Local source changes
            are preserved. Setup continues if you leave this page; keep the app
            running. After setup, run diagnostics before training.
          </p>
        </>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  );
}
