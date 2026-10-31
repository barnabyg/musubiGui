import type { RefObject } from 'react';
import { ActionMenu } from './ActionMenu';
import { sections } from './jobSections';
import type { Job, Review } from './jobTypes';

export function JobActionBar({
  actionBar,
  status,
  job,
  dirty,
  busy,
  review,
  prepareReason,
  error,
  section,
  goToSection,
  save,
  inspect,
  prepare,
  duplicate,
  convert,
  archive,
  deleteJob,
  setTransferOpen,
}: {
  actionBar: RefObject<HTMLDivElement | null>;
  status: string;
  job: Job | null;
  dirty: boolean;
  busy: boolean;
  review: Review | null;
  prepareReason: string;
  error: string;
  section: string;
  goToSection: (id: string) => void;
  save: () => Promise<void>;
  inspect: () => Promise<void>;
  prepare: (start?: boolean) => Promise<void>;
  duplicate: () => Promise<void>;
  convert: () => Promise<void>;
  archive: () => Promise<void>;
  deleteJob: (target: Job) => Promise<void>;
  setTransferOpen: (open: boolean) => void;
}) {
  return (
    <div className="job-action-bar" ref={actionBar}>
      <div className="actions">
        <span role="status">{status}</span>
        {job && (
          <>
            <button
              className={dirty ? 'primary' : ''}
              disabled={busy}
              onClick={() => void save()}
            >
              Save draft
            </button>
            <button
              className={!dirty && !review?.valid ? 'primary' : ''}
              disabled={busy}
              onClick={() => void inspect()}
            >
              Review configuration
            </button>
            <button
              disabled={busy || !!prepareReason}
              title={prepareReason || undefined}
              aria-describedby={prepareReason ? 'prepare-reason' : undefined}
              onClick={() => void prepare()}
            >
              Prepare run
            </button>
            <button
              className={prepareReason ? '' : 'primary'}
              disabled={busy || !!prepareReason}
              title={prepareReason || undefined}
              aria-describedby={prepareReason ? 'prepare-reason' : undefined}
              onClick={() => void prepare(true)}
            >
              Prepare and start
            </button>
          </>
        )}
        <ActionMenu
          label="More job actions"
          disabled={busy}
          note={
            dirty
              ? 'Save your edits to use the job actions.'
              : !job
                ? 'Open a job to use the job actions.'
                : undefined
          }
          items={[
            {
              label: 'Duplicate job',
              disabled: !job || dirty,
              onSelect: () => void duplicate(),
            },
            {
              label: `Change model to ${job?.family === 'h3' ? 'Wan T2V' : 'H3 T2VA'}`,
              disabled: !job || dirty,
              onSelect: () => void convert(),
            },
            {
              label: 'Archive job',
              disabled: !job || dirty,
              onSelect: () => void archive(),
            },
            {
              label: 'Delete job',
              danger: true,
              disabled: !job || dirty,
              onSelect: () => job && void deleteJob(job),
            },
            {
              label: 'Import / export configuration',
              onSelect: () => setTransferOpen(true),
            },
          ]}
        />
      </div>
      {job && prepareReason && (
        <p id="prepare-reason" className="hint">
          {prepareReason}
        </p>
      )}
      {job && (
        <nav className="job-sections" aria-label="Job sections">
          {sections.map(({ id, label }) => (
            <button
              key={id}
              className="quiet"
              aria-current={section === id ? 'true' : undefined}
              onClick={() => goToSection(id)}
            >
              {label}
            </button>
          ))}
        </nav>
      )}
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
    </div>
  );
}
