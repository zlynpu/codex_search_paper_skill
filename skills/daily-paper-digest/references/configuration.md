# Configuration reference

The runtime uses one UTF-8 JSON file. Paths support `~` and environment variables. Relative paths resolve from the config file's directory.

## Schedule

- `schedule.time`: local wall-clock time in `HH:MM`.
- `schedule.timezone`: IANA timezone such as `Asia/Shanghai`, `Europe/London`, or `America/Los_Angeles`.
- `schedule.catch_up`: run later the same configured day if the exact minute was missed.
- `schedule.retry_interval_minutes`: minimum delay before retrying a failed run.

The installed OS task wakes once per minute; `run_daily.py --if-due` performs timezone and once-per-day checks. Editing the time or timezone therefore does not require rewriting platform-specific time math, but run the scheduler install command after material configuration changes so stale tasks are repaired.

## Topics and allocation

`digest.total_papers` is the daily total. `digest.categories` is an ordered list. Each enabled category contains:

- `key`: stable lowercase identifier used in JSON and folder mapping.
- `label`: display name.
- `quota`: exact daily count. If every enabled category has a quota, their sum must equal `total_papers`.
- `weight`: alternative proportional allocation. Use weights only when quotas are omitted for every enabled category; the runtime converts weights to integer quotas with the largest-remainder method.
- `search_terms`: user-controlled phrases matched against title and abstract.
- `arxiv_categories`: source categories such as `cs.CV`, `cs.AI`, or `cs.RO`.
- `negative_terms`: optional exclusions in addition to global exclusions.
- `minimum_relevance_score`: optional category-specific score floor. Use it to keep a high-confidence specialty lane from accepting a paper merely because it shares an arXiv source category.
- `featured`: optional fixed recommendation lane with:
  - `label`: standalone `digest.md` section heading;
  - `selection_priority`: integer from 0–100; higher lanes claim overlapping candidates first;
  - `confidence`: `normal`, `high`, or `highest` editorial confidence tier;
  - `outside_top_recommendations`: keep this lane separate from ordinary Top recommendations;
  - `require_detailed_note`: require the complete note contract rather than a shortened specialty blurb.
- `zotero_collection`: child collection name under `zotero.top_collection`.

Arbitrary categories are allowed. Do not assume the bundled example categories are mandatory.

Examples:

```text
python configure.py set --config CONFIG --time 08:30 --timezone Asia/Shanghai
python configure.py set --config CONFIG --total 15 \
  --quota generation=3 --quota understanding=4 \
  --quota agentic_rl=3 --quota embodied_vla_wam=3 \
  --quota creative_design_aigc=1 --quota others=1
python configure.py set --config CONFIG \
  --terms agentic_rl="agentic reinforcement learning|tool-use agent|RLVR"
```

For a new category, featured lane, or advanced edit, modify the JSON directly and run `configure.py validate`. A featured lane with `outside_top_recommendations: true` consumes its category quota but does not consume a Top recommendation slot.

## Selection and archive

- `archive.root`: final archive root. Each run goes to `YYYY/MM/DD` under it.
- `archive.history_file`: persistent pushed-paper index. Defaults under the archive root when empty.
- `selection.lookback_days`: recency window.
- `selection.max_results_per_source`: source scan depth.
- `selection.minimum_original_figures`: minimum paper-owned figures required to select a candidate.
- `selection.maximum_original_figures`: maximum figures downloaded and offered to the note writer.
- `selection.allow_quota_rebalance`: if true, shortages may be filled by the best remaining candidates from other enabled categories. The actual category is still recorded.
- `selection.additional_history_files`: optional Hermes or older indexes to include in deduplication. Missing files are ignored.

Deduplication removes arXiv version suffixes and compares normalized titles. It scans both the persistent index and archived `digest.json` files before selection.

## Agent harness

- `agent.harness`: `codex`, `claude-code`, `qoder`, `none`, or `custom`.
- `agent.executable`: optional executable override.
- `agent.custom_command`: argument array for a custom harness. Placeholders: `{prompt}`, `{prompt_file}`, `{workspace}`, `{config}`, `{date}`.
- `agent.timeout_minutes`: upper bound for the note-writing process.

`none` prepares source material only. Other harnesses run non-interactively in the day's archive folder.

## Zotero

- `zotero.enabled`: enable import and deep links.
- `zotero.required`: block finalization if Zotero linking fails.
- `zotero.database`: optional explicit `zotero.sqlite`; empty enables platform detection.
- `zotero.top_collection`: top-level collection name.
- Per-category `zotero_collection`: child collection mapping.
- `zotero.launch`: attempt to launch Zotero when its local connector is unavailable.

Create/repair the collection tree while Zotero is closed:

```text
python zotero_bridge.py setup-collections --config CONFIG
```

The command refuses to edit a running Zotero database, makes a timestamped backup, and performs one transaction.
