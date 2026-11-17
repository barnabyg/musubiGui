# Runs

Use **Runs** in the sidebar to browse Run history and manage the pending queue. Select a Run to open its dedicated detail page; **Back to runs** returns to history. Prepare new Runs from a saved, reviewed job using the link to Jobs. Direct links, reloads and browser back/forward keep the selected detail page.

The Runs collection lists the queue and history. A training resumed in segments is one
row that expands to its segments. Opening a Run shows only its details. Statuses read as labels such as
Preparing or Ready to start, and the stored values are unchanged. The GPU line names the job
using the GPU and links to its Run. On Run details it shares one line with the reminder that
closing the browser leaves training running.

Each topic has its own page:

- [Run details, folders and links](runs-details.md): the Overview and Samples tabs,
  trainings resumed in segments, output folder and checkpoint names, and links between
  Runs and jobs.
- [Preparing, training and stopping Runs](runs-lifecycle.md): preparation, immutable
  inputs, caches, the GPU lease, training, queueing, stopping, recovery and deleting a
  Run.
- [Saved state](runs-saved-state.md): restoring a saved optimizer state as a new
  segment, including alternating Wan pairs.
- [Run timing, diagnostics and refresh](runs-timing.md): seconds per step, H3
  performance diagnostics, Run elapsed, remaining-time estimates and history refresh.
