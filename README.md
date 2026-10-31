# Musubi GUI

A local Windows LoRA training application for an **existing musubi-tuner installation**.
It supports Wan 2.2 A14B T2V and MiniMax H3 T2VA on Windows 11 / RTX 3090 /
96 GB RAM, with 400+ GB available disk. React/TypeScript provides the interface;
FastAPI serves the local API and a durable worker manages preparation and training.

Register existing datasets or upload into managed folders, browse images/video/audio,
and edit authoritative `.txt` captions with immediate saves, conflict detection and
recoverable history. Selection, exclusions and previewed bulk edits preserve originals.
Save Wan/H3 job drafts, inspect generated configuration, and reuse presets or versioned
imports/exports. Runs keep immutable media/caption snapshots, tracked cache validity,
logs, a single-GPU queue, output playback and revision comparisons.

Install Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 24 LTS and Git for Windows.
From a clean checkout, double-click `run.bat` or run this command in PowerShell:

```powershell
.\run.bat
```

It installs only the application's locked dependencies into `.venv` and `node_modules`,
builds the UI, starts the backend on **127.0.0.1:8765** and opens the browser.
Use `run.bat --port 8766 --no-browser` for a second local instance. Stop with Ctrl+C.
Registry access is required on first launch. No model download or training upgrade occurs.
The Wan reference can use the separately qualified [offline conversion](docs/reference/wan-conversion.md)
of existing scaled-FP8 files; originals remain untouched and lost precision is not recovered.

In Settings, select the existing checkout and its Python executable, dedicated output/cache
roots, and model roots. Component paths must remain within those model roots; symlinks and
Windows junctions resolve before containment checks. Enter one model or dataset root per line;
saving drops blank lines and surrounding spaces. Save, then Run diagnostics: it warns about each
configured root that is not an existing folder, such as two paths joined on one line. The probe
records the selected environment's Python, packages, CUDA/GPU, upstream revision, host RAM,
disk space and write checks. It does not log raw subprocess output or environment variables.
Settings live in `.state/settings.json`; dataset/run metadata uses versioned SQLite
and job revisions remain versioned JSON. Set `MUSUBI_GUI_STATE` to an absolute path for a
different application state directory. Unknown persisted versions are preserved and blocked.
Credentials are not a settings field. Use local filesystem paths, never credential-bearing URLs.

Run diagnostics keeps its latest result, with its time and the settings it checked, in a
versioned `diagnostics.json` beside `settings.json`. The sidebar shows it as Ready, Problems
found or Not checked and links to the report, which stays until the next run and is marked out
of date once settings change. These checks do not establish model support.

Manual setup scenario: select a nonexistent checkout, save, and run diagnostics. Correct the
reported checkout/Python paths, save, and rerun. Stop the backend, relaunch, and confirm that
the fields persist. Change a field without saving: diagnostics require a save and closing
the tab prompts about unsaved edits. A failed save retains edits and reports the failure.

`npm.cmd run verify` is the canonical check; see [verification](docs/verification.md).
Browser tests exercise real UI → API → disk → backend restart and supervised process
recovery. They use temporary isolated state and explicitly labelled CPU fixture workers.
GPU support uses separate target-PC reference and GUI acceptance evidence.

Run `acceptance.cmd --workspace .cache\acceptance-1` for a complete isolated synthetic
scenario with datasets and Wan/H3 jobs. See [release acceptance](docs/release.md),
[datasets](docs/datasets.md), [jobs](docs/jobs.md), [runs](docs/runs.md) and
[outputs](docs/outputs.md) for commands and recovery scenarios.

Stop is observed rather than assumed, and force termination requires confirmation.
Complete saved optimizer state can be restored as a new segment. The pinned upstream
resets its displayed step counter and data position, so seamless resume is unsupported.
LoRA checkpoints alone do not restore optimizer state.

Run the measured Wan/H3 acceptance scenarios with `.venv\Scripts\python.exe -m musubi_gui.reference wan`
or the same command ending in `h3`. See [reference workflows](docs/reference/README.md)
for compatible weights, exact recipes, evidence and unresolved support limits.

The interface takes interaction inspiration from [AI Toolkit](https://github.com/ostris/ai-toolkit):
a persistent sidebar and grouped settings. No AI Toolkit source/assets are copied.
[musubi-tuner](https://github.com/kohya-ss/musubi-tuner) owns all training; it remains a separate
installation with its own licences. No upstream code or model weights are distributed here.
