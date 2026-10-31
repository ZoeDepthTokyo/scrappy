from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_includes_runtime_modules_needed_by_entrypoints():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    py_modules = set(pyproject["tool"]["setuptools"]["py-modules"])
    assert {"prefilter", "score_inbox", "geo_backfill"} <= py_modules
