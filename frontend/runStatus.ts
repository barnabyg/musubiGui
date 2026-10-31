import type { Tone } from './StatusBadge';

// One place for how a Run's stored status reads, so every screen says the same thing.
const statuses: Record<string, { label: string; tone: Tone }> = {
  pending_preparation: { label: 'Waiting to prepare', tone: 'neutral' },
  preparing: { label: 'Preparing', tone: 'running' },
  prepared: { label: 'Ready to start', tone: 'neutral' },
  pending_training: { label: 'Waiting to train', tone: 'neutral' },
  training: { label: 'Training', tone: 'running' },
  generating: { label: 'Generating sample', tone: 'running' },
  stopping: { label: 'Stopping', tone: 'running' },
  completed: { label: 'Completed', tone: 'success' },
  failed: { label: 'Failed', tone: 'failure' },
  cancelled: { label: 'Cancelled', tone: 'warning' },
  stopped: { label: 'Stopped', tone: 'warning' },
  interrupted: { label: 'Interrupted', tone: 'warning' },
  orphaned: { label: 'Needs recovery', tone: 'warning' },
  uncertain: { label: 'Needs checking', tone: 'warning' },
  deleting: { label: 'Deleting', tone: 'warning' },
};

// A status from a newer version still reads as something.
export function runStatus(status: string) {
  return (
    statuses[status] ?? {
      label: status.replace(/_/g, ' '),
      tone: 'neutral' as const,
    }
  );
}

export function readableTime(iso: string) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
      });
}
