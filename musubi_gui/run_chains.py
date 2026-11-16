"""A chain of resumed segments: Runs linked by `resume_of` that present as one training.

Each restored or resumed segment is its own stored Run whose `resume_of` names the Run it continued, one hop
back. A chain is every Run reachable through those links, in creation order. Stored Runs are never changed to
form one, so records written before chains existed read the same way.

Branches happen when one saved position is resumed twice, for example after a segment failed. The chain's
**line** is the path from its first segment to the most recently created one; that is the training it stands
for, and its merged outputs, retention and latest status follow it. Other segments stay in the chain, listed in
creation order, and each shows its own history (the line up to it). Creation time decides, with the Run ID
breaking a tie, so the line never depends on list order.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, NamedTuple


class StepRange(NamedTuple):
    """The global steps a Run trains: from `start` (its saved step) until `target`.

    `legacy` marks a segment restored before continuous numbering, whose files and previews count its own steps
    from zero; their global step is `start` plus the recorded one.
    """

    start: int
    target: int
    legacy: bool


def step_range(run: dict[str, Any]) -> StepRange:
    segment = run.get("segment")
    if not segment:
        config = run.get("job", {}).get("config") or {}
        return StepRange(0, int(config.get("steps") or 0), False)
    if "target_steps" in segment:
        return StepRange(int(segment.get("step_offset", 0)), int(segment["target_steps"]), False)
    start = int(segment.get("schedule_start", segment.get("optimizer_step_before", 0)) or 0)
    return StepRange(start, start + int(segment.get("additional_steps", 0)), True)


def source_id(run: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> str | None:
    """The Run that `run` continued, as recorded, whether or not it still exists; None for a fresh Run.

    A retried segment records only `retry_of`, and resumes the same saved state as the segment it retried.
    """
    if run.get("resume_of"):
        return str(run["resume_of"])
    seen: set[str] = set()
    current = run
    while "segment" in current and current.get("retry_of") and current["id"] not in seen:
        seen.add(current["id"])
        retried = by_id.get(current["retry_of"])
        if retried is None:
            return None
        if retried.get("resume_of"):
            return str(retried["resume_of"])
        current = retried
    return None


def _created(run: dict[str, Any]) -> tuple[str, str]:
    return str(run.get("created", "")), str(run["id"])


@dataclass
class Chain:
    """One training: `segments` in creation order and `line`, the IDs from the first to the latest created."""

    segments: list[dict[str, Any]]
    parents: dict[str, str] = field(default_factory=dict)
    # The recorded source of the first segment, when that Run is gone.
    missing_source: str | None = None
    line: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.line = self._path(self.segments[-1]["id"])

    @property
    def id(self) -> str:
        return str(self.segments[0]["id"])

    @property
    def latest(self) -> dict[str, Any]:
        return self.segments[-1]

    def _path(self, run_id: str) -> list[str]:
        path: list[str] = []
        current: str | None = run_id
        while current is not None and current not in path:
            path.append(current)
            current = self.parents.get(current)
        return path[::-1]

    def lineage(self, run_id: str) -> list[str]:
        """The segments whose outputs belong to `run_id`'s training: the line, or for a side branch, its path."""
        return self.line if run_id in self.line else self._path(run_id)

    def children(self, run_id: str) -> list[str]:
        """Segments that continued `run_id`, in creation order."""
        return [str(item["id"]) for item in self.segments if self.parents.get(item["id"]) == run_id]


def chains(runs: Iterable[dict[str, Any]]) -> list[Chain]:
    """Every chain among `runs`, the one with the most recently created segment first."""
    records = list(runs)
    by_id = {str(run["id"]): run for run in records}
    parents: dict[str, str] = {}
    sources: dict[str, str] = {}
    group = {run_id: run_id for run_id in by_id}

    def find(run_id: str) -> str:
        while group[run_id] != run_id:
            group[run_id] = group[group[run_id]]
            run_id = group[run_id]
        return run_id

    for run_id, run in by_id.items():
        source = source_id(run, by_id)
        if source is None:
            continue
        sources[run_id] = source
        if source in by_id and source != run_id:
            parents[run_id] = source
            group[find(run_id)] = find(source)
    members: dict[str, list[dict[str, Any]]] = {}
    for run_id, run in by_id.items():
        members.setdefault(find(run_id), []).append(run)
    result = []
    for segments in members.values():
        segments.sort(key=_created)
        ids = {item["id"] for item in segments}
        first = segments[0]["id"]
        missing = sources.get(first) if sources.get(first) not in by_id else None
        result.append(
            Chain(segments, {key: value for key, value in parents.items() if key in ids}, missing_source=missing)
        )
    return sorted(result, key=lambda chain: _created(chain.latest), reverse=True)


def chain_of(runs: Iterable[dict[str, Any]], run_id: str) -> Chain:
    found = next((chain for chain in chains(runs) if any(item["id"] == run_id for item in chain.segments)), None)
    if found is None:
        raise ValueError("Run not found.")
    return found


def chain_fields(runs: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per Run ID, its place in its chain, as Run history shows it."""
    fields: dict[str, dict[str, Any]] = {}
    for chain in chains(runs):
        by_id = {item["id"]: item for item in chain.segments}
        for position, run in enumerate(chain.segments, start=1):
            run_id = run["id"]
            steps = step_range(run)
            lineage = chain.lineage(run_id)
            after = lineage.index(run_id) + 1
            # A segment that something continued reached the step its successor resumed from.
            end = (
                step_range(by_id[lineage[after]]).start
                if after < len(lineage)
                else steps.target
                if run["status"] == "completed"
                else None
            )
            fields[run_id] = {
                "id": chain.id,
                "size": len(chain.segments),
                "position": position,
                "latest": chain.latest["id"],
                "on_line": run_id in chain.line,
                "source": chain.parents.get(run_id),
                "missing_source": chain.missing_source,
                "later_segments": len(chain.children(run_id)),
                "steps": {"start": steps.start, "target": steps.target, "end": end, "legacy": steps.legacy},
            }
    return fields
