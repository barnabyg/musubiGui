import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

from musubi_gui import wan_tokenizer
from musubi_gui.reference import Stage
from musubi_gui.runs import ExecutionPlan, RunService
from musubi_gui.settings import Settings
from musubi_gui.wan_tokenizer import COMMIT, REPOSITORY, SUBFOLDER, publish_shared, seed_run

FILES = {"tokenizer.json": b"tokens", "spiece.model": b"pieces"}


@pytest.fixture(autouse=True)
def pinned_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wan_tokenizer, "FILES", {name: hashlib.sha256(data).hexdigest() for name, data in FILES.items()}
    )


def downloaded_cache(cache: Path, files: dict[str, bytes] = FILES) -> None:
    """What a run's Hugging Face cache holds after the pinned upstream downloaded the tokenizer."""
    folder = cache / "huggingface" / "hub" / REPOSITORY / "snapshots" / COMMIT / SUBFOLDER
    folder.mkdir(parents=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)


def test_a_verified_download_is_published_and_seeds_a_later_run(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    downloaded_cache(tmp_path / "first")
    assert publish_shared(tmp_path / "first", shared) is True

    later = tmp_path / "later"
    assert seed_run(shared, later) is True
    repository = later / "huggingface" / "hub" / REPOSITORY
    assert (repository / "refs" / "main").read_text(encoding="utf-8") == COMMIT
    folder = repository / "snapshots" / COMMIT / SUBFOLDER
    assert (folder / "tokenizer.json").read_bytes() == b"tokens"
    assert (folder / "spiece.model").read_bytes() == b"pieces"
    # The Hub has no config here; offline loading needs the empty config the online path falls back to.
    assert json.loads((folder / "config.json").read_text(encoding="utf-8")) == {}


def test_a_different_download_is_not_published(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    downloaded_cache(tmp_path / "first", {**FILES, "tokenizer.json": b"changed upstream"})
    assert publish_shared(tmp_path / "first", shared) is False
    assert seed_run(shared, tmp_path / "later") is False


def test_a_run_without_a_download_publishes_nothing(tmp_path: Path) -> None:
    assert publish_shared(tmp_path / "empty", tmp_path / "shared") is False
    assert not (tmp_path / "shared").exists()


def test_a_damaged_shared_copy_does_not_seed(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    downloaded_cache(tmp_path / "first")
    publish_shared(tmp_path / "first", shared)
    (shared / "tokenizer.json").write_bytes(b"damaged")
    later = tmp_path / "later"
    assert seed_run(shared, later) is False
    assert not (later / "huggingface").exists()


def stage_plan(root: Path, name: str, family: str = "wan") -> ExecutionPlan:
    """A latent cache stage that records the environment and files it was launched with."""
    work = root / name
    work.mkdir(parents=True)
    script = work / "stage.py"
    script.write_text(
        "import json,os,pathlib\n"
        "hub=pathlib.Path(os.environ['HF_HUB_CACHE'])\n"
        "pathlib.Path('observed.json').write_text(json.dumps({'offline':os.environ.get('HF_HUB_OFFLINE'),"
        "'seeded':[str(p.relative_to(hub)) for p in hub.rglob('tokenizer.json')]}))\n",
        encoding="utf-8",
    )
    settings = Settings(
        checkout=str(work), python=sys.executable, output_root=str(root / "output"), cache_root=str(root / "cache")
    )
    return ExecutionPlan(
        job={"id": name, "revision": 1, "family": family, "name": name},
        settings=settings,
        manifest={"items": []},
        directory=work,
        cache=work / "run-cache",
        stages=[Stage("latent_cache", [sys.executable, str(script)])],
        fingerprint=name,
    )


def observed(service: RunService, plan: ExecutionPlan) -> dict:
    run = service.submit(plan)
    deadline = time.monotonic() + 15
    while service.get(run["id"])["status"] not in {"prepared", "failed"} and time.monotonic() < deadline:
        time.sleep(0.02)
    assert service.get(run["id"])["status"] == "prepared", service.get(run["id"])
    return json.loads((plan.directory / "observed.json").read_text(encoding="utf-8"))


def test_a_new_wan_run_cannot_contact_the_hub_when_the_shared_copy_exists(tmp_path: Path) -> None:
    downloaded_cache(tmp_path / "first")
    publish_shared(tmp_path / "first", tmp_path / "cache" / "shared" / "wan-umt5-tokenizer")
    service = RunService(tmp_path / "state", device_path=tmp_path / "gpu.sqlite")
    try:
        seen = observed(service, stage_plan(tmp_path, "seeded"))
    finally:
        service.close()
    assert seen["offline"] == "1"
    assert seen["seeded"] == [os.path.join(REPOSITORY, "snapshots", COMMIT, *SUBFOLDER.split("/"), "tokenizer.json")]


def test_a_wan_run_uses_the_hub_until_a_download_has_been_published(tmp_path: Path) -> None:
    plan = stage_plan(tmp_path, "first")
    downloaded_cache(plan.cache)
    service = RunService(tmp_path / "state", device_path=tmp_path / "gpu.sqlite")
    try:
        seen = observed(service, plan)
    finally:
        service.close()
    assert seen["offline"] is None
    assert (tmp_path / "cache" / "shared" / "wan-umt5-tokenizer" / "tokenizer.json").read_bytes() == b"tokens"


def test_h3_runs_are_not_seeded_or_forced_offline(tmp_path: Path) -> None:
    downloaded_cache(tmp_path / "first")
    publish_shared(tmp_path / "first", tmp_path / "cache" / "shared" / "wan-umt5-tokenizer")
    service = RunService(tmp_path / "state", device_path=tmp_path / "gpu.sqlite")
    try:
        seen = observed(service, stage_plan(tmp_path, "h3", family="h3"))
    finally:
        service.close()
    assert seen == {"offline": None, "seeded": []}
