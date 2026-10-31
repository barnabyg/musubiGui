import { useEffect, useLayoutEffect, useRef, useState } from 'react';

export const sections = [
  { id: 'job-section-job', label: 'Job & dataset' },
  { id: 'job-section-dataset', label: 'Dataset preparation' },
  { id: 'job-section-training', label: 'LoRA & training' },
  { id: 'job-section-memory', label: 'Memory' },
  { id: 'job-section-checkpoints', label: 'Checkpoints & sample' },
  { id: 'job-section-review', label: 'Review' },
];

export function useJobSections(hasJob: boolean) {
  const actionBar = useRef<HTMLDivElement>(null);
  const [section, setSection] = useState(sections[0].id);
  // A chosen section stays current until the page scrolls away from where it landed: a short last
  // section can't reach the top of the window.
  const chosen = useRef<{ id: string; at: number } | null>(null);
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
  useEffect(() => {
    if (!hasJob) return;
    const follow = () => {
      if (chosen.current) {
        if (Math.abs(window.scrollY - chosen.current.at) < 2) return;
        chosen.current = null;
      }
      const line =
        (actionBar.current?.getBoundingClientRect().bottom ?? 0) + 16;
      const root = document.documentElement;
      const atEnd =
        window.scrollY + window.innerHeight >= root.scrollHeight - 2 &&
        window.scrollY > 0;
      let current = sections[0].id;
      for (const { id } of sections)
        if (
          (document.getElementById(id)?.getBoundingClientRect().top ?? 0) <=
          line
        )
          current = id;
      setSection(atEnd ? sections[sections.length - 1].id : current);
    };
    follow();
    window.addEventListener('scroll', follow, { passive: true });
    return () => window.removeEventListener('scroll', follow);
  }, [hasJob]);
  function goToSection(id: string) {
    const target = document.getElementById(id);
    if (!target) return;
    target.scrollIntoView({ block: 'start' });
    target.focus({ preventScroll: true });
    chosen.current = { id, at: window.scrollY };
    setSection(id);
  }
  return { actionBar, section, goToSection };
}
