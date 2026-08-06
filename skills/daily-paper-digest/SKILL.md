---
name: daily-paper-digest
description: "Build or operate a configurable daily research-paper digest: search recent papers by user-defined categories and quotas, deduplicate past pushes, download each paper's own figures into isolated folders, write detailed Chinese Markdown notes, validate them, add Zotero deep links, and run on a schedule. Use for daily paper pushes, paper radar/digest automation, arXiv monitoring, research-note generation, Zotero routing, missed-run recovery, or changing delivery time, timezone, topics, ratios, counts, and archive paths."
---

# Daily Paper Digest

Create an evidence-grounded daily digest from the user's configuration. Never hardcode a date, home directory, category list, quota, or schedule.

## Locate the runtime

Set `SKILL_DIR` to the directory containing this file. Use the bundled Python scripts with the active Python 3 interpreter. On Windows, use `py -3` when `python` is unavailable.

The default config is printed by:

```text
python <SKILL_DIR>/scripts/configure.py path
```

If the user requests a configuration change, run `configure.py` first, then reinstall/reset the OS schedule with the repository installer or scheduler described in [configuration.md](references/configuration.md). Do not silently retain an old scheduled task.

## Daily workflow

1. Validate configuration:

   ```text
   python <SKILL_DIR>/scripts/configure.py validate --config <CONFIG>
   ```

2. Prepare candidates and source material:

   ```text
   python <SKILL_DIR>/scripts/prepare_digest.py --config <CONFIG> --date YYYY-MM-DD
   ```

   This step searches, removes known arXiv IDs and normalized duplicate titles, fills configured category quotas, and creates `<archive>/YYYY/MM/DD/` with `digest.json`, one JSON source record per paper, `sources/<slug>/`, and isolated `images/<slug>/` folders.

3. Read `<day>/JOB.md`, `<day>/digest.json`, and every selected paper JSON. Write exactly one `<slug>.md` per paper plus `<day>/digest.md`. Update each paper JSON with the analytical fields required by [note-contract.md](references/note-contract.md).

4. Explain the method as an operation chain, not as slogans. For every paper, reconstruct at least three stages from input/data construction through training or optimization to inference/output. Every stage must state:

   - its concrete input and representation;
   - the operation, module, objective, or update actually applied;
   - the resulting output passed to the next stage;
   - why the stage exists and what failure it addresses;
   - the paper evidence supporting the claim.

   Top recommendations require extra depth. If a paper has multiple phases such as seed generation, scoring, pruning, refinement, and final selection, explain each phase separately, including what is retained or discarded and how the next phase consumes the result.

5. Use only figures listed in that paper's JSON. Markdown image paths for paper `<slug>` must start with `images/<slug>/`; never reuse another paper's image or move all images into a shared flat namespace. Embed 2–4 semantically relevant original figures unless the source record explicitly records that fewer exist.

6. Validate before publishing:

   ```text
   python <SKILL_DIR>/scripts/verify_digest.py --config <CONFIG> --date YYYY-MM-DD
   ```

7. If Zotero is enabled, start Zotero and link/import the papers:

   ```text
   python <SKILL_DIR>/scripts/zotero_bridge.py link --config <CONFIG> --date YYYY-MM-DD
   ```

   Then rerun verification. The Markdown link must open the attached PDF through `zotero://open-pdf/...`, and the item must be assigned to the configured top collection and category child collection.

8. Commit the deduplication history only after every required check passes:

   ```text
   python <SKILL_DIR>/scripts/verify_digest.py --config <CONFIG> --date YYYY-MM-DD --finalize
   ```

Never update history for a partial or invalid digest. A failed run must remain retryable.

## Unattended run

For a configured harness, use:

```text
python <SKILL_DIR>/scripts/run_daily.py --config <CONFIG> --force
```

OS schedulers call the same script with `--if-due`; it evaluates the configured IANA timezone and time, prevents overlaps, retries failed days, and runs at most once successfully per configured day.

## Failure handling

- If one source is unavailable, try the bundled arXiv API/RSS/HTML fallbacks and another ranked candidate in the same category.
- If a category cannot meet its quota, stop with a candidate report. Do not silently change the user's allocation unless `selection.allow_quota_rebalance` is enabled.
- If original figures cannot be associated with the current paper, reject that candidate when the configured minimum is not met.
- If Zotero is unavailable, keep the verified notes but do not finalize when Zotero is required.
- If today was missed, run `run_daily.py --force --date YYYY-MM-DD`; deduplication still applies.

Read [configuration.md](references/configuration.md) for all user-facing options and [note-contract.md](references/note-contract.md) before drafting notes.
