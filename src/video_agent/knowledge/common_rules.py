"""Case-independent rules for handling video evidence, shown as references only.

The bundled list holds titles and official links copied from the team demo material;
it carries no article text and is never used to decide whether a law applies.
Case-specific legal material is not connected yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote

DEFAULT_RULES_PATH = Path(__file__).with_name("data") / "common_rules.json"
_ALLOWED_URL_PREFIX = "https://www.law.go.kr/"


@dataclass(frozen=True, slots=True)
class CommonRule:
    rule_id: str
    law_name: str
    article: str
    category: str
    source: str
    source_url: str

    def __post_init__(self) -> None:
        for name in ("rule_id", "law_name", "article", "category", "source", "source_url"):
            if not getattr(self, name).strip():
                raise ValueError(f"common rule {name} is required")
        if not self.source_url.startswith(_ALLOWED_URL_PREFIX):
            raise ValueError("common rule links must point to the national law information center")

    @property
    def title(self) -> str:
        return f"{self.law_name} {self.article}"

    @property
    def link(self) -> str:
        """Percent-encoded URL safe to use as an href."""
        return quote(self.source_url, safe=":/?=&%")


@dataclass(frozen=True, slots=True)
class CommonRules:
    source_name: str
    checked_on: str
    rules: tuple[CommonRule, ...]

    def __post_init__(self) -> None:
        if not self.source_name.strip():
            raise ValueError("common rules source_name is required")
        date.fromisoformat(self.checked_on)
        if not self.rules:
            raise ValueError("common rules list is empty")
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("common rule IDs must be unique")


def load_common_rules(path: Path = DEFAULT_RULES_PATH) -> CommonRules:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError("common rules file must contain a rules list")
    allowed = {"rule_id", "law_name", "article", "category", "source", "source_url"}
    rules = []
    for item in data["rules"]:
        if not isinstance(item, dict) or set(item) != allowed:
            raise ValueError("common rule has missing or unknown fields")
        rules.append(CommonRule(**{key: str(item[key]) for key in allowed}))
    return CommonRules(
        source_name=str(data.get("source_name", "")),
        checked_on=str(data.get("checked_on", "")),
        rules=tuple(rules),
    )
