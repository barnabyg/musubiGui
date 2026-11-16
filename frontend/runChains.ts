import type { RunSummary } from './runHistory';

// One training: a Run and every segment resumed from it, grouped by the chain the backend reports.
export type Training = {
  id: string;
  // In creation order.
  segments: RunSummary[];
  latest: RunSummary;
};

// Trainings in history order: each sits where its most recently created segment does.
export function trainings(runs: RunSummary[]): Training[] {
  const groups = new Map<string, RunSummary[]>();
  for (const run of runs) {
    const key = run.chain?.id ?? run.id;
    groups.set(key, [...(groups.get(key) ?? []), run]);
  }
  return [...groups.entries()].map(([id, members]) => {
    const segments = [...members].sort(
      (a, b) => (a.chain?.position ?? 1) - (b.chain?.position ?? 1),
    );
    const latestId = segments[0].chain?.latest ?? segments[0].id;
    return {
      id,
      segments,
      latest:
        segments.find((run) => run.id === latestId) ??
        segments[segments.length - 1],
    };
  });
}

export function trainingOf(runs: RunSummary[], runId: string) {
  return trainings(runs).find((training) =>
    training.segments.some((run) => run.id === runId),
  );
}

// "Steps 120–250", or "Steps 250–400 (target)" while the end isn't known yet.
export function stepRange(run: RunSummary) {
  const { start, target, end } = run.chain.steps;
  return end === null
    ? `steps ${start}–${target} (target)`
    : `steps ${start}–${end}`;
}

export function segmentLabel(run: RunSummary) {
  return `Segment ${run.chain.position}`;
}
