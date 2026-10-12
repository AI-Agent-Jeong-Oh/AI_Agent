"""Laws to look at for each case type (폭행, 아동학대, 실종, 절도), shown as references only.

Titles, sources and official links only, like the common rules: no article text, and never
used to decide whether a law applies. 폭행·아동학대·실종 come from the team demo material;
절도 is not in the demo and was added by the app from the Criminal Act's article names
(`origin == "app"`), to be checked against the original.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote

DEFAULT_CASE_RULES_PATH = Path(__file__).with_name("data") / "case_rules.json"
_ALLOWED_URL_PREFIX = "https://www.law.go.kr/"
_FIELDS = {"rule_id", "law_name", "article", "case_types", "source", "source_url", "origin"}


@dataclass(frozen=True, slots=True)
class CaseRule:
    rule_id: str
    law_name: str
    article: str
    case_types: tuple[str, ...]
    source: str
    source_url: str
    origin: str = "demo"

    def __post_init__(self) -> None:
        for name in ("rule_id", "law_name", "article", "source", "source_url"):
            if not getattr(self, name).strip():
                raise ValueError(f"case rule {name} is required")
        if not self.case_types:
            raise ValueError("case rule needs at least one case type")
        if self.origin not in {"demo", "app"}:
            raise ValueError("case rule origin must be demo or app")
        if not self.source_url.startswith(_ALLOWED_URL_PREFIX):
            raise ValueError("case rule links must point to the national law information center")

    @property
    def link(self) -> str:
        return quote(self.source_url, safe=":/?=&%")


@dataclass(frozen=True, slots=True)
class CaseRules:
    source_name: str
    checked_on: str
    rules: tuple[CaseRule, ...]

    def __post_init__(self) -> None:
        if not self.source_name.strip():
            raise ValueError("case rules source_name is required")
        date.fromisoformat(self.checked_on)
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("case rule IDs must be unique")

    def for_case_type(self, case_type: str | None) -> tuple[CaseRule, ...]:
        """Rules for one case type, in file order (none for 기타 or an unknown type)."""
        if not case_type:
            return ()
        return tuple(rule for rule in self.rules if case_type in rule.case_types)


def load_case_rules(path: Path = DEFAULT_CASE_RULES_PATH) -> CaseRules:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError("case rules file must contain a rules list")
    rules = []
    for item in data["rules"]:
        if not isinstance(item, dict) or set(item) != _FIELDS:
            raise ValueError("case rule has missing or unknown fields")
        if not isinstance(item["case_types"], list):
            raise ValueError("case rule case_types must be a list")
        rules.append(
            CaseRule(
                rule_id=str(item["rule_id"]),
                law_name=str(item["law_name"]),
                article=str(item["article"]),
                case_types=tuple(str(value) for value in item["case_types"]),
                source=str(item["source"]),
                source_url=str(item["source_url"]),
                origin=str(item["origin"]),
            )
        )
    return CaseRules(
        source_name=str(data.get("source_name", "")),
        checked_on=str(data.get("checked_on", "")),
        rules=tuple(rules),
    )
