import { useLayoutEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import type { Review } from './jobTypes';

export const sections = [
  { id: 'job-section-job', label: 'Job & dataset' },
  { id: 'job-section-essentials', label: 'Training' },
  { id: 'job-section-samples', label: 'Samples' },
  { id: 'job-section-dataset', label: 'Dataset preparation' },
  { id: 'job-section-advanced', label: 'Advanced' },
  { id: 'job-section-presets', label: 'Presets' },
  { id: 'job-section-review', label: 'Review' },
];

export function useJobSections(
  jobId: string | undefined,
  review: Review | null,
) {
  const actionBar = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState({
    jobId,
    review,
    section: sections[0].id,
  });
  let { section } = selection;
  if (selection.jobId !== jobId || selection.review !== review) {
    section = review
      ? 'job-section-review'
      : selection.jobId !== jobId
        ? sections[0].id
        : section;
    setSelection({ jobId, review, section });
  }
  function goToSection(id: string) {
    setSelection({ jobId, review, section: id });
  }
  // Keeps focused and scrolled-to content clear of the sticky bar, however far it has wrapped.
  useLayoutEffect(() => {
    const bar = actionBar.current;
    if (!bar) return;
    const root = document.documentElement;
    const observer = new ResizeObserver(() => {
      root.style.scrollPaddingTop = `${bar.offsetHeight + 12}px`;
    });
    observer.observe(bar);
    return () => {
      observer.disconnect();
      root.style.scrollPaddingTop = '';
    };
  }, []);
  // Error links reveal the owning panel before focusField focuses its control.
  useLayoutEffect(() => {
    const reveal = (event: Event) => {
      const panel = (event.target as HTMLElement).closest<HTMLElement>(
        '[role="tabpanel"]',
      );
      if (panel && sections.some(({ id }) => id === panel.id))
        flushSync(() =>
          setSelection((current) => ({ ...current, section: panel.id })),
        );
    };
    document.addEventListener('reveal-job-field', reveal);
    return () => document.removeEventListener('reveal-job-field', reveal);
  }, []);
  return { actionBar, section, goToSection };
}
