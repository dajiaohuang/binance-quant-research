from __future__ import annotations

import sys
import unittest
from collections.abc import Iterator


POST_FORMAL_PRECONDITION_TEST_IDS = frozenset(
    {
        "test_binance_spot_forward_schedule_pit_v2.ForwardSchedulePitTests."
        "test_real_workspace_formal_paths_absent_and_network_zero",
        "test_binance_spot_forward_schedule_pit_v4.ForwardSchedulePitTests."
        "test_real_workspace_formal_paths_absent_and_network_zero",
        "test_binance_spot_forward_schedule_pit_v6.ForwardSchedulePitTests."
        "test_real_workspace_formal_paths_absent_and_network_zero",
    }
)


def _iter_tests(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


def build_release_suite() -> tuple[unittest.TestSuite, tuple[str, ...]]:
    discovered = unittest.defaultTestLoader.discover("tests")
    selected = unittest.TestSuite()
    excluded: list[str] = []
    for test in _iter_tests(discovered):
        test_id = test.id()
        if test_id in POST_FORMAL_PRECONDITION_TEST_IDS:
            excluded.append(test_id)
        else:
            selected.addTest(test)
    if frozenset(excluded) != POST_FORMAL_PRECONDITION_TEST_IDS:
        missing = sorted(POST_FORMAL_PRECONDITION_TEST_IDS.difference(excluded))
        unexpected = sorted(set(excluded).difference(POST_FORMAL_PRECONDITION_TEST_IDS))
        raise RuntimeError(
            f"release-test exclusion drift; missing={missing!r}, unexpected={unexpected!r}"
        )
    return selected, tuple(sorted(excluded))


def main() -> int:
    suite, excluded = build_release_suite()
    print(
        "Excluding consumed-run workspace-absence preconditions: "
        + ", ".join(excluded),
        file=sys.stderr,
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
