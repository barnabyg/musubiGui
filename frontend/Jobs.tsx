import { JobActionBar } from './JobActionBar';
import { useJobEditor, type JobsProps } from './jobEditor';
import { JobPresets } from './JobPresets';
import { JobReview } from './JobReview';
import { useJobSections } from './jobSections';
import { JobControls, JobIdentity } from './JobSettings';
import { JobTable } from './JobTable';
import { JobTransfer } from './JobTransfer';
import { runStatus } from './runStatus';
import './jobs.css';

export function Jobs(props: JobsProps) {
  const { onRun } = props;
  const {
    jobs,
    archived,
    summaries,
    nameField,
    datasets,
    models,
    modelRoots,
    job,
    dirty,
    busy,
    status,
    error,
    setError,
    review,
    presets,
    presetName,
    setPresetName,
    presetId,
    setPresetId,
    transfer,
    setTransfer,
    transferOpen,
    setTransferOpen,
    existingRun,
    fields,
    edit,
    config,
    choose,
    create,
    save,
    inspect,
    prepare,
    duplicate,
    archive,
    restore,
    deleteJob,
    savePreset,
    applyPreset,
    convert,
    exportConfiguration,
    importConfiguration,
    prepareReason,
  } = useJobEditor(props);
  const { actionBar, section, goToSection } = useJobSections(!!job);
  return (
    <div className="jobs-workspace">
      <section className="title">
        <h1>Jobs</h1>
        <p>
          Set up a LoRA training job, review it, then prepare a Run. A Run keeps
          the settings it was prepared with, so later edits don't change it.
        </p>
        <button disabled={busy} onClick={() => void create('wan')}>
          New Wan job
        </button>{' '}
        <button disabled={busy} onClick={() => void create('h3')}>
          New H3 job
        </button>
      </section>
      {jobs.length > 0 && (
        <JobTable
          jobs={jobs}
          summaries={summaries}
          datasets={datasets}
          selectedId={job?.id}
          disabled={busy}
          onSelect={(value) => void choose(value)}
        />
      )}
      {archived.length > 0 && (
        <details className="panel">
          <summary>Archived jobs ({archived.length})</summary>
          <p>
            Archived jobs are hidden from the list above. Their Runs, outputs
            and saved states stay in Runs.
          </p>
          <ul aria-label="Archived jobs">
            {archived.map((item) => (
              <li key={item.id}>
                {item.name} · {item.family.toUpperCase()} · r{item.revision}{' '}
                <button disabled={busy} onClick={() => void restore(item.id)}>
                  Restore {item.name}
                </button>{' '}
                <button
                  className="danger"
                  disabled={busy}
                  onClick={() => void deleteJob(item)}
                >
                  Delete {item.name}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
      <JobActionBar
        actionBar={actionBar}
        status={status}
        job={job}
        dirty={dirty}
        busy={busy}
        review={review}
        prepareReason={prepareReason}
        error={error}
        section={section}
        goToSection={goToSection}
        save={save}
        inspect={inspect}
        prepare={prepare}
        duplicate={duplicate}
        convert={convert}
        archive={archive}
        deleteJob={deleteJob}
        setTransferOpen={setTransferOpen}
      />
      {transferOpen && (
        <JobTransfer
          busy={busy}
          dirty={dirty}
          job={job}
          transfer={transfer}
          setTransfer={setTransfer}
          setError={setError}
          importConfiguration={importConfiguration}
          exportConfiguration={exportConfiguration}
          setTransferOpen={setTransferOpen}
        />
      )}
      {job && (
        <>
          <JobIdentity
            job={job}
            busy={busy}
            fields={fields}
            edit={edit}
            config={config}
            nameField={nameField}
            datasets={datasets}
            models={models}
            modelRoots={modelRoots}
          />
          <JobPresets
            job={job}
            busy={busy}
            dirty={dirty}
            presets={presets}
            presetName={presetName}
            setPresetName={setPresetName}
            presetId={presetId}
            setPresetId={setPresetId}
            savePreset={savePreset}
            applyPreset={applyPreset}
          />
          <JobControls job={job} busy={busy} fields={fields} config={config} />
          {review && existingRun && (
            <p>
              This revision already has a Run with the same inputs (
              {runStatus(existingRun.status).label}), so no new Run was created.{' '}
              <button onClick={() => onRun?.(existingRun.id)}>
                Open existing run
              </button>
            </p>
          )}
          {!review && (
            <section className="panel" id="job-section-review" tabIndex={-1}>
              <h2>Review</h2>
              <p>
                Choose Review configuration to check these settings before
                preparing a Run.
              </p>
            </section>
          )}
          {review && (
            <JobReview
              job={job}
              review={review}
              reviewField={fields.reviewField}
            />
          )}
        </>
      )}
      {!job && (
        <p>
          Select a saved job or create a draft to begin. Incomplete drafts can
          be saved.
        </p>
      )}
    </div>
  );
}
