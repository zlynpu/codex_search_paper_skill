#!/usr/bin/env python3
"""Repository-level checks that do not require any external service."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "daily-paper-digest"


def main() -> int:
    errors: list[str] = []
    required = [
        SKILL / "SKILL.md",
        SKILL / "agents/openai.yaml",
        SKILL / "scripts/run_daily.py",
        SKILL / "scripts/schedule_windows.ps1",
        ROOT / "install.sh",
        ROOT / "install.ps1",
        ROOT / "config.example.json",
    ]
    for path in required:
        if not path.is_file():
            errors.append(f"missing {path.relative_to(ROOT)}")
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
    if not match:
        errors.append("SKILL.md has no YAML frontmatter")
    else:
        keys = {
            line.split(":", 1)[0].strip()
            for line in match.group(1).splitlines()
            if ":" in line
        }
        if keys != {"name", "description"}:
            errors.append(f"SKILL.md frontmatter keys are {sorted(keys)}")
    if "Register-ScheduledTask" not in (SKILL / "scripts/schedule_windows.ps1").read_text(encoding="utf-8"):
        errors.append("Windows scheduler does not register a Scheduled Task")
    config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    if sum(category.get("quota", 0) for category in config["digest"]["categories"]) != config["digest"]["total_papers"]:
        errors.append("example quotas do not sum to total_papers")
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
        return 1
    print("repository validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
