from __future__ import annotations

import argparse
import json
from pathlib import Path

from .recovery import validate_source_probe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()
    result = validate_source_probe(Path(args.repo_root))
    print(json.dumps(result.bundle(), ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
