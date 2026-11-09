# Dataset maintenance through LoRA builds

Issue [#121](https://github.com/barnabyg/musubiGui/issues/121) is the umbrella review
for #108–#120. Its earlier input protection and job setup changes (#108–#115) were
integrated through PRs #122 and #123. The remaining work covers Run actions,
history traffic, caption navigation, sample comparison and gallery loading.

## Verification map

| Review requirement                                        | User-facing regression coverage                                                 |
| --------------------------------------------------------- | ------------------------------------------------------------------------------- |
| #108 Bind preparation to the reviewed revision            | `tests/browser/review-revision.spec.ts`                                         |
| #109 Surface pending and conflicting captions             | `tests/browser/job-captions.spec.ts`, `job-repair-writes.spec.ts`               |
| #110 Open the exact item needing repair                   | `tests/browser/job-repair.spec.ts`                                              |
| #111 Create a job from its dataset                        | `tests/browser/dataset-job.spec.ts`                                             |
| #112 Distinguish upload and registration locations        | `tests/browser/dataset-create.spec.ts`                                          |
| #113 Group required first-job choices                     | `tests/browser/compact-job.spec.ts`                                             |
| #114 Explain shared dataset changes                       | `tests/browser/dataset-impact.spec.ts`                                          |
| #115 Link jobs and frozen Runs                            | `tests/browser/job-run-links.spec.ts`                                           |
| #116 Explain queue cancellation and frozen-input retry    | `tests/browser/runActions.spec.ts`                                              |
| #117 Transfer summaries and avoid unchanged terminal logs | `tests/browser/runPolling.spec.ts`                                              |
| #118 Keep caption navigation and focus usable             | `tests/browser/dataset-preview.spec.ts`                                         |
| #119 Compare existing samples and their recorded inputs   | `tests/browser/sampleComparison.spec.ts`                                        |
| #120 Read captions once when opening the gallery          | `tests/python/test_dataset_gallery.py`, `tests/browser/dataset_gallery.spec.ts` |

Existing inline-caption, bulk-edit, dataset, Run and output tests cover the
preserved thumbnail/caption pairing, conflict recovery, bulk preview/restore,
immutable Run inputs and prominent final LoRA download. The canonical command and
all verification gates are documented in [verification.md](verification.md).

## Manual workflow

From the repository root in PowerShell 7, launch an isolated CPU fixture:

```powershell
.\acceptance.cmd --workspace ".cache/ux-check-$([guid]::NewGuid().ToString('N'))"
```

Open Mixed media and invalid item, preview blue.png, and use Alt+Right twice from
its Caption. Each advance should retain useful focus; Close preview should return
to the gallery. Repeat at a narrow width. Edit a caption, create a Wan or H3 job
from its dataset, save and review it. Repair any linked item error and return to
the same job. A caption conflict must be resolved or explicitly acknowledged
before preparing that dataset.

Complete a Wan Run, follow its originating job link, change the sample prompt,
save, review and complete the next revision. Compare the two Runs' samples and
recorded changes; each final LoRA download should identify its own revision.
Repeat with H3 playback. A build with sampling disabled must still be comparable.
Check [Runs](runs.md#queueing) for cancellation/retry scenarios,
[outputs](outputs.md) for the independent two-revision media fixture, and
[datasets](datasets.md) for measured 8/32/64 gallery scenarios. Network history
updates should transfer summaries while a completed Run's inputs and terminal log
stay unchanged.

These checks establish CPU-fixture control flow, persistence and browser playback.
GPU learning quality, audible H3 quality, screen-reader usability and comparative
human task times remain unmeasured; production qualification remains in #26.
