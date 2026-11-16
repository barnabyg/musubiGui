import { useEffect, useEffectEvent, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { apiClient } from './api';
import {
  RunCheckpoints,
  RunComparison,
  RunSamples,
  RunWarnings,
  stateFiles,
  useRunOutputs,
} from './Outputs';
import { formatRoute } from './route';
import { PreviewMemory } from './PreviewMemory';
import { confirmDelete, RunActions } from './RunActions';
import { RunBadge } from './RunBadge';
import { RunJobLink } from './RunJobLink';
import { RunList } from './RunList';
import { RunRecords } from './RunRecords';
import { RunCaching } from './RunStages';
import { RunTabs } from './RunTabs';
import { RunCompletion, RunElapsed, TrainingProgress } from './RunTiming';
import { refreshRunHistory, useRunHistory } from './runHistory';
import { notificationsWanted } from './runSignals';
import { sizeLabel } from './runStatus';
import type {
  CompletionEstimate,
  DeletePreview,
  DeleteResult,
  Progress,
  Run,
} from './runTypes';
import { SavedStates } from './SavedStates';
import './runs.css';

const request = apiClient('Run request failed.');

export function Runs({
  selectedId,
  onSelect,
}: {
  selectedId?: string;
  onSelect?: (id?: string, replace?: boolean) => void;
}) {
  const history = useRunHistory();
  const runs = history.runs ?? [];
  const [detail, setDetail] = useState<Run>();
  const [detailRefresh, setDetailRefresh] = useState(0);
  const selected = selectedId ?? '';
  const [view, setView] = useState<{
    runId: string;
    tab: 'overview' | 'samples';
  }>();
  const tab = view?.runId === selected ? view.tab : 'overview';
  const [error, setError] = useState('');
  const [detailFailure, setDetailFailure] = useState<{
    runId: string;
    message: string;
  }>();
  const [logFailure, setLogFailure] = useState<{
    runId: string;
    message: string;
  }>();
  const [notice, setNotice] = useState('');
  const [deleting, setDeleting] = useState('');
  const [busy, setBusy] = useState(false);
  const [owner, setOwner] = useState<{ run_id: string } | null>(null);
  const [fixture, setFixture] = useState(false);
  const [notify, setNotify] = useState(notificationsWanted);
  const [live, setLive] = useState<{
    runId: string;
    text: string;
    progress: Progress | null;
    performance_available?: boolean;
    run_estimate?: CompletionEstimate;
  } | null>(null);
  // Deleted, perhaps from another tab, or never there: a stale link leaves nothing to show.
  const forgetRun = useEffectEvent(() => onSelect?.(undefined, true));
  const summary = runs.find((record) => record.id === selected);
  const changeKey = summary?.change_key;
  useEffect(() => {
    const controller = new AbortController();
    if (!selected) return;
    let timer: number;
    async function refresh() {
      try {
        const record = await request<Run>(
          `/api/runs/${selected}`,
          'GET',
          undefined,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setDetail(record);
        setDetailFailure(undefined);
      } catch (reason) {
        if (controller.signal.aborted) return;
        const message = (reason as Error).message;
        if (message === 'Run not found.') forgetRun();
        else {
          setDetailFailure({ runId: selected, message });
          timer = window.setTimeout(() => void refresh(), 1000);
        }
      }
    }
    void refresh();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [selected, changeKey, detailRefresh]);
  const run = detail?.id === selected ? detail : undefined;
  useEffect(() => {
    const controller = new AbortController();
    let timer: number;
    async function refresh() {
      try {
        const capabilities = await request<{
          fixture: boolean;
          gpu_owner: { run_id: string } | null;
        }>('/api/runs/capabilities', 'GET', undefined, controller.signal);
        if (controller.signal.aborted) return;
        setOwner(capabilities.gpu_owner);
        setFixture(capabilities.fixture);
      } catch (reason) {
        if (!controller.signal.aborted) setError((reason as Error).message);
      } finally {
        if (!controller.signal.aborted)
          timer = window.setTimeout(() => void refresh(), 1000);
      }
    }
    void refresh();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, []);
  const runId = run?.id;
  const status = run?.status;
  useEffect(() => {
    if (!runId || !status || status === 'deleting') return;
    const controller = new AbortController();
    let timer: number;
    const live = [
      'preparing',
      'training',
      'generating',
      'stopping',
      'orphaned',
      'uncertain',
    ].includes(status);
    async function refresh() {
      let succeeded = false;
      try {
        const result = await request<{
          text: string;
          progress: Progress | null;
          performance_available?: boolean;
          run_estimate?: CompletionEstimate;
        }>(`/api/runs/${runId}/logs`, 'GET', undefined, controller.signal);
        if (!controller.signal.aborted) {
          setLive({ runId: runId!, ...result });
          setLogFailure(undefined);
          succeeded = true;
        }
      } catch (reason) {
        if (!controller.signal.aborted)
          setLogFailure({ runId: runId!, message: (reason as Error).message });
      } finally {
        if ((live || !succeeded) && !controller.signal.aborted)
          timer = window.setTimeout(() => void refresh(), 1000);
      }
    }
    void refresh();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [runId, status]);
  const queue = runs
    .filter((record) =>
      ['pending_preparation', 'pending_training'].includes(record.status),
    )
    .sort((a, b) => a.order - b.order);
  const ownerRun = runs.find((record) => record.id === owner?.run_id);
  const current = live?.runId === run?.id ? live : null;
  const pairedStarted =
    run?.job.config?.recipe === 'alternating' &&
    (run.resume_of ||
      run.stages.some(
        (stage) => stage.name === 'train' && stage.status !== 'pending',
      ));
  const readError =
    (detailFailure?.runId === selected ? detailFailure.message : '') ||
    (logFailure && logFailure.runId === run?.id ? logFailure.message : '');
  const showFiles = run && run.status !== 'deleting' && run.id !== deleting;
  // Changes whenever the Run or one of its stages does, so finished work appears without a click.
  const outputs = useRunOutputs(
    showFiles ? run.id : undefined,
    run ? [run.status, ...run.stages.map((stage) => stage.status)].join() : '',
  );
  async function action(name: string, body: object = {}) {
    if (!run) return;
    setBusy(true);
    setError('');
    try {
      const result = await request<Run>(
        `/api/runs/${run.id}/${name}`,
        'POST',
        body,
      );
      await refreshRunHistory();
      onSelect?.(result.id);
      // A POST response may predate progress already read while it was in flight.
      // Select its identity, then fetch authoritative detail even if its summary key stayed the same.
      setDetailRefresh((value) => value + 1);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function deleteRun() {
    if (!run) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const preview = await request<DeletePreview>(
        `/api/runs/${run.id}/delete-preview`,
      );
      if (preview.refusal) throw new Error(preview.refusal);
      if (!(await confirmDelete(run, preview))) return;
      setNotice('Deleting run…');
      // Unmounting the outputs closes any preview stream holding one of the files open.
      setDeleting(run.id);
      const result = await request<DeleteResult>(
        `/api/runs/${run.id}/delete`,
        'POST',
        { confirmed: true },
      );
      setNotice(
        result.deleted
          ? `Run deleted. Freed ${sizeLabel(result.freed_bytes ?? 0)}.`
          : '',
      );
      if (result.deleted) onSelect?.();
      await refreshRunHistory();
    } catch (reason) {
      setNotice('');
      setError((reason as Error).message);
    } finally {
      setDeleting('');
      setBusy(false);
    }
  }
  async function moveEarlier(id: string) {
    const pending = runs
      .filter((record) =>
        ['pending_preparation', 'pending_training'].includes(record.status),
      )
      .sort((a, b) => a.order - b.order);
    const index = pending.findIndex((record) => record.id === id);
    if (index < 1) return;
    [pending[index - 1], pending[index]] = [pending[index], pending[index - 1]];
    try {
      await request('/api/runs/queue/order', 'POST', {
        run_ids: pending.map((record) => record.id),
      });
      await refreshRunHistory();
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  const gpuStatus = !owner ? (
    'GPU available'
  ) : ownerRun ? (
    <>
      GPU in use by{' '}
      <a href={formatRoute({ screen: 'Runs', id: ownerRun.id })}>
        {ownerRun.job.name}
      </a>
    </>
  ) : (
    'GPU in use by other work'
  );
  return (
    <>
      {selected && (
        <button className="quiet" onClick={() => onSelect?.()}>
          <ArrowLeft /> Back to runs
        </button>
      )}
      {selected ? (
        <section className="title run-details-title">
          <h1>Run details</h1>
          {/* Joined only visually: the status stays its own live region. */}
          <p className="run-status-line">
            <span role="status">{gpuStatus}</span> · Closing the browser leaves
            training running.
          </p>
        </section>
      ) : (
        <section className="title">
          <h1>Runs</h1>
          <p>
            Each Run keeps the job settings and dataset files it was prepared
            with. Closing the browser leaves training running.
          </p>
        </section>
      )}
      {fixture && (
        <p className="error">
          Synthetic fixture mode — these runs do not establish GPU or model
          support.
        </p>
      )}
      {(error || history.error || readError) && (
        <p role="alert" className="error">
          {error || history.error || readError}
        </p>
      )}
      {!selected && <p role="status">{gpuStatus}</p>}
      {notice && <p role="status">{notice}</p>}
      <div className="runs-layout">
        {!selected && (
          <RunList
            runs={runs}
            queue={queue}
            selected={selected}
            onSelect={onSelect}
            moveEarlier={moveEarlier}
            notify={notify}
            setNotify={setNotify}
          />
        )}
        {selected && (
          <div className="run-details">
            {!run && !readError && <p>Loading Run...</p>}
            {run && (
              <RunTabs
                tab={tab}
                onChange={(value) => setView({ runId: selected, tab: value })}
              />
            )}
            {run && (
              <div
                role="tabpanel"
                id="run-panel-overview"
                aria-labelledby="run-tab-overview"
                hidden={tab !== 'overview'}
              >
                <section className="panel">
                  <h2>{run.job.name}</h2>
                  <p title={run.id}>Run {run.id.slice(0, 8)}</p>
                  <p>
                    State: <RunBadge status={run.status} />
                  </p>
                  {run.error && <p className="error">{run.error}</p>}
                  {showFiles && <RunWarnings outputs={outputs} />}
                  <RunElapsed run={run} />
                  <RunCompletion run={run} estimate={current?.run_estimate} />
                  <PreviewMemory stages={run.stages} />
                  <TrainingProgress
                    run={run}
                    progress={current?.progress ?? null}
                  />
                  <RunJobLink key={run.id} frozen={run.job} />
                </section>
                <RunActions
                  run={run}
                  busy={busy}
                  pairedStarted={Boolean(pairedStarted)}
                  action={action}
                  deleteRun={deleteRun}
                />
                {showFiles && (
                  <SavedStates
                    key={`states-${run.id}`}
                    runId={run.id}
                    files={stateFiles(outputs)}
                    config={run.job.config}
                    onRestore={(stateId, steps) =>
                      action('restore-state', {
                        state_id: stateId,
                        additional_steps: steps,
                        confirmed: true,
                      })
                    }
                  />
                )}
                <RunCaching run={run} />
                {showFiles && <RunCheckpoints outputs={outputs} />}
                {showFiles && (
                  <RunComparison
                    key={`compare-${run.id}`}
                    runId={run.id}
                    outputs={outputs}
                  />
                )}
                <RunRecords
                  run={run}
                  logText={current?.text}
                  performanceAvailable={current?.performance_available}
                />
              </div>
            )}
            {showFiles && (
              <div
                role="tabpanel"
                id="run-panel-samples"
                aria-labelledby="run-tab-samples"
                hidden={tab !== 'samples'}
              >
                <RunSamples outputs={outputs} />
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
