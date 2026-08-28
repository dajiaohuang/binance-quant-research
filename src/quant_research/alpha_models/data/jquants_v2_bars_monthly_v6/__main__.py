from __future__ import annotations

import argparse
import json
from pathlib import Path

from .recovery import (
    dry_recovery_plan,
    launch_formal,
    reserve_recovery_batch,
    verify_recovery_preflight,
)


def main() -> int:
    parser = argparse.ArgumentParser(prog="jquants_v2_bars_monthly_v6")
    parser.add_argument("--repo-root", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-recovery-plan", action="store_true")
    modes.add_argument("--recovery-preflight-check", action="store_true")
    modes.add_argument("--reserve-recovery-batch", action="store_true")
    modes.add_argument("--formal-recovery-pre-reserved", action="store_true")
    args = parser.parse_args()
    root = args.repo_root.resolve(strict=True)
    if args.dry_recovery_plan:
        result = dry_recovery_plan(root)
    elif args.recovery_preflight_check:
        result = verify_recovery_preflight(root)
    elif args.reserve_recovery_batch:
        result = reserve_recovery_batch(root)
    else:
        result = launch_formal(root, pre_reserved=True)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
