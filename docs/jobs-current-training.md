# Training from the Job page

The Job page is where a Job's training is started, watched, stopped and resumed. [Jobs](jobs.md)
lists the other job topics.

## Current training

Once a Job has a Run, its page shows **Current training** below the action bar: the Job's
latest training, which is the chain of resumed segments whose latest segment was created most
recently. It shows the state, the segment and revision, a link to **Open Run details**, the
step progress (Step X/N, elapsed, ETA and estimated time remaining) and, while it trains,
**Save now**, **Sample now**, **Save and stop**, **Stop now** and **Force terminate**. These
are the same controls, progress and requests as the Actions panel in Run details, read from the
same Run, so both pages show the same step. Below them are the training's newest preview and
LoRA checkpoint with a download link, from every segment's files in step order; Run details
lists them all.

**Prepare and start** stays on the Job page, which then follows the new training.
**Prepare run** still opens Run details, where the prepared Run is started.

## Start resumes implicitly

When the current training has ended (stopped, completed, failed, cancelled or interrupted) and
one of its segments has a saved state, the panel says where training stopped and where its last
state was saved, and **Training steps** reads as a total: `Training steps (total; training is
at step 300)`. The saved step comes from the state folder's name (`-step00000300-state`, or the
target for a completed Run's final state) until Inspect saved states records it; paired Wan
states show it only after inspection. Reading it never hashes or probes states.

Every field a resumed training can't take is marked **Needs a new Run: changing this starts a
fresh training.** It stays editable. Save optimizer/training state is marked only while it is off
for a training that kept state: turning it on always resumes. The fields and tiers are listed in
[Resuming with an edited Job revision](runs-saved-state.md#resuming-with-an-edited-job-revision).

**Prepare and start** on the reviewed revision then decides:

- **Resume.** It checks the saved states and compares the reviewed revision with the one that
  trained them. When the revision can take it, it resumes the latest valid state as a new
  segment until the Job's Training steps, with the revision's settings, and says
  `Resuming from step 300 until step 600`. Numbering continues across segments. A plain resume
  asks nothing; one with warned changes (learning rate, timestep strategy and the like) or a
  dataset re-prepare first shows a confirmation with the work and, for warned changes, the
  acknowledgement.
- **Start fresh.** When the revision can't resume, for example after changing LoRA rank, or
  when Training steps isn't after the saved step, Start asks **Start a fresh training?** It
  names each changed field that needs a new Run with its old and new value, and any other
  reason. **Start fresh** prepares and starts a new Run from step 0; **Keep editing** changes
  nothing. The previous training stays in Runs.
- **Prepare and start as before** when there is no saved state, or the current training is
  still preparing, queued or training.

The resumed state belongs to the current training's line: the most recently created ended
segment with a saved state, so a segment that failed before saving resumes from the state it
started from. An older training of the same Job is never resumed instead.

`GET /api/jobs/{id}/training` returns the current training's latest segment `run_id`, its
`chain_id` and `status`, `resume` (`run_id`, `run_status`, `saved_step`, or null when Start
would not resume), `resumable_fields`, the fields a resumed segment takes at any value, and
`resumable_values`, the values at which it takes the others that depend on the value, such as
`{"save_state": [true]}` for a training that kept state. Job review's
Resume compatibility compares against the same Run.

## Manual check

From the repository root in PowerShell 7:

```powershell
.\acceptance.cmd --workspace ".cache/job-training-$([guid]::NewGuid().ToString('N'))"
```

This CPU fixture needs no models or GPU. Open WAN acceptance 1 and on Training set 200
training steps; on Checkpoints set save every 200 steps and enable Save optimizer/training
state. Choose Review configuration, then **Prepare and start**.

1. The page stays on the Job. Current training shows Training and Step N/200 rising. Open Run
   details in a second tab: its step matches.
2. Choose **Save and stop** on the Job page. Expect Stopped, a note that training stopped at
   step N with its last state saved at step N, and the steps label
   `Training steps (total; training is at step N)`. LoRA rank shows the Needs a new Run hint.
3. Set Training steps to 210 and change the sample prompt. Review, then **Prepare and start**.
   Expect no dialog, `Resuming from step N until step 210`, then Segment 2 of 2 completing at
   Step 210/210 without leaving the Job page.
4. Set LoRA rank to 16, review and **Prepare and start**. Expect **Start a fresh training?**
   listing `LoRA rank: 8 → 16`. Keep editing creates nothing; Start fresh starts a new Run,
   and the stopped training stays in Runs.
5. At 375 px wide, the page has no horizontal scroll.
