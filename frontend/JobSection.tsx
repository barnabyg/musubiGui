import { createContext, useContext, type ComponentProps } from 'react';

export const JobSectionContext = createContext('job-section-job');

export function JobSection({
  id,
  ...props
}: ComponentProps<'section'> & { id: string }) {
  const selected = useContext(JobSectionContext);
  return (
    <div
      id={id}
      role="tabpanel"
      aria-labelledby={`${id}-tab`}
      hidden={selected !== id}
      tabIndex={0}
    >
      <section {...props} />
    </div>
  );
}
