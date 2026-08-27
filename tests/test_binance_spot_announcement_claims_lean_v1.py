from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_ROOT = REPO_ROOT / "src" / "quant_research"
sys.path.insert(0, str(MODULE_ROOT))

import binance_spot_announcement_claims_lean_v1 as extractor
import binance_spot_announcement_claims_lean_v1_loader as loader
import binance_spot_announcement_claims_lean_v1_runner as runner


def text(value: str) -> dict:
    return {"node": "text", "text": value}


def element(tag: str, *children: dict) -> dict:
    return {"node": "element", "tag": tag, "child": list(children)}


def paragraph(value: str) -> dict:
    return element("p", text(value))


def root(*children: dict) -> dict:
    return {"node": "root", "child": list(children)}


def detail(code: str, ast: dict) -> extractor.AcceptedDetail:
    body = json.dumps(
        ast, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    body_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    response_sha = hashlib.sha256(("response:" + code).encode()).hexdigest()
    return extractor.AcceptedDetail(
        code, response_sha, body_sha, body, ast,
    )


def dummy_bindings() -> dict[str, dict[str, str]]:
    return {
        name: {"path": relative, "sha256": "0" * 64}
        for _flag, name, relative in runner.BINDING_SPECS
    }


def actual_bindings() -> dict[str, dict[str, str]]:
    return {
        name: {
            "path": relative,
            "sha256": hashlib.sha256(
                REPO_ROOT.joinpath(
                    *pathlib.PurePosixPath(relative).parts
                ).read_bytes()
            ).hexdigest(),
        }
        for _flag, name, relative in runner.BINDING_SPECS
    }


class JsonAndRendererTests(unittest.TestCase):
    def test_strict_json_rejects_duplicate_and_nonfinite(self) -> None:
        with self.assertRaises(extractor.ClaimsError):
            extractor.strict_json('{"a":1,"a":2}')
        with self.assertRaises(extractor.ClaimsError):
            extractor.strict_json('{"a":NaN}')
        self.assertEqual(extractor.strict_jsonl(b""), [])

    def test_r2_2_1_textcontent_nbsp_and_no_structural_separator(self) -> None:
        node = element(
            "p", element("span", text("A&nbsp;")),
            element("strong", text("B")), element("span", text(".")),
        )
        view = extractor.build_view(node, "/child/0")
        self.assertEqual(view.text, "A B.")
        span = extractor.make_span(view, 0, len(view.text))
        kinds = [atom["kind"] for atom in span["atoms"]]
        self.assertIn("ENTITY_NBSP", kinds)
        self.assertNotIn("STRUCTURAL", kinds)
        entity = next(
            atom for atom in span["atoms"]
            if atom["kind"] == "ENTITY_NBSP"
        )
        self.assertEqual(entity["raw_fragment"], "&nbsp;")
        self.assertEqual((entity["raw_start_cp"], entity["raw_end_cp"]), (1, 7))

    def test_other_entity_is_literal(self) -> None:
        view = extractor.build_view(paragraph("A&amp;B"), "/child/0")
        self.assertEqual(view.text, "A&amp;B")

    def test_cross_node_glued_token_is_rejected(self) -> None:
        node = element(
            "p", text("open trading for these spot trading pai"),
            element("strong", text("rs")),
        )
        view = extractor.build_view(node, "/child/0")
        self.assertEqual(view.text, "open trading for these spot trading pairs")
        self.assertEqual(extractor._ascii_ci_matches(view), [])


class GrammarTests(unittest.TestCase):
    OPEN_HEADER = (
        "Binance will open trading for these spot trading pairs "
        "at 2024-02-06 15:00 (UTC)."
    )
    REMOVAL_HEADER = (
        "Binance will remove and cease trading on the following "
        "spot trading pairs:"
    )

    def _open(
        self, carrier_leaves: list[str] | None = None,
        header: str | None = None,
    ) -> extractor.AcceptedDetail:
        children = [paragraph(header or self.OPEN_HEADER)]
        if carrier_leaves is not None:
            children.append(
                element(
                    "ul", *(element("li", paragraph(value))
                            for value in carrier_leaves),
                )
            )
        return detail("a" * 32, root(*children))

    def _removal(self, leaves: list[str]) -> extractor.AcceptedDetail:
        return detail(
            "b" * 32,
            root(
                paragraph(self.REMOVAL_HEADER),
                element(
                    "ul", *(element("li", paragraph(value))
                            for value in leaves),
                ),
            ),
        )

    def test_open_claims_and_source_spans(self) -> None:
        item = self._open(
            ["New Spot Trading Pairs: AAA/USDT and BBB/USDT."],
        )
        claims, ambiguity, coverage = extractor.analyze_article(item)
        self.assertIsNone(ambiguity)
        self.assertEqual(coverage["status"], "CLAIMED")
        self.assertEqual(
            [row["syntactic_pair_token_claim"] for row in claims],
            ["AAA/USDT", "BBB/USDT"],
        )
        for claim in claims:
            self.assertEqual(
                claim["pair_source_span"]["fragment"],
                claim["syntactic_pair_token_claim"],
            )
            self.assertEqual(len(claim["claim_id"]), 64)

    def test_removal_multi_leaf_binds_each_time(self) -> None:
        item = self._removal(
            [
                "At 2024-05-10 03:00 (UTC): AAVE/BNB, DEGO/BTC",
                "At 2024-05-17 03:00 (UTC): CFX/TUSD.",
            ]
        )
        claims, ambiguity, coverage = extractor.analyze_article(item)
        self.assertIsNone(ambiguity)
        self.assertEqual(coverage["claim_count"], 3)
        self.assertEqual(
            [row["claimed_schedule_ms"] for row in claims],
            [1715310000000, 1715310000000, 1715914800000],
        )

    def test_same_family_containment_reduces_r4_inside_r1(self) -> None:
        view = extractor.build_view(
            paragraph(
                "remove and cease trading on the following spot trading pairs"
            ),
            "/child/0",
        )
        matches = extractor._ascii_ci_matches(view)
        self.assertEqual({row["id"] for row in matches}, {"R1", "R4"})
        self.assertEqual(
            [row["id"] for row in extractor.reduce_actions(matches)], ["R1"],
        )

    def test_same_family_across_segments_is_not_containment_reduced(self) -> None:
        item = detail(
            "e" * 32,
            root(
                paragraph(
                    "remove and cease trading on the following spot trading pairs:"
                ),
                paragraph("cease trading on the following spot trading pairs:"),
            ),
        )
        claims, ambiguity, _ = extractor.analyze_article(item)
        self.assertEqual(claims, [])
        self.assertEqual(
            ambiguity["primary_reason"], "MULTIPLE_ACTION_SPANS",
        )

    def test_cross_family_actions_are_ambiguous(self) -> None:
        item = self._open(
            ["New Spot Trading Pairs: AAA/USDT."],
            header=(
                self.OPEN_HEADER + " Later remove and cease trading on "
                "the following spot trading pairs:"
            ),
        )
        claims, ambiguity, coverage = extractor.analyze_article(item)
        self.assertEqual(claims, [])
        self.assertEqual(coverage["status"], "AMBIGUOUS")
        self.assertEqual(
            ambiguity["primary_reason"], "MULTIPLE_ACTION_FAMILIES",
        )

    def test_multiple_open_wrappers_are_ambiguous(self) -> None:
        item = self._open(
            [
                "New Spot Trading Pairs: AAA/USDT.",
                "New Spot Trading Pairs: BBB/USDT.",
            ]
        )
        claims, ambiguity, _ = extractor.analyze_article(item)
        self.assertEqual(claims, [])
        self.assertIn("MULTIPLE_PAIR_WRAPPERS", ambiguity["reasons"])

    def test_invalid_utc_is_ambiguous(self) -> None:
        item = self._open(
            ["New Spot Trading Pairs: AAA/USDT."],
            header=(
                "Binance will open trading for these spot trading pairs "
                "at 2024-02-30 15:00 (UTC)."
            ),
        )
        claims, ambiguity, _ = extractor.analyze_article(item)
        self.assertEqual(claims, [])
        self.assertIn("UTC_INVALID", ambiguity["reasons"])

    def test_multiple_utc_is_ambiguous(self) -> None:
        item = self._open(
            ["New Spot Trading Pairs: AAA/USDT."],
            header=(
                "Binance will open trading for these spot trading pairs "
                "at 2024-02-28 15:00 (UTC) 2024-02-28 16:00 (UTC)."
            ),
        )
        claims, ambiguity, _ = extractor.analyze_article(item)
        self.assertEqual(claims, [])
        self.assertIn("HEADER_SUFFIX_INVALID", ambiguity["reasons"])
        self.assertIn("UTC_INVALID", ambiguity["reasons"])

    def test_duplicate_pair_and_multiple_time_are_ambiguous(self) -> None:
        item = self._removal(
            [
                "At 2024-05-10 03:00 (UTC): AAA/USDT",
                "At 2024-05-17 03:00 (UTC): AAA/USDT",
            ]
        )
        claims, ambiguity, _ = extractor.analyze_article(item)
        self.assertEqual(claims, [])
        self.assertIn("DUPLICATE_PAIR", ambiguity["reasons"])
        self.assertIn(
            "PAIR_BOUND_TO_MULTIPLE_TIMES", ambiguity["reasons"],
        )

    def test_nonselected_pair_or_url_collides(self) -> None:
        for collision in (
            "Reference AAA/USDT",
            "See https://example.invalid",
        ):
            with self.subTest(collision=collision):
                item = self._open(
                    [
                        "New Spot Trading Pairs: BBB/USDT.",
                        collision,
                    ]
                )
                claims, ambiguity, _ = extractor.analyze_article(item)
                self.assertEqual(claims, [])
                self.assertIn(
                    "NONSELECTED_CARRIER_COLLISION",
                    ambiguity["reasons"],
                )

    def test_nonselected_isolated_utc_schedule_is_ignored(self) -> None:
        item = self._open(
            [
                "New Spot Trading Pairs: BBB/USDT.",
                "Withdrawals will open at 2024-02-07 15:00 (UTC).",
            ]
        )
        claims, ambiguity, coverage = extractor.analyze_article(item)
        self.assertIsNone(ambiguity)
        self.assertEqual(coverage["status"], "CLAIMED")
        self.assertEqual(len(claims), 1)

    def test_invalid_pair_token_is_ambiguous(self) -> None:
        item = self._open(
            ["New Spot Trading Pairs: btc/usdt."],
        )
        claims, ambiguity, _ = extractor.analyze_article(item)
        self.assertEqual(claims, [])
        self.assertIn("PAIR_TOKEN_REJECTED", ambiguity["reasons"])

    def test_pair_glued_across_text_nodes_is_rejected(self) -> None:
        header = paragraph(self.OPEN_HEADER)
        leaf = element(
            "li", element(
                "p", text("New Spot Trading Pairs: AA"),
                element("strong", text("A/USDT")), text("."),
            ),
        )
        item = detail("d" * 32, root(header, element("ul", leaf)))
        claims, ambiguity, _ = extractor.analyze_article(item)
        self.assertEqual(claims, [])
        self.assertIn("PAIR_TOKEN_REJECTED", ambiguity["reasons"])

    def test_no_action_is_no_match(self) -> None:
        claims, ambiguity, coverage = extractor.analyze_article(
            detail("c" * 32, root(paragraph("ordinary announcement"))),
        )
        self.assertEqual(claims, [])
        self.assertIsNone(ambiguity)
        self.assertEqual(coverage["status"], "NO_MATCH")


class FrozenCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.details = extractor.load_accepted_details(REPO_ROOT)
        cls.by_code = {item.article_code: item for item in cls.details}
        cls.parameters = json.loads(
            (
                REPO_ROOT
                / "experiments/exp_20260826_006/parameters.json"
            ).read_text(encoding="utf-8")
        )

    def test_minimal_input_revalidation_closes_756(self) -> None:
        self.assertEqual(len(self.details), 756)
        self.assertEqual(
            extractor.keyset_sha256(self.by_code),
            extractor.EXPECTED_DETAIL_KEYSET,
        )

    def test_six_frozen_positive_fixtures_rebuild_exactly(self) -> None:
        selected = []
        for fixture in self.parameters["fixtures"]:
            item = self.by_code[fixture["code"]]
            self.assertEqual(item.response_sha256, fixture["raw_sha256"])
            self.assertEqual(item.body_sha256, fixture["body_sha256"])
            selected.append(item)
        payload = extractor.build_payload(selected, dummy_bindings())
        claims = payload["rows"]["claims.jsonl"]
        coverage = payload["rows"]["coverage.jsonl"]
        self.assertEqual(len(claims), 20)
        self.assertTrue(all(row["status"] == "CLAIMED" for row in coverage))
        grouped: dict[str, list[tuple[str, int]]] = {}
        for claim in claims:
            grouped.setdefault(claim["article_code"], []).append(
                (
                    claim["syntactic_pair_token_claim"],
                    claim["claimed_schedule_ms"],
                )
            )
        for fixture in self.parameters["fixtures"]:
            self.assertEqual(
                grouped[fixture["code"]],
                [
                    (pair, fixture["epoch_ms"])
                    for pair in sorted(
                        fixture["pairs"], key=lambda value: value.encode("utf-8")
                    )
                ],
            )
            fixture_claims = [
                row for row in claims
                if row["article_code"] == fixture["code"]
            ]
            for row in fixture_claims:
                self.assertTrue(
                    all(
                        atom["pointer"].startswith(
                            fixture["header_pointer"] + "/"
                        )
                        for atom in row["action_source_span"]["atoms"]
                    )
                )
                self.assertTrue(
                    all(
                        atom["pointer"].startswith(
                            fixture["carrier_pointer"] + "/"
                        )
                        for atom in row["pair_source_span"]["atoms"]
                    )
                )

    def test_retry_positive_fixture_has_three_429_then_ok(self) -> None:
        ledger = extractor.strict_jsonl(
            (
                REPO_ROOT
                / extractor.INPUT_BINDINGS[
                    "exp005_request_ledger"
                ]["path"]
            ).read_bytes()
        )
        retry = next(
            row for row in ledger
            if row.get("kind") == "detail" and len(row["attempts"]) == 4
        )
        self.assertEqual(
            [attempt["outcome"] for attempt in retry["attempts"]],
            ["HTTP_429", "HTTP_429", "HTTP_429", "OK"],
        )

    def test_full_corpus_in_memory_and_temp_loader_coverage_closure(self) -> None:
        payload = extractor.build_payload(self.details, dummy_bindings())
        coverage = payload["rows"]["coverage.jsonl"]
        self.assertEqual(len(coverage), 756)
        self.assertEqual(
            sum(row["claim_count"] for row in coverage),
            len(payload["rows"]["claims.jsonl"]),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory)
            for name, raw in payload["payload_bytes"].items():
                (output / name).write_bytes(raw)
            (output / "summary.json").write_bytes(payload["summary_bytes"])
            loaded = loader.validate_directory(
                output,
                expected_article_codes=[item.article_code for item in self.details],
                expected_detail_bindings={
                    item.article_code: (
                        item.response_sha256, item.body_sha256,
                    )
                    for item in self.details
                },
                expected_input_bindings=extractor.INPUT_BINDINGS,
                expected_code_bindings=dummy_bindings(),
            )
            self.assertEqual(loaded.summary["coverage_count"], 756)


class LoaderAndRunnerTests(unittest.TestCase):
    def _payload_root(self) -> tuple[pathlib.Path, tempfile.TemporaryDirectory]:
        temporary = tempfile.TemporaryDirectory()
        root_path = pathlib.Path(temporary.name) / "result"
        root_path.mkdir()
        details = [
            GrammarTests()._open(
                ["New Spot Trading Pairs: AAA/USDT."],
            ),
            detail("c" * 32, root(paragraph("ordinary announcement"))),
        ]
        payload = extractor.build_payload(details, dummy_bindings())
        for name, raw in payload["payload_bytes"].items():
            (root_path / name).write_bytes(raw)
        (root_path / "summary.json").write_bytes(
            payload["summary_bytes"],
        )
        return root_path, temporary

    def test_loader_enforces_partition_and_hash_tree(self) -> None:
        root_path, temporary = self._payload_root()
        self.addCleanup(temporary.cleanup)
        loaded = loader.validate_directory(
            root_path,
            expected_article_codes=["a" * 32, "c" * 32],
            expected_detail_bindings={
                "a" * 32: (
                    hashlib.sha256(("response:" + "a" * 32).encode()).hexdigest(),
                    hashlib.sha256(
                        json.dumps(
                            root(
                                paragraph(GrammarTests.OPEN_HEADER),
                                element(
                                    "ul",
                                    element(
                                        "li",
                                        paragraph("New Spot Trading Pairs: AAA/USDT."),
                                    ),
                                ),
                            ),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                ),
                "c" * 32: (
                    hashlib.sha256(("response:" + "c" * 32).encode()).hexdigest(),
                    hashlib.sha256(
                        json.dumps(
                            root(paragraph("ordinary announcement")),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest(),
                ),
            },
            expected_input_bindings=extractor.INPUT_BINDINGS,
            expected_code_bindings=dummy_bindings(),
        )
        self.assertEqual(loaded.terminal_status, "NEEDS_MORE_DATA")
        self.assertEqual(loaded.claim_count, 1)

    def test_loader_rejects_unknown_or_duplicate_summary_key(self) -> None:
        for mutation in ("unknown", "duplicate"):
            with self.subTest(mutation=mutation):
                root_path, temporary = self._payload_root()
                try:
                    summary_path = root_path / "summary.json"
                    raw = summary_path.read_text(encoding="utf-8")
                    if mutation == "unknown":
                        value = json.loads(raw)
                        value["alpha"] = True
                        summary_path.write_bytes(
                            extractor.canonical_pretty(value),
                        )
                    else:
                        summary_path.write_text(
                            raw.replace(
                                "{\n",
                                '{\n  "experiment_id": "duplicate",\n',
                                1,
                            ),
                            encoding="utf-8",
                            newline="\n",
                        )
                    with self.assertRaises(
                        (loader.ClaimsLoadError, extractor.ClaimsError),
                    ):
                        loader.validate_directory(root_path)
                finally:
                    temporary.cleanup()

    def test_loader_rejects_claim_tamper(self) -> None:
        root_path, temporary = self._payload_root()
        self.addCleanup(temporary.cleanup)
        claim_path = root_path / "claims.jsonl"
        row = json.loads(claim_path.read_text(encoding="utf-8"))
        row["syntactic_pair_token_claim"] = "TAMPER/USDT"
        claim_path.write_bytes(
            extractor.canonical_compact(row, newline=True),
        )
        with self.assertRaises(loader.ClaimsLoadError):
            loader.validate_directory(root_path)

    def test_runner_requires_six_flags_in_exact_order(self) -> None:
        argv: list[str] = []
        for flag, _name, _path in runner.BINDING_SPECS:
            argv.extend((flag, "0" * 64))
        parsed = runner.parse_args(argv)
        self.assertEqual(
            parsed.expected_runner_sha256, "0" * 64,
        )
        swapped = argv[:]
        swapped[0], swapped[2] = swapped[2], swapped[0]
        with self.assertRaises(runner.RunnerError):
            runner.parse_args(swapped)

    def _argv(self) -> list[str]:
        argv: list[str] = []
        for flag, _name, _path in runner.BINDING_SPECS:
            argv.extend((flag, "0" * 64))
        return argv

    def test_runner_preexistence_is_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = pathlib.Path(directory)
            final = parent / "final"
            staging = parent / "staging"
            control = parent / "control"
            final.mkdir()
            with (
                mock.patch.object(runner, "FINAL_ROOT", final),
                mock.patch.object(runner, "STAGING_ROOT", staging),
                mock.patch.object(runner, "CONTROL_ROOT", control),
            ):
                self.assertEqual(runner.main(self._argv()), 10)
            self.assertEqual(list(parent.iterdir()), [final])

    def test_runner_controlled_input_failure_retains_lease_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = pathlib.Path(directory)
            final = parent / "final"
            staging = parent / "staging"
            control = parent / "control"
            with (
                mock.patch.object(runner, "FINAL_ROOT", final),
                mock.patch.object(runner, "STAGING_ROOT", staging),
                mock.patch.object(runner, "CONTROL_ROOT", control),
                mock.patch.object(runner, "verify_bindings"),
                mock.patch.object(
                    runner.extractor, "extract",
                    side_effect=extractor.ClaimsError(
                        "INPUT_BINDING", "synthetic input failure",
                    ),
                ),
            ):
                self.assertEqual(runner.main(self._argv()), 21)
            self.assertFalse(final.exists())
            self.assertTrue((control / "lease.json").is_file())
            failure = json.loads(
                (control / "failure.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failure["failure_code"], "INPUT_BINDING")
            self.assertFalse((control / "authorization.json").exists())

    def test_runner_success_promotes_only_validated_four_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = pathlib.Path(directory)
            final = parent / "final"
            staging = parent / "staging"
            control = parent / "control"
            details = [
                GrammarTests()._open(
                    ["New Spot Trading Pairs: AAA/USDT."],
                ),
                detail(
                    "c" * 32,
                    root(paragraph("ordinary announcement")),
                ),
            ]
            payload = extractor.build_payload(details, dummy_bindings())
            payload["accepted_details"] = details
            verify = mock.Mock()
            with (
                mock.patch.object(runner, "FINAL_ROOT", final),
                mock.patch.object(runner, "STAGING_ROOT", staging),
                mock.patch.object(runner, "CONTROL_ROOT", control),
                mock.patch.object(runner, "verify_bindings", verify),
                mock.patch.object(
                    runner.extractor, "extract", return_value=payload,
                ),
            ):
                self.assertEqual(runner.main(self._argv()), 0)
            self.assertTrue(final.is_dir())
            self.assertEqual(
                {item.name for item in final.iterdir()},
                {"claims.jsonl", "ambiguity.jsonl", "coverage.jsonl", "summary.json"},
            )
            self.assertTrue((control / "authorization.json").is_file())
            self.assertFalse((control / "failure.json").exists())
            self.assertFalse(staging.exists())
            self.assertEqual(verify.call_count, 2)

    def test_committed_loader_revalidates_authority_and_live_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = pathlib.Path(directory)
            final = parent / "final"
            staging = parent / "staging"
            control = parent / "control"
            final.mkdir()
            control.mkdir()
            details = [
                GrammarTests()._open(
                    ["New Spot Trading Pairs: AAA/USDT."],
                ),
                detail(
                    "c" * 32,
                    root(paragraph("ordinary announcement")),
                ),
            ]
            bindings = actual_bindings()
            payload = extractor.build_payload(details, bindings)
            for name, raw in payload["payload_bytes"].items():
                (final / name).write_bytes(raw)
            (final / "summary.json").write_bytes(
                payload["summary_bytes"],
            )
            lease = {
                "experiment_id": extractor.EXPERIMENT_ID,
                "run_id": extractor.RUN_ID,
                "formal_command_sha256":
                    loader._formal_command_sha256(bindings),
                "expected_bindings_sha256": hashlib.sha256(
                    extractor.canonical_compact(bindings)
                ).hexdigest(),
            }
            (control / "lease.json").write_bytes(
                extractor.canonical_pretty(lease),
            )
            authorization = {
                "experiment_id": extractor.EXPERIMENT_ID,
                "run_id": extractor.RUN_ID,
                "summary_sha256": hashlib.sha256(
                    (final / "summary.json").read_bytes()
                ).hexdigest(),
                "payload_tree_sha256":
                    payload["summary"]["payload_tree_sha256"],
                "final_tree_sha256": loader.final_tree_sha256(final),
            }
            (control / "authorization.json").write_bytes(
                extractor.canonical_pretty(authorization),
            )
            with mock.patch.object(
                loader.extractor, "load_accepted_details",
                return_value=details,
            ):
                loaded = loader.load_committed(
                    REPO_ROOT, final_root=final,
                    staging_root=staging, control_root=control,
                )
            self.assertEqual(loaded.claim_count, 1)
            authorization["final_tree_sha256"] = "0" * 64
            (control / "authorization.json").write_bytes(
                extractor.canonical_pretty(authorization),
            )
            with (
                mock.patch.object(
                    loader.extractor, "load_accepted_details",
                    return_value=details,
                ),
                self.assertRaises(loader.ClaimsLoadError),
            ):
                loader.load_committed(
                    REPO_ROOT, final_root=final,
                    staging_root=staging, control_root=control,
                )

    def test_promotion_failure_removes_authorization_and_records_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = pathlib.Path(directory)
            final = parent / "final"
            staging = parent / "staging"
            control = parent / "control"
            details = [
                GrammarTests()._open(
                    ["New Spot Trading Pairs: AAA/USDT."],
                )
            ]
            payload = extractor.build_payload(details, dummy_bindings())
            payload["accepted_details"] = details
            with (
                mock.patch.object(runner, "FINAL_ROOT", final),
                mock.patch.object(runner, "STAGING_ROOT", staging),
                mock.patch.object(runner, "CONTROL_ROOT", control),
                mock.patch.object(runner, "verify_bindings"),
                mock.patch.object(
                    runner.extractor, "extract", return_value=payload,
                ),
                mock.patch.object(
                    runner.os, "rename", side_effect=OSError("synthetic"),
                ),
            ):
                self.assertEqual(runner.main(self._argv()), 23)
            self.assertFalse(final.exists())
            self.assertTrue(staging.is_dir())
            self.assertFalse((control / "authorization.json").exists())
            failure = json.loads(
                (control / "failure.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failure["failure_code"], "PROMOTION")

    def test_formal_paths_absent_and_no_runtime_forensics(self) -> None:
        self.assertFalse(runner.FINAL_ROOT.exists())
        self.assertFalse(runner.STAGING_ROOT.exists())
        self.assertFalse(runner.CONTROL_ROOT.exists())
        combined = (
            pathlib.Path(extractor.__file__).read_text(encoding="utf-8")
            + pathlib.Path(loader.__file__).read_text(encoding="utf-8")
            + pathlib.Path(runner.__file__).read_text(encoding="utf-8")
        ).lower()
        for forbidden in (
            "socket", "urllib", "requests", "api_key",
            "runtime_fingerprint", "addaudithook", "marshal",
        ):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
