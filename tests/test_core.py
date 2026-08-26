from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = ROOT / "skills" / "daily-paper-digest" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

from common import (  # noqa: E402
    ConfigError,
    allocate_quotas,
    atomic_write_json,
    normalize_arxiv_id,
    normalize_title,
    validate_config,
)
from prepare_digest import (  # noqa: E402
    apply_recommendation_metadata,
    parse_atom,
    parse_recent_html,
    score_for_category,
    top_recommendation_ids,
)
from run_daily import due, harness_command  # noqa: E402
from verify_digest import finalize, verify  # noqa: E402


def template() -> dict:
    return json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))


class ConfigurationTests(unittest.TestCase):
    def test_default_allocation_matches_latest_ratio(self):
        self.assertEqual(
            allocate_quotas(template()),
            {
                "generation": 3,
                "understanding": 4,
                "agentic_rl": 3,
                "embodied_vla_wam": 3,
                "creative_design_aigc": 1,
                "others": 1,
            },
        )

    def test_weight_allocation_uses_largest_remainder(self):
        config = template()
        config["digest"]["total_papers"] = 7
        for category, weight in zip(config["digest"]["categories"], (4, 1, 1, 1, 1, 1)):
            category.pop("quota")
            category["weight"] = weight
        self.assertEqual(sum(allocate_quotas(config).values()), 7)
        self.assertEqual(allocate_quotas(config)["generation"], 3)

    def test_bad_quota_sum_is_rejected(self):
        config = template()
        config["digest"]["categories"][0]["quota"] = 99
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ConfigError):
                validate_config(config, Path(temporary) / "config.json")

    def test_normalization_removes_arxiv_version_and_title_punctuation(self):
        self.assertEqual(normalize_arxiv_id("https://arxiv.org/abs/2608.01234v3"), "2608.01234")
        self.assertEqual(normalize_title("Seed: A Method!"), normalize_title("seed a method"))


class SearchTests(unittest.TestCase):
    def test_atom_parser_and_category_score(self):
        body = b"""<?xml version='1.0'?>
        <feed xmlns='http://www.w3.org/2005/Atom'>
          <entry><id>http://arxiv.org/abs/2608.01234v2</id>
          <updated>2026-08-06T01:00:00Z</updated><published>2026-08-05T01:00:00Z</published>
          <title>Agentic Reinforcement Learning for Tool Use</title>
          <summary>A long-horizon agent learns with verifiable reward.</summary>
          <author><name>Alice Example</name></author>
          <link rel='alternate' href='https://arxiv.org/abs/2608.01234v2'/>
          <link rel='related' href='https://arxiv.org/pdf/2608.01234v2'/></entry>
        </feed>"""
        record = parse_atom(body, "cs.AI")[0]
        self.assertEqual(record["arxiv_id"], "2608.01234")
        category = template()["digest"]["categories"][2]
        self.assertGreater(score_for_category(record, category, []), 0)

    def test_recent_html_fallback_pairs_each_title_with_its_own_id(self):
        body = b"""
        <dt><a href='/abs/2608.01234' title='Abstract'>arXiv:2608.01234</a></dt>
        <dd><div class='list-title mathjax'><span class='descriptor'>Title:</span> First Agent Paper </div>
        <div class='list-authors'><a>Alice</a>, <a>Bob</a></div></dd>
        <dt><a href='/abs/2608.05678v2' title='Abstract'>arXiv:2608.05678</a></dt>
        <dd><div class='list-title'><span class='descriptor'>Title:</span> Second VLA Paper </div></dd>
        """
        records = parse_recent_html(body, "cs.AI")
        self.assertEqual([record["arxiv_id"] for record in records], ["2608.01234", "2608.05678"])
        self.assertEqual(records[0]["title"], "First Agent Paper")

    def test_design_lane_uses_strict_relevance_and_stays_outside_top_three(self):
        config = template()
        categories = {
            category["key"]: category for category in config["digest"]["categories"]
        }
        design = categories["creative_design_aigc"]
        relevant = {
            "title": "A Creative Agent for Editable Interface Design",
            "abstract": "Human-AI co-creation uses a design agent and a revision loop.",
            "source_categories": ["cs.HC"],
            "published": "",
        }
        unrelated = {
            "title": "A Benchmark for General Human Computer Interaction",
            "abstract": "The study evaluates interaction techniques.",
            "source_categories": ["cs.HC"],
            "published": "",
        }
        self.assertGreaterEqual(
            score_for_category(relevant, design, []), design["minimum_relevance_score"]
        )
        self.assertLess(
            score_for_category(unrelated, design, []), design["minimum_relevance_score"]
        )

        papers = [
            {"arxiv_id": "1", "channel": "creative_design_aigc", "selection_score": 99.0},
            {"arxiv_id": "2", "channel": "generation", "selection_score": 50.0},
            {"arxiv_id": "3", "channel": "understanding", "selection_score": 40.0},
            {"arxiv_id": "4", "channel": "agentic_rl", "selection_score": 30.0},
        ]
        for paper in papers:
            apply_recommendation_metadata(paper, categories[paper["channel"]])
        top_ids = top_recommendation_ids(papers, categories, 3)
        self.assertEqual(top_ids, {"2", "3", "4"})
        self.assertTrue(papers[0]["special_recommendation"])
        self.assertEqual(papers[0]["recommendation_confidence"], "highest")


