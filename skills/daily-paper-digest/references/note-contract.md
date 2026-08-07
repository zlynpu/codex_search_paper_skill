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
4. `## A｜Action：按论文 Method 逐部分翻译与解释`
5. `## R｜Result：实验结果、收益与证据`
6. `## 与我的研究方向的关联`
7. `## 局限与证据边界`
8. `## 原文摘要`

Metadata at the top must contain English title, authors, publication date, arXiv link, code/project status, category, and—when enabled—a Zotero PDF deep link.

STAR is a reasoning contract, not a cosmetic rename. Each part must be independently understandable:

- **Situation** (at least 300 non-whitespace characters): define the real research/application setting, relevant data or environment, the precise failure mode, its consequence, and why representative existing approaches do not solve it. Tie the failure to paper evidence rather than giving a generic field introduction.
- **Task** (at least 220 non-whitespace characters): define the paper's exact input and output, optimization/decision target, key constraints or assumptions, evaluation target, and what is outside scope. Explain what a successful solution must accomplish.
- **Action**: mirror the paper's actual Method structure and translate plus explain every Method part using the standard below. Detail scales with the paper: do not pad a one-part method into three stages or collapse a multi-part method into a short summary.
- **Result** (at least 300 non-whitespace characters): report the main quantitative and qualitative evidence. Every number must name the dataset/environment, metric and direction, comparison baseline, and experimental condition when available. Include the decisive ablation or failure case, then explain what the evidence establishes and what it does not.

If the source omits a requested detail, explicitly say that the paper does not report it. Never fill a length requirement with repeated background, raw abstract translation, or invented mechanisms.

## Action Method-part standard

Determine the outline from the paper's Method section before drafting. Map each explicit first-level Method subsection or named method component to one note section, preserving the original order. Use this heading:

`### 方法部分 N：中文标题（Original Method Heading）`

If Method has one part, write one section. If it has N parts, write N sections. Do not invent, split, or merge parts to satisfy an arbitrary count. Do not count Related Work, Experiments, or Implementation Details as Method parts unless the paper itself places them inside Method as a substantive component.

Every Method part must be at least 220 non-whitespace characters and contain only the two required explanatory blocks:

- **翻译**: faithfully translate and restate this Method part in Chinese. Translate its heading and key method content while preserving technical terms, symbols, equations, and stated conditions. Do not copy long passages verbatim.
- **解释**: explain how this part operates, why it is needed, and how it connects to adjacent parts in language understandable without reading the paper. When present in the source, cover its concrete input, operation, output, objective, formulas/loss/reward, training or inference timing, and downstream interface. Cite the supporting section, figure, table, equation, or algorithm inside this explanation.

Place each training objective, reward, key equation, and training/inference distinction inside the Method part where it appears. Define variables, state which parameters are updated, and explain the behavioral effect. If a part is inference-only or the paper omits an implementation detail, say so. Do not add detached boilerplate subsections or invented pseudo-formulas.

Every paper JSON must contain a `method_stages` array with exactly one object per Method part, despite the legacy field name. Each object uses keys `name`, `source_heading`, `translation`, `explanation`, and `evidence`. Its length must equal the number of `### 方法部分 N：...` sections in Markdown.

## Figures

Embed 2–4 original figures selected for explanatory value. Use a relative path such as `images/my-paper/figure-01.png`. Place each figure immediately after the relevant Situation, Action, or Result paragraph, not in a detached gallery. At least one available figure must appear inside Action. Its caption must identify the paper figure number, what the reader should inspect, and which claim or Method part it supports. Never claim a rendered PDF page or self-drawn diagram is an original paper figure.

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
