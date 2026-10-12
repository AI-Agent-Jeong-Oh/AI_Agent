from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video_agent.knowledge.common_rules import (
    DEFAULT_RULES_PATH,
    CommonRule,
    load_common_rules,
)


class CommonRulesTests(unittest.TestCase):
    def test_bundled_rules_load(self) -> None:
        rules = load_common_rules()

        self.assertEqual(rules.source_name, "국가법령정보센터")
        self.assertEqual(rules.checked_on, "2026-10-07")
        self.assertEqual(
            [rule.rule_id for rule in rules.rules],
            ["sj-3", "sj-42-3", "dig-5", "dig-9", "pipa-3", "sj-5", "psr-2"],
        )
        for rule in rules.rules:
            self.assertTrue(rule.source_url.startswith("https://www.law.go.kr/"))
            self.assertNotIn(" ", rule.link)

    def test_bundled_file_has_titles_and_sources_only(self) -> None:
        data = json.loads(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))
        for item in data["rules"]:
            self.assertEqual(
                set(item),
                {"rule_id", "law_name", "article", "category", "source", "source_url"},
            )

    def test_link_is_percent_encoded(self) -> None:
        rule = CommonRule(
            rule_id="pipa-3",
            law_name="개인정보 보호법",
            article="제3조",
            category="법령",
            source="법률",
            source_url="https://www.law.go.kr/법령/개인정보 보호법/제3조",
        )
        self.assertEqual(
            rule.link,
            "https://www.law.go.kr/%EB%B2%95%EB%A0%B9/"
            "%EA%B0%9C%EC%9D%B8%EC%A0%95%EB%B3%B4%20%EB%B3%B4%ED%98%B8%EB%B2%95/%EC%A0%9C3%EC%A1%B0",
        )

    def test_invalid_files_are_rejected(self) -> None:
        base = json.loads(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))
        broken_cases = []
        foreign = json.loads(json.dumps(base))
        foreign["rules"][0]["source_url"] = "https://example.com/law"
        broken_cases.append(foreign)
        duplicate = json.loads(json.dumps(base))
        duplicate["rules"].append(duplicate["rules"][0])
        broken_cases.append(duplicate)
        extra = json.loads(json.dumps(base))
        extra["rules"][0]["text"] = "본문"
        broken_cases.append(extra)
        undated = json.loads(json.dumps(base))
        undated["checked_on"] = "최근"
        broken_cases.append(undated)
        empty = json.loads(json.dumps(base))
        empty["rules"] = []
        broken_cases.append(empty)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            for data in broken_cases:
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                with self.subTest(data=str(data)[:80]), self.assertRaises(ValueError):
                    load_common_rules(path)


if __name__ == "__main__":
    unittest.main()
