from typing import Any

from musubi_gui.run_chains import chain_fields, chain_of, chains, source_id, step_range


def run(run_id: str, created: str, *, status: str = "completed", steps: int = 100, **details: Any) -> dict[str, Any]:
    return {
        "id": run_id,
        "created": f"2026-11-16T10:00:{created}+00:00",
        "status": status,
        "job": {"id": "job", "name": "Job", "family": "wan", "revision": 1, "config": {"steps": steps}},
        **details,
    }


def segment(run_id: str, created: str, source: str, start: int, target: int, **details: Any) -> dict[str, Any]:
    return run(
        run_id,
        created,
        resume_of=source,
        segment={"step_offset": start, "target_steps": target, "additional_steps": target - start},
        **details,
    )


def test_a_run_never_resumed_is_a_chain_of_one() -> None:
    only = run("a", "01")
    [chain] = chains([only])
    assert chain.id == "a"
    assert [item["id"] for item in chain.segments] == ["a"]
    assert chain.line == ["a"]
    assert chain.latest["id"] == "a"
    assert chain.missing_source is None
    assert step_range(only) == (0, 100, False)


def test_a_linear_chain_orders_segments_by_creation_and_follows_resume_links() -> None:
    # Newest first, as the store lists them.
    records = [
        segment("c", "03", "b", 250, 400, status="training"),
        segment("b", "02", "a", 120, 300, status="stopped"),
        run("a", "01", status="stopped", steps=300),
    ]
    [chain] = chains(records)
    assert chain.id == "a"
    assert [item["id"] for item in chain.segments] == ["a", "b", "c"]
    assert chain.line == ["a", "b", "c"]
    assert chain.latest["status"] == "training"
    assert chain.children("a") == ["b"]
    assert chain.children("c") == []
    assert chain_of(records, "b").id == "a"
    assert step_range(records[0]) == (250, 400, False)

    fields = chain_fields(records)
    assert fields["a"]["size"] == 3
    assert [fields[key]["position"] for key in "abc"] == [1, 2, 3]
    assert {fields[key]["latest"] for key in "abc"} == {"c"}
    # A stopped segment reached the step the next one resumed from.
    assert fields["a"]["steps"] == {"start": 0, "target": 300, "end": 120, "legacy": False}
    assert fields["b"]["steps"] == {"start": 120, "target": 300, "end": 250, "legacy": False}
    assert fields["c"]["steps"] == {"start": 250, "target": 400, "end": None, "legacy": False}
    assert fields["a"]["later_segments"] == 1 and fields["c"]["later_segments"] == 0


def test_a_completed_latest_segment_reached_its_target() -> None:
    records = [segment("b", "02", "a", 100, 200), run("a", "01")]
    assert chain_fields(records)["b"]["steps"]["end"] == 200


def test_branches_follow_the_latest_created_segment_and_list_the_rest_in_creation_order() -> None:
    # b failed; the user resumed a's state again as c, then continued c as d.
    records = [
        segment("d", "04", "c", 300, 400),
        segment("c", "03", "a", 200, 300),
        segment("b", "02", "a", 200, 300, status="failed"),
        run("a", "01", status="stopped", steps=300),
    ]
    [chain] = chains(records)
    assert [item["id"] for item in chain.segments] == ["a", "b", "c", "d"]
    assert chain.line == ["a", "c", "d"]
    assert chain.children("a") == ["b", "c"]
    # A side branch shows its own history: the line up to it.
    assert chain.lineage("b") == ["a", "b"]
    assert chain.lineage("a") == ["a", "c", "d"]
    assert chain.lineage("d") == ["a", "c", "d"]
    fields = chain_fields(records)
    assert fields["b"]["on_line"] is False and fields["c"]["on_line"] is True
    assert fields["a"]["steps"]["end"] == 200


def test_a_branch_created_later_becomes_the_line() -> None:
    records = [
        segment("e", "05", "b", 300, 500),
        segment("d", "04", "c", 300, 400),
        segment("c", "03", "a", 200, 300),
        segment("b", "02", "a", 200, 300),
        run("a", "01", steps=300),
    ]
    assert chain_of(records, "d").line == ["a", "b", "e"]


def test_a_creation_tie_is_broken_by_id_so_the_line_is_deterministic() -> None:
    records = [segment("y", "02", "a", 1, 2), segment("x", "02", "a", 1, 2), run("a", "01")]
    assert chain_of(records, "a").line == ["a", "y"]
    assert chain_of(list(reversed(records)), "a").line == ["a", "y"]


def test_a_retried_segment_continues_from_the_same_source_as_the_one_it_retried() -> None:
    first = segment("b", "02", "a", 100, 200, status="failed")
    # A retry copies the segment fields, but records only retry_of.
    retry = run("r", "03", retry_of="b", segment=first["segment"])
    records = [retry, first, run("a", "01")]
    by_id = {item["id"]: item for item in records}
    assert source_id(retry, by_id) == "a"
    assert chain_of(records, "r").line == ["a", "r"]
    # A retry of a Run that was never resumed is a separate training, as before.
    plain = run("p", "04", retry_of="a")
    assert source_id(plain, {**by_id, "p": plain}) is None
    assert chain_of([plain, *records], "p").id == "p"


def test_a_missing_source_starts_the_chain_at_the_first_segment_still_present() -> None:
    # The source was deleted by an older version, which allowed deleting it alone.
    records = [segment("c", "03", "b", 250, 400), segment("b", "02", "gone", 120, 300)]
    [chain] = chains(records)
    assert chain.id == "b"
    assert chain.line == ["b", "c"]
    assert chain.missing_source == "gone"
    fields = chain_fields(records)
    assert fields["b"]["missing_source"] == "gone"
    assert fields["b"]["source"] is None
    assert fields["b"]["steps"]["start"] == 120


def test_segments_restored_before_continuous_numbering_count_from_their_saved_step() -> None:
    legacy = run(
        "b", "02", resume_of="a", segment={"additional_steps": 50, "optimizer_step_before": 30, "counter_resets": True}
    )
    assert step_range(legacy) == (30, 80, True)
    paired = run("p", "02", resume_of="a", segment={"additional_steps": 4, "schedule_start": 6})
    assert step_range(paired) == (6, 10, True)
    # Records without a job config still read.
    assert step_range({"id": "x", "job": {}}) == (0, 0, False)


def test_independent_trainings_stay_separate_and_newest_first() -> None:
    records = [segment("b", "03", "a", 1, 2), run("z", "02"), run("a", "01")]
    assert [chain.id for chain in chains(records)] == ["a", "z"]
    assert chain_fields(records)["z"]["size"] == 1


def test_a_resume_cycle_in_damaged_records_does_not_loop() -> None:
    records = [run("a", "01", resume_of="b"), run("b", "02", resume_of="a")]
    chain = chain_of(records, "a")
    assert {item["id"] for item in chain.segments} == {"a", "b"}
