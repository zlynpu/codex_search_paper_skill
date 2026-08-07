from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


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
from prepare_digest import parse_atom, parse_recent_html, score_for_category  # noqa: E402
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
                "others": 2,
            },
        )

    def test_weight_allocation_uses_largest_remainder(self):
        config = template()
        config["digest"]["total_papers"] = 7
        for category, weight in zip(config["digest"]["categories"], (4, 1, 1, 1, 1)):
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
            (image_dir / "figure-01.png").write_bytes(b"png-one")
            (image_dir / "figure-02.png").write_bytes(b"png-two")
            stages = [
                {
                    "name": "种子筛选与精炼",
                    "source_heading": "Seed Selection and Refinement",
                    "translation": "论文把候选种子的评分、保留、丢弃与后续精炼定义为一个统一的方法部分，并明确各操作的先后关系。",
                    "explanation": "通俗地说，这一部分像一条带质量门控的流水线：候选种子和任务上下文先按论文阈值评分，低质量项被丢弃，保留项再精炼成下游训练样本。它发生在数据构造和训练准备阶段，推理时不再执行，并通过原文算法与图表给出依据。",
                    "evidence": "Method section Seed Selection and Refinement, algorithm and figure",
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
                "论文将候选种子的评分、保留、丢弃和后续精炼放在同一个 Method 小节中，"
                "并按原文顺序说明质量门控与下游接口。\n\n"
                "*可以把它理解为一条带质检的流水线。输入是候选种子、任务上下文及其分数表示；"
                "系统依据论文给定的阈值和目标函数逐项评分、筛选，再对保留项执行精炼。输出是带来源与评分标记、"
                "可直接供下游训练消费的候选集合。该操作发生在数据构造与训练准备阶段，推理时不再执行；"
                "它的作用是在进入下游优化前阻断低质量候选造成的错误累积。证据来自 Method 小节 "
                "Seed Selection and Refinement、对应算法与 Figure 1。*\n\n"
                "#### 必要公式与直觉\n\n"
                "论文用 $q_i=s_i/(1+s_i)$ 把原始候选分数 $s_i$ 压到有界质量分数 $q_i$。\n\n"
                "*$s_i$ 越大，$q_i$ 越接近 1，但不会无限增长；训练数据门根据 $q_i$ 与阈值的比较决定保留或丢弃。"
                "直观上，它像把不同量纲的原始成绩换算成统一百分制，避免极端大值垄断筛选结果。"
                + "这一部分继续解释具体计算、保留条件、丢弃条件以及下游如何消费输出。" * 10
                + "*"
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
            note = f"""# Seed Paper

- **论文**：https://arxiv.org/abs/2608.01234

## 一句话总结
总结。
## S｜Situation：研究情境与具体失败模式
{situation}
## T｜Task：论文要解决的任务与约束
{task}
## A｜Action：按论文 Method 逐部分翻译、解释与公式直觉
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
Abstract.
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
                note.replace("#### 必要公式与直觉", "#### 公式说明"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "必要公式与直觉"):
                verify(config, config_path, run_date)
            (day / "seed-paper.md").write_text(
                note.replace("论文将候选种子", "翻译：论文将候选种子", 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "must not use"):
                verify(config, config_path, run_date)
            (day / "seed-paper.md").write_text(
                note.replace("*可以把它理解", "可以把它理解", 1).replace(
                    "Figure 1。*", "Figure 1。", 1
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "italic explanation"):
                verify(config, config_path, run_date)
            extra_part = (
                "### 方法部分 2：虚构步骤（Invented Step）\n\n"
                "这个部分并不存在于论文 Method 中。\n\n"
                "*验证器应拒绝 Markdown 与 JSON 方法部分数量不一致。*\n\n"
                "#### 必要公式与直觉\n\n本部分无关键公式。\n"
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
