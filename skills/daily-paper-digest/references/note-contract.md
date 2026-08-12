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

Use these exact headings and order:

1. `## 一句话总结`
2. `## S｜Situation：研究情境与具体失败模式`
3. `## T｜Task：论文要解决的任务与约束`
4. `## A｜Action：把论文方法完整走一遍`
5. `## R｜Result：实验结果、收益与证据`
6. `## 与我的研究方向的关联`
7. `## 局限与证据边界`
8. `## 原文摘要`

Metadata at the top must contain English title, authors, publication date, arXiv link, code/project status, category, and—when enabled—a Zotero PDF deep link.

STAR is a reasoning contract, not a cosmetic rename. Each part must be independently understandable:

- **Situation** (at least 220 non-whitespace characters): define the real research/application setting, relevant data or environment, the precise failure mode, its consequence, and why representative existing approaches do not solve it. Turn the checked source facts into a natural problem narrative; do not narrate how those facts were checked.
- **Task** (at least 160 non-whitespace characters): define the paper's exact input and output, optimization/decision target, key constraints or assumptions, evaluation target, and what is outside scope. Explain what a successful solution must accomplish.
- **Action**: write a cohesive knowledge-blog explanation that mirrors the paper's actual Method structure and makes the complete operation chain reproducible from the note alone. Detail scales with the paper: do not pad a one-part method into three stages or collapse a multi-part method into a short summary.
- **Result** (at least 220 non-whitespace characters): report the main quantitative and qualitative evidence. Every number must name the dataset/environment, metric and direction, comparison baseline, and experimental condition when available. Include the decisive ablation or failure case, then explain what the evidence establishes and what it does not.

Source checking, exact translation, section lookup, and figure/equation provenance are authoring work. Keep them in the structured JSON evidence fields, not in reader-facing prose. Omit an unavailable optional metadata item instead of writing an extraction failure. Never fill a length requirement with repeated background, an English abstract, or invented mechanisms.

The final Markdown must read as an independently written Chinese knowledge blog. It must not contain process commentary such as “原文操作证据为”“原文证据位于”“可核对的转换文本”“官方 HTML”“对应依据为”, raw source quotations, extraction-tool failures, or claims that the author is checking the paper for the reader. English technical names, symbols, and original Method headings may remain where they improve precision. The content under `## 原文摘要` must be a fluent Chinese translation or faithful condensation, never a pasted English abstract.

## Action Method-part standard

Determine the outline from the paper's Method section before drafting. Map each explicit first-level Method subsection or named method component to one note section, preserving the original order. Use this heading:

`### 方法部分 N：中文标题（Original Method Heading）`

If Method has one part, write one section. If it has N parts, write N sections. Do not invent, split, or merge parts to satisfy an arbitrary count. Do not count Related Work, Experiments, or Implementation Details as Method parts unless the paper itself places them inside Method as a substantive component.

Before the first Method part, give a concrete end-to-end workflow and introduce one running example. Reuse that example across stages so inputs, intermediate states, decisions, candidate outputs, and accept/reject conditions remain connected.

Every Method part must be at least 450 non-whitespace characters. Write continuous explanatory prose in which the translated meaning, operational detail, motivation, and intuition are fused; never write `翻译：`, `解释：`, alternating role paragraphs, source-audit commentary, or vague module boilerplate. Recover every explicit nested Method subsection and each distinct named operation. Give each one a natural `####` heading and at least 180 non-whitespace characters covering:

- the actual input objects and their provenance;
- the ordered operations, branches, thresholds, gates, or update rules;
- the exact output and which next component consumes it;
- why the submodule exists and which failure it prevents;
- a concrete continuation of the running example.

Place each necessary formula, training objective, reward, and selection rule at the point where the corresponding operation is explained. Render it in LaTeX, define every variable, state the calculation order and behavioral role, and add plain-language intuition. Do not copy formulas merely to look complete. When no key formula exists, explain the real discrete procedure directly; do not add a detached “no formula” boilerplate section. Explicitly distinguish what happens during training/offline evolution and runtime inference, what parameters or artifacts change, and what is frozen or removed. Do not interrupt the explanation with a list of unreported implementation details; mention a missing detail only when it materially changes how the result should be interpreted.

Every paper JSON must contain a `method_stages` array with exactly one object per first-level Method part. Each object keeps `name`, `source_heading`, `translation`, `explanation`, and `evidence` for compatibility and additionally requires `overview`, `walkthrough`, `submodules`, `equations`, and `equation_note`. `submodules` contains one object for every nested subsection or distinct named operation, with `name`, `source_heading`, `input`, `operations`, `output`, `purpose`, and `evidence`; `operations` is an ordered array with at least two concrete steps. `equations` contains objects with `latex`, `variables`, `role`, `intuition`, and `evidence`. The Method-part count and submodule headings in Markdown must match these arrays.

Reject generic filler such as “the upstream information is transformed and passed to the next module,” as well as universal cross-domain bundles such as “视觉、语言、轨迹或潜状态.” Name this paper's actual data structures, modules, decisions, and interfaces. Repetition of the same paragraph across different Method parts is a failure, not acceptable padding. Completeness means covering every real component and transition, not making the note long through repetition.

## Figures

Embed 2–4 original figures selected for explanatory value. Use a relative path such as `images/my-paper/figure-01.png`. Place each figure immediately after the relevant Situation, Action, or Result paragraph, not in a detached gallery. At least one available figure must appear inside Action. Its Chinese caption must identify the paper figure number, what the reader should inspect, and which claim or Method part it supports. Captions must not expose source URLs or say that the figure is “用于核对”. Never claim a rendered PDF page or self-drawn diagram is an original paper figure.

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
