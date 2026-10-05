"""Docs links use page titles, and #anchors point at real headings."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
ANCHOR_ID = re.compile(r"""<a\s[^>]*\bid=["']([^"']+)["']""", re.I)
REL_MD = re.compile(r"^([^)#]+\.md)#([^)#]+)$")


def _slug(heading: str) -> str:
    kept = []
    for char in heading.lower():
        if char.isalnum() or char in " _-":
            kept.append(char)
    return "".join(kept).replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found = {_slug(match.group(1)) for match in HEADING.finditer(text)}
    found.update(ANCHOR_ID.findall(text))
    return found


def test_doc_links_use_titles_and_real_anchors():
    filename_labels: list[str] = []
    missing_anchors: list[str] = []
    cache: dict[Path, set[str]] = {}

    for path in sorted(DOCS.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            label, href = match.group(1), match.group(2)
            visible = label.replace("`", "")
            if visible.endswith(".md") or ".md#" in visible:
                filename_labels.append(f"{path.relative_to(ROOT)}: [{label}]({href})")
            rel = REL_MD.match(href)
            if not rel:
                continue
            target = (path.parent / rel.group(1)).resolve()
            anchor = rel.group(2)
            if target not in cache:
                cache[target] = _anchors(target) if target.is_file() else set()
            if anchor not in cache[target]:
                missing_anchors.append(
                    f"{path.relative_to(ROOT)}: {href} -> {target.relative_to(ROOT)}"
                )

    assert filename_labels == []
    assert missing_anchors == []
