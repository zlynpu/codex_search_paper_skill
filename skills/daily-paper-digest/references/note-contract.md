# Daily note contract

## Directory contract

```text
<archive>/YYYY/MM/DD/
├── digest.md
├── digest.json
├── JOB.md
├── <slug>.md
├── <slug>.json
├── images/<slug>/...
└── sources/<slug>/...
```

Each image folder belongs to exactly one paper. A paper note may reference only files listed in that paper JSON's `figures` array.

## Required paper-note sections

Use these exact headings:

1. `## 一句话总结`
2. `## 具体失败模式与已有方法为何不够`
3. `## 核心创新：从输入到输出的逐阶段操作链`
4. `## 训练目标、奖励或关键公式`
5. `## 关键指标与实验结论`
6. `## 与我的研究方向的关联`
7. `## 局限与证据边界`
8. `## 原文摘要`

Metadata at the top must contain English title, authors, publication date, arXiv link, code/project status, category, and—when enabled—a Zotero PDF deep link.

## Method-chain standard

The core-innovation section must contain at least three numbered `### 阶段 N：...` headings. Use more when the method has more phases. For every stage explain:

- **输入**: exact data/object/state and its representation or shape when stated.
- **操作**: concrete module, algorithm, sampling/scoring/pruning rule, loss, reward, or update.
- **输出**: artifact passed downstream.
- **目的**: why this operation is necessary and which failure mode it fixes.
- **证据**: section, figure, table, equation, algorithm, or explicit statement in the source.

Do not replace these items with “uses a multi-stage framework”, “improves quality”, or invented pseudo-formulas. Distinguish facts from interpretation. If details are absent, say so.

Top recommendations require at least 1,000 Chinese characters in the core section. Other notes require at least 700. Every paper JSON must contain a `method_stages` array with at least three objects using keys `name`, `input`, `operation`, `output`, `purpose`, and `evidence`.

## Figures

Embed 2–4 original figures selected for explanatory value. Use a relative path such as `images/my-paper/figure-01.png`. Explain what each figure demonstrates; do not use it as decoration. Never claim a rendered PDF page or self-drawn diagram is an original paper figure.

## Evidence and metrics

Record experimental setting and comparable baselines with every numeric result. Do not infer missing scores. Separate the paper's claims, reproduced facts, and your interpretation. Mention code availability only after checking a source URL supplied by the paper metadata.

## Digest

`digest.md` must include:

- configured date and search window;
- actual count and per-category counts;
- Top recommendations with a concrete reason;
- one table per enabled category linking to note Markdown and Zotero when present;
- a cross-paper synthesis;
- source failures or degraded paths.

The digest must not claim successful publication until verification and history finalization succeed.
