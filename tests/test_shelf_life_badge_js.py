"""Run the JS tests for the Shelf Life chip on library cards.

an admin's library cards carry a Shelf Life score in a colour band against the
job's threshold, or a shield when a gate protects the title.

The contract tests live in `tests/static/test_shelf_life_badge.mjs` and run via
Node's built-in runner; this shim surfaces them in the regular pytest sweep.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_FILE = _REPO_ROOT / "tests" / "static" / "test_shelf_life_badge.mjs"


from tests._node_runner import NODE, node_available, node_path


def test_shelf_life_badge_js():
    """Pin the chip each Shelf Life state draws on a library card."""
    if not node_available():
        pytest.skip("Node.js >= 22 required to run the JS Shelf Life badge tests")

    if not _TEST_FILE.exists():
        pytest.skip(f"JS test file missing: {_TEST_FILE}")

    result = subprocess.run(
        [NODE, "--test", node_path(_TEST_FILE)],
        capture_output=True, text=True,
        cwd=str(_REPO_ROOT),
        timeout=60,
    )

    if result.returncode != 0:
        pytest.fail(
            "JS Shelf Life badge tests failed:\n\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}",
            pytrace=False,
        )
