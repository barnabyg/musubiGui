import json
from pathlib import Path

import pytest
from helpers import acceptance_workspace
from test_training_settings import client_for

from musubi_gui.jobs import smoke_config


def test_legacy_h3_preset_keeps_uniform_sampling_when_applied(tmp_path: Path) -> None:
    config = smoke_config("h3").model_dump(exclude={"timestep_sampling", "sigmoid_scale", "discrete_flow_shift"})
    presets = tmp_path / "presets"
    presets.mkdir()
    preset_id = "a" * 32
    (presets / f"{preset_id}.json").write_text(
        json.dumps({"version": 1, "id": preset_id, "name": "Old H3", "family": "h3", "config": config}),
        encoding="utf-8",
    )
    client = client_for(tmp_path)
    listed = next(preset for preset in client.get("/api/jobs/presets").json() if preset["id"] == preset_id)
    assert listed["config"]["timestep_sampling"] == "uniform"
    job = client.post("/api/jobs", json={"family": "h3", "name": "Apply legacy"}).json()
    applied = client.post(
        f"/api/jobs/{job['id']}/apply-preset", json={"preset_id": preset_id, "revision": job["revision"]}
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["config"]["timestep_sampling"] == "uniform"
    review = client.post(f"/api/jobs/{job['id']}/review", json={}).json()
    assert not [error for error in review["errors"] if error.startswith("Timestep strategy:")]


@pytest.mark.parametrize(
    ("sampling", "scale", "shift", "original"),
    [
        ("shift", 1.0, 3.0, True),
        ("shift", 0.001, 1000.0, False),
        ("sigmoid", 1000.0, 3.0, False),
        ("uniform", 1.0, 3.0, False),
    ],
)
def test_wan_timestep_commands_preserve_defaults_and_bound_custom_expert_ranges(
    tmp_path: Path, sampling: str, scale: float, shift: float, original: bool
) -> None:
    fixture = acceptance_workspace(tmp_path)
    root = Path(fixture["state"])
    client = client_for(root, fixture_mode=True)
    job = next(job for job in fixture["jobs"] if job["family"] == "wan")
    saved = client.post(
        "/api/jobs",
        json={
            "name": "Both experts",
            "family": "wan",
            "dataset_id": job["dataset_id"],
            "config": {
                "recipe": "alternating",
                "steps": 4,
                "expert_interval": 1,
                "save_state": True,
                "timestep_sampling": sampling,
                "sigmoid_scale": scale,
                "discrete_flow_shift": shift,
            },
        },
    ).json()
    review = client.post(f"/api/jobs/{saved['id']}/review", json={}).json()
    assert review["valid"], review["errors"]
    assert review["timestep_sampling"] == sampling
    assert review["timestep_range_policy"] == ("original" if original else "bounded")
    trains = [stage["args"] for stage in review["stages"] if stage["name"] == "train"]
    assert len(trains) == 4
    assert [(args[args.index("--min_timestep") + 1], args[args.index("--max_timestep") + 1]) for args in trains] == [
        ("0", "875"),
        ("875", "1000"),
        ("0", "875"),
        ("875", "1000"),
    ]
    for args in trains:
        assert args[args.index("--timestep_sampling") + 1] == sampling
        assert ("--preserve_distribution_shape" in args) == original
        if sampling != "uniform":
            assert args[args.index("--sigmoid_scale") + 1] == str(scale)
        if sampling == "shift":
            assert args[args.index("--discrete_flow_shift") + 1] == str(shift)


def test_h3_uses_uniform_and_rejects_unsupported_sampling_without_losing_draft(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    saved = client.post("/api/jobs", json={"family": "h3"}).json()
    assert saved["config"]["timestep_sampling"] == "uniform"
    changed = client.post("/api/jobs", json={"family": "h3", "config": {"timestep_sampling": "sigmoid"}}).json()
    assert changed["config"]["timestep_sampling"] == "sigmoid"
    review = client.post(f"/api/jobs/{changed['id']}/review", json={}).json()
    assert "uniform sampling only" in next(
        error for error in review["errors"] if error.startswith("Timestep strategy:")
    )


@pytest.mark.parametrize("key", ["sigmoid_scale", "discrete_flow_shift"])
def test_unsafe_timestep_parameters_are_review_errors(tmp_path: Path, key: str) -> None:
    client = client_for(tmp_path)
    saved = client.post("/api/jobs", json={"config": {key: 1e308}}).json()
    review = client.post(f"/api/jobs/{saved['id']}/review", json={}).json()
    assert any("stable sampling" in error for error in review["errors"])
