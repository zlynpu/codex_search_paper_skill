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
                    "name": f"method stage {index}",
                    "input": "candidate seeds and prompt representation",
                    "operation": "score, retain, and transform candidates with the stated rule",
                    "output": "ranked candidates passed to the next processing stage",
                    "purpose": "remove low-quality candidates before downstream optimization",
                    "evidence": f"paper section {index}, algorithm and figure",
                }
                for index in range(1, 4)
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
            core = "\n".join(
                f"### 阶段 {index}：具体操作\n\n**输入**：候选种子。**操作**：逐项评分、筛选并传递。**输出**：下一阶段候选。**目的**：避免错误累积。**证据**：算法 {index}。" + "该阶段说明具体保留与丢弃规则。" * 20
                for index in range(1, 4)
            )
            note = f"""# Seed Paper

- **论文**：https://arxiv.org/abs/2608.01234

## 一句话总结
总结。
## 具体失败模式与已有方法为何不够
失败模式。
## 核心创新：从输入到输出的逐阶段操作链
{core}
![图一](images/seed-paper/figure-01.png)
![图二](images/seed-paper/figure-02.png)
## 训练目标、奖励或关键公式
目标。
## 关键指标与实验结论
结果。
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
            verified_day, verified_digest, papers = verify(config, config_path, run_date)
            finalize(config, config_path, run_date, verified_day, verified_digest, papers)
            verified_day, verified_digest, papers = verify(config, config_path, run_date)
            finalize(config, config_path, run_date, verified_day, verified_digest, papers)
            history = json.loads((root / "archive/pushed-paper-index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(history["papers"]), 1)


if __name__ == "__main__":
    unittest.main()
