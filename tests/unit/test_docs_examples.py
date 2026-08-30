"""Documentation consistency tests.

Every YAML block in the top-level docs that declares ``kind: Agent`` must
validate against the implemented schema, so copy-pasted examples work.
"""

from pathlib import Path

import pytest

from osa.generic_agent import load_agent_definition

REPO_ROOT = Path(__file__).resolve().parents[2]

DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "PROJECT_DEFINITION.md",
]


def _agent_example_blocks() -> list[tuple[str, str]]:
    """Collect (doc name, yaml block) pairs that declare kind: Agent."""
    blocks: list[tuple[str, str]] = []
    for doc in DOCS:
        for fence in doc.read_text().split("```yaml")[1:]:
            block = fence.split("```", 1)[0]
            if "kind: Agent" in block:
                blocks.append((doc.name, block))
    return blocks


def test_agent_examples_exist() -> None:
    assert len(_agent_example_blocks()) >= 2


@pytest.mark.parametrize(("doc_name", "block"), _agent_example_blocks())
def test_documented_agent_example_validates(doc_name: str, block: str) -> None:
    definition = load_agent_definition(block)
    assert definition.kind == "Agent"
    assert definition.metadata.name
