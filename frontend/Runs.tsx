import { useEffect, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { apiClient } from './api';
import {
  RunOutputActions,
  RunComparison,
  RunSamples,
  RunWarnings,
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
import { RunTabs, type RunTab } from './RunTabs';
import { RunTiming, TrainingProgress } from './RunTiming';
import { trainingOf } from './runChains';
import { refreshRunHistory, useRunHistory } from './runHistory';
import { useRunDetail, useRunLogs } from './runLive';
import { RunSegments } from './RunSegments';
import { HelpLink } from './Help';
import { sizeLabel } from './runStatus';
import type { DeletePreview, DeleteResult, Run } from './runTypes';
import { RunResume } from './RunResume';
import './runs.css';

const request = apiClient('Run request failed.');
// Ended Runs, whose saved states can be inspected and resumed.
const ENDED = ['completed', 'stopped', 'failed', 'cancelled', 'interrupted'];

export function Runs({
  selectedId,
  onSelect,
}: {
  selectedId?: string;
  onSelect?: (id?: string, replace?: boolean) => void;
}) {
  const history = useRunHistory();
  const runs = history.runs ?? [];
  const [detailRefresh, setDetailRefresh] = useState(0);
  const selected = selectedId ?? '';
  const [view, setView] = useState<{
    runId: string;
    tab: RunTab;
  }>();
  const tab = view?.runId === selected ? view.tab : 'overview';
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [deleting, setDeleting] = useState('');
  const [busy, setBusy] = useState(false);
  const [owner, setOwner] = useState<{ run_id: string } | null>(null);
  const [fixture, setFixture] = useState(false);
  const summary = runs.find((record) => record.id === selected);
  const changeKey = summary?.change_key;
  const training = selected ? trainingOf(runs, selected) : undefined;
  const segments =
    training && training.segments.length > 1 ? training.segments : [];
  // A stale link to a deleted Run leaves nothing to show.
  const detail = useRunDetail(
    selected || undefined,
    changeKey,
    detailRefresh,
    () => onSelect?.(undefined, true),
  );
  const run = detail.run;
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
  const logs = useRunLogs(run?.id, run?.status);
  const queue = runs
    .filter((record) =>
      ['pending_preparation', 'pending_training'].includes(record.status),
    )
    .sort((a, b) => a.order - b.order);
  const ownerRun = runs.find((record) => record.id === owner?.run_id);
  const current = logs.live;
  const pairedStarted =
    run?.job.config?.recipe === 'alternating' &&
    (run.resume_of ||
      run.stages.some(
        (stage) => stage.name === 'train' && stage.status !== 'pending',
      ));
  const readError = detail.error || logs.error;
  const showFiles = run && run.status !== 'deleting' && run.id !== deleting;
  // Changes whenever the Run, one of its stages or another segment of its training does, so
  // finished work appears without a click.
  const outputs = useRunOutputs(
    showFiles ? run.id : undefined,
    run
      ? [
          run.status,
          ...run.stages.map((stage) => stage.status),
          ...segments.map((segment) => `${segment.id}:${segment.status}`),
        ].join()
      : '',
    segments.length > 1,
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
  // Deletes one Run, or with `chain` every segment of the training it belongs to.
  async function remove(runId: string, chain: boolean) {
    const target =
      runs.find((record) => record.id === runId) ??
      (run?.id === runId ? run : undefined);
    if (!target) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const preview = await request<DeletePreview>(
        `/api/runs/${runId}/delete-preview${chain ? '?chain=true' : ''}`,
      );
      if (preview.refusal) throw new Error(preview.refusal);
      if (!(await confirmDelete(target, preview))) return;
      setNotice(chain ? 'Deleting training…' : 'Deleting run…');
      // Unmounting the outputs closes any preview stream holding one of the files open.
      setDeleting(chain ? selected || runId : runId);
      const result = await request<DeleteResult>(
        `/api/runs/${runId}/delete`,
        'POST',
        { confirmed: true, chain },
      );
      setNotice(
        result.deleted
          ? `${chain ? 'Training' : 'Run'} deleted. Freed ${sizeLabel(result.freed_bytes ?? 0)}.`
          : '',
      );
      if (
        result.deleted &&
        selected &&
        (selected === runId || result.deleted_ids?.includes(selected))
      )
        onSelect?.();
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
            <span role="status">{gpuStatus}</span> ·{' '}
            <HelpLink topic="Background training" />
          </p>
        </section>
      ) : (
        <section className="title">
          <h1>Runs</h1>
          <p>
            <HelpLink topic="Background training" />
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
            onDelete={remove}
            busy={busy}
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
                  {showFiles && (
                    <RunOutputActions
                      key={`outputs-${run.id}`}
                      directory={run.directory}
                      outputs={outputs}
                      completed={run.status === 'completed'}
                      samplingDisabled={
                        run.job.config?.sample_enabled === false &&
                        !run.job.config?.preview_every
                      }
                      openSamples={() => {
                        setView({ runId: selected, tab: 'samples' });
                        document.getElementById('run-tab-samples')?.focus();
                      }}
                    />
                  )}
                  {run.error && <p className="error">{run.error}</p>}
                  {showFiles && <RunWarnings outputs={outputs} />}
                  <PreviewMemory stages={run.stages} />
                  <TrainingProgress
                    run={run}
                    progress={current?.progress ?? null}
                  />
                  <RunTiming
                    run={run}
                    progress={current?.progress ?? null}
                    estimate={current?.run_estimate}
                  />
                  <RunJobLink key={run.id} frozen={run.job} />
                </section>
                {segments.length > 1 && (
                  <RunSegments
                    runId={run.id}
                    segments={segments}
                    progress={current?.progress ?? null}
                  />
                )}
                <RunActions
                  run={run}
                  progress={current?.progress ?? null}
                  busy={busy}
                  pairedStarted={Boolean(pairedStarted)}
                  segments={segments}
                  action={action}
                  deleteRun={(chain) => remove(run.id, chain)}
                  resume={
                    showFiles &&
                    ENDED.includes(run.status) && (
                      <RunResume
                        key={run.id}
                        run={run}
                        allowEditedRevision={
                          !segments.find((segment) => segment.id === run.id)
                            ?.chain.later_segments
                        }
                        busy={busy}
                        onAction={action}
                      />
                    )
                  }
                />
                <RunCaching run={run} chained={segments.length > 1} />
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
                  refreshOutputs={
                    showFiles && (
                      <button disabled={outputs.busy} onClick={outputs.reload}>
                        Refresh outputs
                      </button>
                    )
                  }
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
