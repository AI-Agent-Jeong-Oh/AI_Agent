from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video_agent.knowledge.case_rules import load_case_rules


class CaseRulesTests(unittest.TestCase):
    def test_bundled_rules_cover_each_case_type(self) -> None:
        rules = load_case_rules()
        counts = {kind: len(rules.for_case_type(kind)) for kind in ("폭행", "아동학대", "실종", "절도")}
        self.assertEqual(counts, {"폭행": 6, "아동학대": 8, "실종": 8, "절도": 4})
        self.assertEqual(rules.for_case_type("기타"), ())
        self.assertEqual(rules.for_case_type(""), ())
        # Only the theft articles were added by the app (not in the team demo material).
        self.assertEqual(
            {rule.rule_id for rule in rules.rules if rule.origin == "app"},
            {"crim-329", "crim-330", "crim-331", "crim-366"},
        )
        self.assertTrue(all(rule.source_url.startswith("https://www.law.go.kr/") for rule in rules.rules))

    def test_unknown_fields_and_foreign_links_are_rejected(self) -> None:
        good = json.loads(Path("src/video_agent/knowledge/data/case_rules.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rules.json"
            extra = dict(good, rules=[dict(good["rules"][0], text="본문")])
            path.write_text(json.dumps(extra, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_case_rules(path)
            foreign = dict(good, rules=[dict(good["rules"][0], source_url="https://example.com/")])
            path.write_text(json.dumps(foreign, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_case_rules(path)


if __name__ == "__main__":
    unittest.main()
