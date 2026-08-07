---
name: daily-paper-digest
description: "Build or operate a configurable daily research-paper digest: search recent papers by user-defined categories and quotas, deduplicate past pushes, download each paper's own figures into isolated folders, write detailed Chinese Markdown notes with the STAR method, validate them, add Zotero deep links, and run on a schedule. Use for daily paper pushes, paper radar/digest automation, arXiv monitoring, research-note generation, Zotero routing, missed-run recovery, or changing delivery time, timezone, topics, ratios, counts, and archive paths."
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

3. Read `<day>/JOB.md`, `<day>/digest.json`, every selected paper JSON, and the source files referenced by that JSON. Write exactly one `<slug>.md` per paper plus `<day>/digest.md`. Update each paper JSON with the analytical fields required by [note-contract.md](references/note-contract.md).

4. Organize every paper note with STAR. Treat STAR as an analytical structure, not four short labels:

   - **Situation**: establish the application/research setting, relevant inputs or environment, the concrete failure mode, and why representative prior approaches are insufficient.
   - **Task**: state the exact problem the paper solves, input/output contract, optimization or decision objective, constraints, evaluation targets, and scope boundaries.
   - **Action**: reconstruct the proposed method as an operation chain from actual input/data construction through training or optimization to inference/output. This is the most detailed section.
   - **Result**: report evidence with metric direction, dataset/environment, baseline, and comparison context; include ablations or qualitative findings when available and separate results from interpretation.

5. In **Action**, mirror the paper's own Method structure. If Method has N explicit first-level subsections or named components, write exactly N `### 方法部分 N：中文标题（Original Heading）` sections in the same order. If Method is one unified part, write one part. Never split or merge parts merely to reach a target count. For every part:

   - write **翻译**: translate the original heading and faithfully restate that Method part in Chinese;
   - write **解释**: explain in plain language how it works, why it is needed, and how it connects to adjacent parts;
   - write **公式与直觉**: preserve every necessary formula from that part, define every variable, explain the calculation and its optimization/inference role, then give a plain-language intuition; if the part has no key formula, explicitly say so instead of inventing one;
   - inside the explanation, cover concrete inputs, operations, outputs, losses, rewards, selection rules, and training/inference timing when the paper states them;
   - cite the source heading plus the supporting section, figure, table, equation, or algorithm.

   Treat translation as faithful Chinese restatement, not unexplained literal copying. Preserve technical terms and equations, then explain them so a reader can follow the operation without opening the paper. When the paper itself defines separate phases such as seed generation, scoring, pruning, refinement, and final selection, keep those same separate parts; when it groups them under one Method part, preserve that grouping and explain the internal flow there.

6. Use only figures listed in that paper's JSON. Markdown image paths for paper `<slug>` must start with `images/<slug>/`; never reuse another paper's image or move all images into a shared flat namespace. Embed 2–4 semantically necessary original figures unless the source record explicitly records that fewer exist. Put each figure immediately after the STAR claim it explains, with a Chinese caption that says what to inspect and why it supports that claim. At least one available figure must appear inside **Action**; use Result figures for quantitative or qualitative evidence when useful.

7. Validate before publishing:

   ```text
   python <SKILL_DIR>/scripts/verify_digest.py --config <CONFIG> --date YYYY-MM-DD
   ```

8. If Zotero is enabled, start Zotero and link/import the papers:

   ```text
   python <SKILL_DIR>/scripts/zotero_bridge.py link --config <CONFIG> --date YYYY-MM-DD
   ```

   Then rerun verification. The Markdown link must open the attached PDF through `zotero://open-pdf/...`, and the item must be assigned to the configured top collection and category child collection.

9. Commit the deduplication history only after every required check passes:

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
