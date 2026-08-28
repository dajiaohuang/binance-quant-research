from __future__ import annotations

import argparse
import json
from pathlib import Path

from .collector import dry_plan, launch_formal, reserve_and_emit_reuse, reuse_preflight_check


def main() -> int:
    parser = argparse.ArgumentParser(prog="jquants_v2_bars_monthly_v2")
    parser.add_argument("--repo-root", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-plan", action="store_true")
    modes.add_argument("--reuse-preflight-check", action="store_true")
    modes.add_argument("--reserve-and-emit-reuse", action="store_true")
    modes.add_argument("--formal-bootstrap", action="store_true")
    modes.add_argument("--formal-bootstrap-pre-reserved", action="store_true")
    args = parser.parse_args()
    root = args.repo_root.resolve(strict=True)
    if args.dry_plan:
        result = dry_plan(root)
    elif args.reuse_preflight_check:
        result = reuse_preflight_check(root)
    elif args.reserve_and_emit_reuse:
        result = reserve_and_emit_reuse(root)
    else:
        result = launch_formal(root, pre_reserved=args.formal_bootstrap_pre_reserved)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
