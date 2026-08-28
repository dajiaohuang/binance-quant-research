from __future__ import annotations

import argparse
import json
from pathlib import Path

from .monthly import dry_plan, launch_formal, reserve_batch_and_emit_source_binding
from .source import verify_source_preflight


def main() -> int:
    parser = argparse.ArgumentParser(prog="jquants_v2_bars_monthly_v4")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--batch-id")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-plan", action="store_true")
    modes.add_argument("--source-preflight-check", action="store_true")
    modes.add_argument("--reserve-batch-and-emit-source-binding", action="store_true")
    modes.add_argument("--formal-monthly-pre-reserved", action="store_true")
    args = parser.parse_args()
    root = args.repo_root.resolve(strict=True)
    if args.dry_plan:
        if args.batch_id is not None:
            parser.error("--batch-id is not accepted by --dry-plan")
        result = dry_plan(root)
    elif args.source_preflight_check:
        if args.batch_id is not None:
            parser.error("--batch-id is not accepted by --source-preflight-check")
        result = verify_source_preflight(root)
    elif args.reserve_batch_and_emit_source_binding:
        if args.batch_id is None:
            parser.error("--batch-id is required")
        result = reserve_batch_and_emit_source_binding(root, args.batch_id)
    else:
        if args.batch_id is None:
            parser.error("--batch-id is required")
        result = launch_formal(root, args.batch_id, pre_reserved=True)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
