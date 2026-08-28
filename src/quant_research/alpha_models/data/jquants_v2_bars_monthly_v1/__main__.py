from __future__ import annotations

import argparse
import json
from pathlib import Path

from .collector import dry_plan, launch_formal


def main() -> int:
    parser = argparse.ArgumentParser(prog="jquants_v2_bars_monthly_v1")
    parser.add_argument("--repo-root", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-plan", action="store_true")
    modes.add_argument("--formal-bootstrap", action="store_true")
    args = parser.parse_args()
    root = args.repo_root.resolve(strict=True)
    result = dry_plan(root) if args.dry_plan else launch_formal(root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
