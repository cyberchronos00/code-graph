"""README's supported languages stay listed on the docs homepage and in the docs nav."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
STACKS = ROOT / "docs-site" / "app" / "components" / "HomeStacks.vue"
NAV = ROOT / "docs-site" / "app" / "utils" / "docs-nav.ts"

BADGE = re.compile(r"!\[([^\]]+)\]")
ROW_LINK = re.compile(r"\]\(([^)]+)\)")


def _supported_rows() -> list[tuple[list[str], str]]:
    lines = README.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## Supported languages"))
    rows: list[tuple[list[str], str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if not line.startswith("| ![") and not line.startswith("|!["):
            continue
        names = BADGE.findall(line)
        link = ROW_LINK.findall(line)[-1]
        rows.append((names, link))
    return rows


def _token(name: str, text: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text) is not None


def test_readme_languages_appear_on_home_and_in_docs():
    stacks = STACKS.read_text(encoding="utf-8")
    nav = NAV.read_text(encoding="utf-8")
    missing_stacks: list[str] = []
    missing_docs: list[str] = []

    for names, link in _supported_rows():
        slug = link.rstrip("/").rsplit("/", 1)[-1]
        page = ROOT / "docs" / f"{slug}.md"
        for name in names:
            if not _token(name, stacks):
                missing_stacks.append(
                    f"{name}: add it to docs-site/app/components/HomeStacks.vue"
                )
            if _token(name, nav) or page.is_file():
                continue
            missing_docs.append(
                f"{name}: add a nav entry in docs-site/app/utils/docs-nav.ts "
                f"or the docs page linked from README ({page.relative_to(ROOT)})"
            )

    assert missing_stacks == [], "\n".join(missing_stacks)
    assert missing_docs == [], "\n".join(missing_docs)
