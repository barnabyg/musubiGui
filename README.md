# Musubi GUI

A local Windows setup application for an **existing musubi-tuner installation**.
React/TypeScript provides the browser interface; FastAPI serves the UI and setup API.
Versioned JSON stores setup atomically. SQLite and a training worker are deferred until
dataset/job execution is implemented. Datasets and Jobs currently explain their future scope.

Install Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 24 LTS and Git for Windows.
From a clean checkout, run this single command in PowerShell:

```powershell
.\launch.cmd
```

It installs only the application's locked dependencies into `.venv` and `node_modules`,
builds the UI, starts the backend on **127.0.0.1:8765** and opens the browser.
Use `launch.cmd --port 8766 --no-browser` for a second local instance. Stop with Ctrl+C.
Registry access is required on first launch. No model download or training upgrade occurs.

In Settings, select the existing checkout and its Python executable, dedicated output/cache
roots, and model roots. Component paths must remain within those model roots; symlinks and
Windows junctions resolve before containment checks. Save, then Run diagnostics. The probe
records the selected environment's Python, packages, CUDA/GPU, upstream revision, host RAM,
disk space and write checks. It does not log raw subprocess output or environment variables.
Settings live in `.state/settings.json`; set `MUSUBI_GUI_STATE` to an absolute path for a
different application state directory. Unknown persisted versions are preserved and blocked.
Credentials are not a settings field. Use local filesystem paths, never credential-bearing URLs.

Manual setup scenario: select a nonexistent checkout, save, and run diagnostics. Correct the
reported checkout/Python paths, save, and rerun. Stop the backend, relaunch, and confirm that
the fields persist. Change a field without saving: diagnostics require a save and closing
the tab prompts about unsaved edits. A failed save retains edits and reports the failure.

`npm.cmd run verify` is the canonical check; see [verification](docs/verification.md).
Browser tests exercise real UI → API → disk → backend restart. They use temporary isolated
state and a non-training fixture checkout. GPU support requires separate reference evidence.

The interface takes interaction inspiration from [AI Toolkit](https://github.com/ostris/ai-toolkit):
a persistent sidebar and grouped settings. No AI Toolkit source/assets are copied.
[musubi-tuner](https://github.com/kohya-ss/musubi-tuner) owns all training; it remains a separate
installation with its own licences. No upstream code or model weights are distributed here.
