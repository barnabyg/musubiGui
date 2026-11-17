import { HelpLink } from './Help';
import { ArrowLeft } from 'lucide-react';
import { JobSection, JobSectionContext } from './JobSection';
import { JobActionBar } from './JobActionBar';
import { JobEssentials } from './JobEssentials';
import { JobSamples } from './JobSamples';
import { useJobEditor, type JobsProps } from './jobEditor';
import { JobPresets } from './JobPresets';
import { JobReview } from './JobReview';
import { useJobSections } from './jobSections';
import { JobControls, JobIdentity } from './JobSettings';
import { JobTable } from './JobTable';
import { JobTraining } from './JobTraining';
import { JobTransfer } from './JobTransfer';
import { runStatus } from './runStatus';
import { datasetItemRoute } from './route';
import './jobs.css';

export function Jobs(props: JobsProps) {
  const { onRun } = props;
  const {
    loadError,
    retryLoad,
    jobs,
    archived,
    summaries,
    nameField,
    datasets,
    models,
    modelRoots,
    trainingOptions,
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
    media,
    edit,
    config,
    choose,
    back,
    create,
    save,
    inspect,
    prepare,
    start,
    current,
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
    failedCaption,
  } = useJobEditor(props);
  const { actionBar, section, goToSection } = useJobSections(job?.id, review);
  return (
    <div className="jobs-workspace">
      {job && (
        <button className="quiet" disabled={busy} onClick={() => void back()}>
          <ArrowLeft /> Back to jobs
        </button>
      )}
      <section className="title">
        <h1>{job ? job.name : 'Jobs'}</h1>
        <p>
          <HelpLink topic="Frozen revisions" />
        </p>
        {!job && (
          <>
            <button disabled={busy} onClick={() => void create('wan')}>
              New Wan job
            </button>{' '}
            <button disabled={busy} onClick={() => void create('h3')}>
              New H3 job
            </button>
          </>
        )}
      </section>
      {loadError && (
        <div role="alert" className="error">
          <p>{loadError}</p>
          <button onClick={retryLoad}>Retry loading jobs</button>
        </div>
      )}
      {!job && jobs.length > 0 && (
        <JobTable
          jobs={jobs}
          summaries={summaries}
          datasets={datasets}
          disabled={busy}
          onSelect={(value) => void choose(value)}
        />
      )}
      {!job && archived.length > 0 && (
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
      {!job && (
        <>
          <p role="status">{status}</p>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <button disabled={busy} onClick={() => setTransferOpen(true)}>
            Import configuration
          </button>
        </>
      )}
      {job && (
        <JobActionBar
          actionBar={actionBar}
          status={status}
          current={current}
          openRun={(id) => onRun?.(id)}
          job={job}
          dirty={dirty}
          busy={busy}
          review={review}
          prepareReason={prepareReason}
          captionRepair={
            job &&
            failedCaption && (
              <a
                href={datasetItemRoute(
                  failedCaption.datasetId,
                  failedCaption.item.id,
                  'caption',
                  job.id,
                  job.family === 'h3' ? job.config.frames : undefined,
                )}
              >
                Resolve caption for {failedCaption.item.name}
              </a>
            )
          }
          error={error}
          section={section}
          goToSection={goToSection}
          save={save}
          inspect={inspect}
          prepare={prepare}
          start={start}
          duplicate={duplicate}
          convert={convert}
          archive={archive}
          deleteJob={deleteJob}
          setTransferOpen={setTransferOpen}
        />
      )}
      {job && review && (
        <JobSectionContext value={section}>
          <JobReview
            prepareReason={prepareReason}
            job={job}
            review={review}
            reviewField={fields.reviewField}
          />
        </JobSectionContext>
      )}
      {job && <JobTraining key={job.id} current={current} />}
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
        <JobSectionContext value={section}>
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
          <JobEssentials
            media={media}
            current={current}
            job={job}
            busy={busy}
            fields={fields}
            config={config}
            trainingOptions={trainingOptions}
          />
          <JobSamples job={job} busy={busy} fields={fields} config={config} />
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
          <JobControls
            edit={edit}
            datasets={datasets}
            media={media}
            job={job}
            busy={busy}
            fields={fields}
            config={config}
          />
          {section === 'job-section-review' && review && existingRun && (
            <p>
              This revision already has a Run with the same inputs (
              {runStatus(existingRun.status).label}), so no new Run was created.{' '}
              <button onClick={() => onRun?.(existingRun.id)}>
                Open existing run
              </button>
            </p>
          )}
          {!review && (
            <JobSection className="panel" id="job-section-review">
              <h2>Review</h2>
              <p>
                Choose Review configuration to check these settings before
                preparing a Run.
              </p>
            </JobSection>
          )}
        </JobSectionContext>
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
