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
4. `## A｜Action：从输入到输出的逐阶段操作链`
5. `## R｜Result：实验结果、收益与证据`
6. `## 与我的研究方向的关联`
7. `## 局限与证据边界`
8. `## 原文摘要`

Metadata at the top must contain English title, authors, publication date, arXiv link, code/project status, category, and—when enabled—a Zotero PDF deep link.

STAR is a reasoning contract, not a cosmetic rename. Each part must be independently understandable:

- **Situation** (at least 300 non-whitespace characters): define the real research/application setting, relevant data or environment, the precise failure mode, its consequence, and why representative existing approaches do not solve it. Tie the failure to paper evidence rather than giving a generic field introduction.
- **Task** (at least 220 non-whitespace characters): define the paper's exact input and output, optimization/decision target, key constraints or assumptions, evaluation target, and what is outside scope. Explain what a successful solution must accomplish.
- **Action** (at least 1,000 non-whitespace characters for a Top recommendation; at least 700 otherwise): explain the actual method stage by stage using the standard below. Include training objectives, rewards, and key equations at the stage where they act, rather than isolating unexplained formulas in a detached section.
- **Result** (at least 300 non-whitespace characters): report the main quantitative and qualitative evidence. Every number must name the dataset/environment, metric and direction, comparison baseline, and experimental condition when available. Include the decisive ablation or failure case, then explain what the evidence establishes and what it does not.

If the source omits a requested detail, explicitly say that the paper does not report it. Never fill a length requirement with repeated background, raw abstract translation, or invented mechanisms.

## Action method-chain standard

The Action section must contain at least three numbered `### 阶段 N：...` headings. Use more when the method has more phases; do not compress seed creation, scoring, pruning, refinement, and final selection into one stage. For every stage explain:

- **输入**: exact data/object/state and its representation or shape when stated.
- **操作**: concrete module, algorithm, sampling/scoring/pruning rule, loss, reward, or update.
- **输出**: artifact passed downstream.
- **目的**: why this operation is necessary and which failure mode it fixes.
- **时机**: whether the operation belongs to data construction, initialization/pretraining, supervised training, post-training/RL, or inference.
- **证据**: section, figure, table, equation, algorithm, or explicit statement in the source.

After the numbered stages, include:

- `### 训练目标、奖励与关键公式`: write each relevant equation in LaTeX, define its variables, say which parameters it updates, and explain how it changes behavior. If no training is performed, state that and explain the inference-only rule.
- `### 训练与推理的差异`: list which modules/signals exist only during training and give the exact inference-time path.
- `### 贯穿全流程的具体样例`: trace one representative input through every stage, naming the intermediate artifacts and final output. Label any constructed illustration as an interpretation rather than a quotation from the paper.

Do not replace these items with “uses a multi-stage framework”, “improves quality”, or invented pseudo-formulas. Distinguish source facts from interpretation. If details are absent, say so.

Every paper JSON must contain a `method_stages` array with at least three objects using keys `name`, `input`, `operation`, `output`, `purpose`, `timing`, and `evidence`.

## Figures

Embed 2–4 original figures selected for explanatory value. Use a relative path such as `images/my-paper/figure-01.png`. Place each figure immediately after the relevant Situation, Action, or Result paragraph, not in a detached gallery. At least one available figure must appear inside Action. Its caption must identify the paper figure number, what the reader should inspect, and which claim or stage it supports. Never claim a rendered PDF page or self-drawn diagram is an original paper figure.

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
