from __future__ import annotations

import hashlib
from pathlib import Path

from .contracts import sha256_canonical


IMPLEMENTATION_FILES = (
    "src/quant_research/alpha_models/tips_v1/__init__.py",
    "src/quant_research/alpha_models/tips_v1/biases.py",
    "src/quant_research/alpha_models/tips_v1/checkpoint.py",
    "src/quant_research/alpha_models/tips_v1/contracts.py",
    "src/quant_research/alpha_models/tips_v1/data.py",
    "src/quant_research/alpha_models/tips_v1/losses.py",
    "src/quant_research/alpha_models/tips_v1/model.py",
    "src/quant_research/alpha_models/tips_v1/pipeline.py",
    "src/quant_research/alpha_models/tips_v1/provenance.py",
    "src/quant_research/alpha_models/tips_v1/smoke.py",
)


def implementation_tree(repo_root: Path, expected_files: tuple[str, ...]) -> tuple[list[dict[str, object]], str]:
    if type(expected_files) is not tuple or expected_files != IMPLEMENTATION_FILES:
        raise ValueError("implementation_files")
    entries: list[dict[str, object]] = []
    root = repo_root.resolve(strict=True)
    for relative in expected_files:
        path = (root / Path(relative)).resolve(strict=True)
        if root not in path.parents or path.is_symlink() or not path.is_file():
            raise ValueError("implementation_path")
        raw = path.read_bytes()
        entries.append({"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    return entries, sha256_canonical(entries)


def verify_implementation_tree(repo_root: Path, expected_files: tuple[str, ...], expected_tree_sha256: str) -> list[dict[str, object]]:
    entries, actual = implementation_tree(repo_root, expected_files)
    if actual != expected_tree_sha256:
        raise ValueError("implementation_tree")
    return entries
