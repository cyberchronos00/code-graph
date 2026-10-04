"""AGENTS.md at the repo root is generated from codegraph.agent_rules; keep them in sync."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codegraph import agent_rules  # noqa: E402


def test_agents_md_matches_package_source():
    assert (ROOT / "AGENTS.md").read_text() == agent_rules.document()


def test_block_wraps_text_with_markers():
    b = agent_rules.block()
    assert b.startswith(agent_rules.BEGIN_MARK) and b.rstrip().endswith(agent_rules.END_MARK)
    assert agent_rules.TEXT in b