class RunnerTests(unittest.TestCase):
    def test_due_respects_configured_time_and_completion(self):
        config = template()
        state = {}
        before = datetime(2026, 8, 6, 8, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
        after = datetime(2026, 8, 6, 9, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertFalse(due(config, state, before)[0])
        self.assertTrue(due(config, state, after)[0])
        state["last_completed_date"] = "2026-08-06"
        self.assertFalse(due(config, state, after)[0])

    def test_harness_commands_are_platform_safe_argument_arrays(self):
        config = template()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "config.json"
            day = root / "2026/08/06"
            day.mkdir(parents=True)
            config["agent"]["executable"] = "codex-test"
            command, stdin = harness_command(config, config_path, day, datetime(2026, 8, 6).date(), "prompt")
            self.assertEqual(command[0], "codex-test")
            self.assertEqual(command[-1], "-")
            self.assertEqual(stdin, "prompt")


class VerificationTests(unittest.TestCase):
    def test_valid_digest_finalizes_idempotently(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = template()
            config["archive"]["root"] = str(root / "archive")
            config["digest"]["total_papers"] = 1
            config["digest"]["top_recommendations"] = 0
            config["digest"]["categories"] = [copy.deepcopy(config["digest"]["categories"][0])]
            config["digest"]["categories"][0]["quota"] = 1
            config["agent"]["harness"] = "none"
            config["zotero"]["enabled"] = False
            config["zotero"]["required"] = False
            config_path = root / "config.json"
            atomic_write_json(config_path, config)
            validate_config(config, config_path)
            day = root / "archive/2026/08/06"
            image_dir = day / "images/seed-paper"
            image_dir.mkdir(parents=True)
            Image.new("RGB", (64, 64), "white").save(image_dir / "figure-01.png")
            Image.new("RGB", (64, 64), "gray").save(image_dir / "figure-02.png")
            stages = [
                {
                    "name": "种子筛选与精炼",
                    "source_heading": "Seed Selection and Refinement",
                    "translation": "论文把候选种子的评分、保留、丢弃与后续精炼定义为一个统一的方法部分，并明确各操作的先后关系。",
                    "explanation": "通俗地说，这一部分像一条带质量门控的流水线：候选种子和任务上下文先按论文阈值评分，低质量项被丢弃，保留项再精炼成下游训练样本。它发生在数据构造和训练准备阶段，推理时不再执行，并通过原文算法与图表给出依据。",
                    "overview": "这一部分把原始候选变成带质量标记、可由下游训练直接消费的精炼样本。",
                    "walkthrough": "以候选种子 A 为贯穿案例：系统先读取候选内容与任务上下文，计算原始分数，再映射到有界质量分数；通过阈值后保留并补齐来源、评分和精炼结果，未通过则记录淘汰原因。通过者随后被清理冗余内容、补足必要上下文并写入可追溯状态，最终训练器只接收已经通过门控的候选集合。这个过程同时标明各步的输入、决策分支、中间状态和下游接口。",
                    "evidence": "Method section Seed Selection and Refinement, algorithm and figure",
                    "submodules": [
                        {
                            "name": "种子评分、门控与精炼",
                            "source_heading": "Scoring, Gating, and Refinement",
                            "input": "原始候选种子、任务上下文、候选的原始质量分数与筛选阈值。",
                            "operations": [
                                "读取候选和上下文，按照论文给出的评分函数计算每个候选的原始质量分数。",
                                "把原始分数映射为有界质量分数，并与门控阈值比较以决定保留或丢弃。",
                                "对保留候选执行内容精炼，并附加来源、质量分数与可追溯的筛选状态。",
                            ],
                            "output": "通过门控且完成精炼的候选集合，以及每个候选对应的评分与筛选记录。",
                            "purpose": "在数据进入训练器之前阻断低质量候选，同时保留足够的来源信息来解释每个样本为何被接受。",
                            "evidence": "Method equation 1, Algorithm 1, and Figure 1",
                        }
                    ],
                    "equations": [
                        {
                            "latex": "q_i = s_i / (1 + s_i)",
                            "variables": "q_i is the bounded quality and s_i is the raw candidate score",
                            "role": "the bounded score determines whether a candidate passes the training-data gate",
                            "intuition": "large raw scores approach one while weak candidates remain below the threshold",
                            "evidence": "Method equation 1",
                        }
                    ],
                    "equation_note": "",
                }
            ]
            paper = {
                "arxiv_id": "2608.01234",
                "title": "Seed Paper",
                "authors": ["A. Author"],
                "published": "2026-08-05",
                "paper_url": "https://arxiv.org/abs/2608.01234",
                "pdf_url": "https://arxiv.org/pdf/2608.01234",
                "channel": "generation",
                "slug": "seed-paper",
                "top_recommendation": False,
                "special_recommendation": False,
                "special_recommendation_label": "",
                "recommendation_confidence": "normal",
                "recommendation_priority": 0,
                "method_stages": stages,
                "figures": [
                    {
                        "path": f"images/seed-paper/figure-0{index}.png",
                        "caption": "original figure",
                        "source_url": f"https://arxiv.org/html/2608.01234/figure-{index}.png",
                        "source_kind": "original-paper-figure",
                    }
                    for index in (1, 2)
                ],
            }
            atomic_write_json(day / "seed-paper.json", paper)
            core = (
                "### 方法部分 1：种子筛选与精炼（Seed Selection and Refinement）\n\n"
                "这一部分不是孤立的打分器，而是候选进入训练集前的一条完整质量控制链。它接收候选文本、任务上下文、"
                "评分信号和门控阈值，依次完成评分、决策、精炼与记录；输出既包括可以训练的候选，也包括可追溯的筛选状态。"
                "因此读者可以沿着同一个候选观察它从原始输入变成训练样本，而不是只看到一个没有上下游的公式。\n\n"
                "#### 种子评分、门控与精炼\n\n"
                "以候选种子 A 为例，它最初只是模型生成的一段回答，并不知道是否真的符合任务目标。第一步，系统把 A 和任务上下文"
                "一起送入论文定义的评分过程，得到原始分数 $s_i$；这里上下文决定评分针对什么目标，候选本身则提供需要检查的内容。"
                "第二步，论文用 $q_i=s_i/(1+s_i)$ 把原始分数映射为有界质量分数 $q_i$。当 $s_i$ 增大时，$q_i$ 越接近 1，"
                "但不会无限放大；这相当于把尺度不稳定的原始成绩换算到统一量尺，避免个别极端分数直接支配筛选。\n\n"
                "得到 $q_i$ 后，系统把它与论文给定的阈值比较。若 A 低于阈值，就把它从训练候选中移除，同时记录它在哪个门上失败；"
                "若 A 通过，则进入精炼步骤。精炼不是重新发明一个样本，而是在保留原始语义的前提下清理冗余、补足必要上下文，"
                "并附加来源、分数和筛选状态。这样，下游训练器拿到的不只是文本，还知道它为何被保留以及可以追溯到哪里。\n\n"
                "具体来看，若 A 的原始分数为 3，则有界分数是 $q_i=3/(1+3)=0.75$；当阈值为 0.7 时它会被保留，"
                "而原始分数为 1 的候选得到 0.5，会在门控阶段被淘汰。这个数字例子把公式、控制分支和实际输出连在了一起。"
                "整个子模块发生在数据构造与训练准备阶段，推理时不会再次筛选同一批训练候选。Figure 1 给出输入、门控、精炼与训练器"
                "之间的接口，Algorithm 1 则规定操作顺序；二者共同说明输出集合如何被下一阶段消费。\n\n"
                "从作用上看，评分负责把质量判断变成可比较的量，门控负责执行保留或丢弃，精炼负责把通过者整理成稳定接口。"
                "三步缺一不可：没有评分便无法统一比较，没有门控便不能阻断噪声，没有精炼则下游仍要面对格式与上下文不一致。"
                "因此该模块解决的不是抽象的“提升质量”，而是明确回答每个候选经过哪些检查、在哪个条件下改变状态、最终交付什么。"
            )
            situation = (
                "论文研究候选种子经过多阶段筛选后用于下游训练的场景。原始候选在正确性、覆盖度和难度上分布不均，"
                "直接使用会把噪声传入后续优化并造成错误累积。已有一次性过滤方法只观察单一分数，无法解释候选在哪个阶段失效，"
                "也不能同时保证多样性和可验证性。论文据此把数据质量控制视为贯穿全流程的问题，并用正文分析和失败案例界定影响。"
            ) * 3
            task = (
                "给定原始候选、任务上下文和论文定义的评分信号，方法需要输出可供下一阶段直接消费的高质量候选集合。"
                "目标是在保持覆盖度的同时降低错误率，并满足预算、阈值和可验证性约束；评估同时考察最终任务指标、筛选质量与消融结果。"
                "论文处理的是该输入输出合同内的数据选择问题，不把未报告的部署条件或额外监督来源算作方法能力。"
            ) * 3
            result = (
                "论文在指定数据集和统一实验条件下，用正文表格报告主要指标并与同规模基线比较；指标方向、绝对差值和实验设置均应一起读取。"
                "消融实验分别移除评分、筛选或后续变换，以确认各阶段对最终结果的贡献；定性案例补充展示保留与丢弃的候选差异。"
                "这些证据支持多阶段链路在论文测试范围内有效，但不能证明它在未评测领域、不同预算或未披露实现条件下仍保持相同收益。"
            ) * 3
            action_intro = (
                "先用候选种子 A 串起整条方法链：原始生成器产生 A 后，系统读取任务上下文并计算质量分数，"
                "再依据阈值选择保留或淘汰；通过门控的 A 会被精炼并附上来源与状态，最后才交给训练器。"
                "沿着这个案例，读者能看到输入如何改变、每一步做出什么决策、哪个中间结果传给下一环节，以及失败候选在哪里退出。"
                "这条端到端链路也界定了训练期与推理期：评分、门控和精炼发生在训练数据准备阶段，部署推理不会重复构造这些样本。"
            ) * 3
            note = f"""# Seed Paper

- **论文**：https://arxiv.org/abs/2608.01234

## 一句话总结
总结。
## S｜Situation：研究情境与具体失败模式
{situation}
## T｜Task：论文要解决的任务与约束
{task}
## A｜Action：把论文方法完整走一遍
{action_intro}
{core}
![图 1：候选种子从生成、评分到筛选的完整方法链路](images/seed-paper/figure-01.png)
## R｜Result：实验结果、收益与证据
{result}
![图 2：主要结果与阶段消融在统一设置下的对比](images/seed-paper/figure-02.png)
## 与我的研究方向的关联
关联。
## 局限与证据边界
局限。
## 原文摘要
本文研究如何把质量参差不齐的候选种子整理为可用于下游训练的可靠数据。方法先结合任务上下文为候选评分，再通过有界分数和阈值执行门控，最后精炼通过筛选的内容并保留可追溯状态。实验从最终任务表现、筛选质量和阶段消融三个角度验证了这条数据准备链路的作用。
"""
            (day / "seed-paper.md").write_text(note, encoding="utf-8")
            digest = {
                "schema_version": 1,
                "date": "2026-08-06",
                "status": "prepared",
                "papers": [paper],
            }
            atomic_write_json(day / "digest.json", digest)
            (day / "digest.md").write_text(
                "# Digest\n\n## 图像与视频生成\n\n[Seed Paper](seed-paper.md)\n",
                encoding="utf-8",
            )
            run_date = datetime(2026, 8, 6).date()
            (day / "seed-paper.md").write_text(
                note.replace(situation, "场景说明过短。"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "Situation"):
                verify(config, config_path, run_date)
            (day / "seed-paper.md").write_text(
                note.replace("#### 种子评分、门控与精炼", "#### 候选排序"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "heading does not match"):
                verify(config, config_path, run_date)
            (day / "seed-paper.md").write_text(
                note.replace("这一部分不是孤立", "翻译：这一部分不是孤立", 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "must not use"):
                verify(config, config_path, run_date)
            (day / "seed-paper.md").write_text(
                note.replace(
                    "这一部分不是孤立的打分器",
                    "原文的评分部分把该组件放在完整流水线中的对应位置。这一部分不是孤立的打分器",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "generic method filler"):
                verify(config, config_path, run_date)
            (day / "seed-paper.md").write_text(
                note.replace(
                    "这一部分不是孤立的打分器",
                    "原文操作证据为官方 HTML 的 Method 章节。这一部分不是孤立的打分器",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "source-verification"):
                verify(config, config_path, run_date)
            (day / "seed-paper.md").write_text(
                note.replace(
                    "## 原文摘要\n本文研究如何把质量参差不齐的候选种子整理为可用于下游训练的可靠数据。方法先结合任务上下文为候选评分，再通过有界分数和阈值执行门控，最后精炼通过筛选的内容并保留可追溯状态。实验从最终任务表现、筛选质量和阶段消融三个角度验证了这条数据准备链路的作用。",
                    "## 原文摘要\nThis paper studies a seed selection pipeline that scores, gates, refines, and records generated candidates before downstream training.",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "fluent Chinese"):
                verify(config, config_path, run_date)
            (day / "seed-paper.md").write_text(
                note.replace(
                    "这一部分不是孤立的打分器",
                    "系统接收视觉、语言、轨迹或潜状态，再处理生成图像、问答结论、智能体轨迹、机器人动作。这一部分不是孤立的打分器",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "generic method filler"):
                verify(config, config_path, run_date)
            (day / "seed-paper.md").write_text(
                note.replace(
                    "这一部分不是孤立的打分器",
                    "系统先读取该阶段需要的论文专属中间结果，再把它从概念名称变成可供下一部分读取的结构化状态。这一部分不是孤立的打分器",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "generic method filler"):
                verify(config, config_path, run_date)
            extra_part = (
                "### 方法部分 2：虚构步骤（Invented Step）\n\n"
                "这个部分并不存在于论文 Method 中，验证器应拒绝 Markdown 与 JSON 方法部分数量不一致。\n"
            )
            (day / "seed-paper.md").write_text(
                note.replace("## R｜Result：实验结果、收益与证据", extra_part + "## R｜Result：实验结果、收益与证据"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "2 Method-part headings"):
                verify(config, config_path, run_date)
            (day / "seed-paper.md").write_text(note, encoding="utf-8")
            verified_day, verified_digest, papers = verify(config, config_path, run_date)
            finalize(config, config_path, run_date, verified_day, verified_digest, papers)
            verified_day, verified_digest, papers = verify(config, config_path, run_date)
            finalize(config, config_path, run_date, verified_day, verified_digest, papers)
            history = json.loads((root / "archive/pushed-paper-index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(history["papers"]), 1)


if __name__ == "__main__":
    unittest.main()
