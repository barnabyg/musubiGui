# Musubi GUI

A local Windows LoRA training application with a **project-managed musubi-tuner checkout**.
It supports Wan 2.2 A14B T2V and MiniMax H3 T2VA on the [target hardware](#target-hardware).
React/TypeScript provides the interface; FastAPI serves the local API and a durable worker
manages preparation and training.

Register existing datasets or upload into managed folders, browse images/video/audio,
and edit authoritative `.txt` captions with immediate saves, conflict detection and
recoverable history. Selection, exclusions and previewed bulk edits preserve originals.
Save Wan/H3 job drafts, inspect generated configuration, and reuse presets or versioned
imports/exports. Runs keep immutable media/caption snapshots, tracked cache validity,
logs, a single-GPU queue, output playback and revision comparisons.

Install Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 24 LTS and Git for Windows.
From a clean checkout, run this command in PowerShell, or double-click `run.bat`, which calls it:

```powershell
.\launch.cmd
```

It installs only the application's locked dependencies into `.venv` and `node_modules`,
builds the UI, starts the backend on **127.0.0.1:8765** and opens the browser.
Use `.\launch.cmd --port 8766 --no-browser` for a second local instance. Stop with Ctrl+C.
Registry access is required on first launch. Musubi setup is a separate action in Settings; model weights are never downloaded by setup.
The Wan reference can use the separately qualified [offline conversion](docs/reference/wan-conversion.md)
of existing scaled-FP8 files; originals remain untouched and lost precision is not recovered.

In Settings, click **Install supported version**. The app clones the official repository into
`musubi-tuner/` inside this project, checks out the revision supported by this GUI, and uses
`uv` to create its separate Python 3.12 `.venv`, install the qualified CUDA 13.0
PyTorch 2.13.0 / torchvision 0.28.0 pair, and install the editable upstream package and its
development group (including TensorBoard). This downloads training dependencies and CUDA 13.0
PyTorch, so allow several GB of disk space and internet access to GitHub, PyPI and PyTorch.
The GUI's `.venv` is separate. Installation paths are read only; external installations are
no longer used. Existing version-1 settings remain readable: model, dataset, output and cache
settings are retained, and old external installation folders are left untouched.

**Update to supported version** installs the revision shipped with your current GUI, rather
than upstream's newest commit. A GUI update can advance that supported revision. Finish or
cancel queued and active Runs first; setup also reserves the GPU across app instances and
blocks new preparation while it runs. Local source changes or an unexpected Git origin stop
the update without overwriting files. The previous source revision is retained in Git at
`refs/musubi-gui/previous`. Setup does not automatically roll back dependencies on failure:
fix the reported cause and retry the same button. A failed dependency install is not shown
as ready. Keep the backend running during setup; leaving the Settings page is fine. Setup
status survives reloads and backend restarts, and interrupted setup can be retried once its
processes have exited. The project-local setup record and sanitized failure log live under
`.cache/musubi-setup/`. Run diagnostics again after setup.

Choose dedicated output/cache roots and model roots. Component paths must remain within those model roots; symlinks and
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
versioned `diagnostics.json` beside `settings.json`. The sidebar shows it as Ready, the number of warnings to
check, Problems found or Not checked and links to the report, which stays until the next diagnostics run or managed setup and is marked out
of date once settings change. These checks do not establish model support.

Path fields can be typed, pasted or filled with Browse, and warn before saving when a path
is missing, the wrong type or outside its root. Browse lists names only, through a loopback
endpoint that never reads file contents. Saving and running enforce the same rules, including
symlink and junction containment.

Manual setup scenario: install the supported version in Settings, configure storage and model
paths, save, and run diagnostics. Click Update to supported version again to confirm setup
is repeatable; expect the installed revision to match the supported revision. Stop the backend, relaunch, and confirm that
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
Complete saved optimizer state can be restored as a new segment, within the
[limits of saved state](docs/runs.md#saved-state).

Run the measured Wan/H3 acceptance scenarios with `.venv\Scripts\python.exe -m musubi_gui.reference wan`
or the same command ending in `h3`. See [reference workflows](docs/reference/README.md)
for compatible weights, exact recipes, evidence and unresolved support limits.

## Target hardware

Native Windows 11, an RTX 3090 with 24 GB VRAM, nominal 96 GB RAM (95.6 GiB usable) and at
least 400 GB available disk.

## Licences and credits

The interface takes interaction inspiration from [AI Toolkit](https://github.com/ostris/ai-toolkit):
a persistent sidebar and grouped settings. No AI Toolkit source/assets are copied.
[musubi-tuner](https://github.com/kohya-ss/musubi-tuner) owns all training; its checkout and training environment have their own licences and stay in the ignored
`musubi-tuner/` folder. Upstream source is fetched during setup; it and model weights are not
bundled in GUI releases.

Model weights retain their model-card licences. In particular, consult the
[MiniMax H3 Community License](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)
and the model cards before using or redistributing weights. The original synthetic fixture
media and captions are dedicated to the public domain under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
