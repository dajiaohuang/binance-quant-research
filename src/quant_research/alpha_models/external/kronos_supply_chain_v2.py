from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import BinaryIO, Callable
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[4]
EXPECTED_MANIFEST = ROOT / "experiments/exp_20260827_009/expected_manifest.json"
ACQUISITION_MANIFEST = ROOT / "experiments/exp_20260827_009/artifacts/acquisition_manifest.json"
RAW_ROOT = ROOT / "data/raw/kronos_official_v2"
VENDOR_ROOT = ROOT / "third_party/kronos/67b630e67f6a18c9e9be918d9b4337c960db1e9a"
EXPECTED_ROWS = 19
EXPECTED_TOTAL_BYTES = 562_430_552
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ORIGIN_HOSTS = frozenset(("huggingface.co", "raw.githubusercontent.com"))
HF_REDIRECT_HOSTS = frozenset(("huggingface.co", "us.aws.cdn.hf.co"))
SOURCE_PATHS = (
    "LICENSE",
    "model/__init__.py",
    "model/kronos.py",
    "model/module.py",
)


def _reject_constant(value: str) -> None:
    raise ValueError(f"nonfinite:{value}")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _strict_json_bytes(raw: bytes) -> object:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("bom")
    return json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=_pairs,
        parse_constant=_reject_constant,
    )


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(document: object) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _validate_url(url: str, allowed_hosts: frozenset[str]) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or not parsed.path.startswith("/")
        or parsed.fragment
    ):
        raise ValueError("url_scope")
    return parsed


def _validate_expected_row(row: object, ordinal: int) -> dict[str, object]:
    if type(row) is not dict:
        raise ValueError("manifest_row")
    source_kind = row.get("source_kind")
    common = {
        "ordinal", "source_kind", "revision", "immutable_url", "path",
        "expected_bytes", "expected_sha256", "authority_oid_kind", "authority_oid",
    }
    expected_keys = common | ({"xet_oid"} if source_kind == "HF_LFS" else set())
    if set(row) != expected_keys or row.get("ordinal") != ordinal:
        raise ValueError("manifest_row_keys")
    if source_kind not in {"HF_GIT_BLOB", "HF_LFS", "GITHUB_BLOB"}:
        raise ValueError("source_kind")
    if type(row["revision"]) is not str or not HEX40.fullmatch(row["revision"]):
        raise ValueError("revision")
    if type(row["expected_bytes"]) is not int or row["expected_bytes"] <= 0:
        raise ValueError("expected_bytes")
    if type(row["expected_sha256"]) is not str or not HEX64.fullmatch(row["expected_sha256"]):
        raise ValueError("expected_sha256")
    if type(row["immutable_url"]) is not str:
        raise ValueError("immutable_url")
    parsed = _validate_url(row["immutable_url"], ORIGIN_HOSTS)
    if row["revision"] not in parsed.path or parsed.query:
        raise ValueError("immutable_url_revision")
    if type(row["path"]) is not str or not row["path"].startswith("data/raw/kronos_official_v2/"):
        raise ValueError("path")
    path = Path(row["path"])
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("path_scope")
    if source_kind == "HF_LFS":
        if row["authority_oid_kind"] != "SHA256_LFS_OID" or row["authority_oid"] != row["expected_sha256"]:
            raise ValueError("lfs_authority")
        if type(row["xet_oid"]) is not str or not HEX64.fullmatch(row["xet_oid"]):
            raise ValueError("xet_oid")
    else:
        if row["authority_oid_kind"] != "GIT_SHA1" or type(row["authority_oid"]) is not str or not HEX40.fullmatch(row["authority_oid"]):
            raise ValueError("git_authority")
    return row


def load_expected_manifest(path: Path = EXPECTED_MANIFEST, expected_sha256: str | None = None) -> dict[str, object]:
    raw = path.read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and (not HEX64.fullmatch(expected_sha256) or actual_sha != expected_sha256):
        raise ValueError("expected_manifest_binding")
    document = _strict_json_bytes(raw)
    if type(document) is not dict or set(document) != {"schema_version", "row_count", "total_bytes", "files"}:
        raise ValueError("expected_manifest_schema")
    if document["schema_version"] != "KRONOS_EXPECTED_MANIFEST_V2" or document["row_count"] != EXPECTED_ROWS or document["total_bytes"] != EXPECTED_TOTAL_BYTES:
        raise ValueError("expected_manifest_constants")
    files = document["files"]
    if type(files) is not list or len(files) != EXPECTED_ROWS:
        raise ValueError("expected_manifest_count")
    checked = [_validate_expected_row(row, index) for index, row in enumerate(files, 1)]
    if sum(row["expected_bytes"] for row in checked) != EXPECTED_TOTAL_BYTES:
        raise ValueError("expected_manifest_total")
    if len({row["path"] for row in checked}) != EXPECTED_ROWS or len({row["immutable_url"] for row in checked}) != EXPECTED_ROWS:
        raise ValueError("expected_manifest_uniqueness")
    return {**document, "expected_manifest_sha256": actual_sha}


class _BoundedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin_host: str) -> None:
        super().__init__()
        self.origin_host = origin_host
        self.redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        self.redirect_count += 1
        if self.redirect_count > 1 or self.origin_host != "huggingface.co":
            raise ValueError("redirect_scope")
        _validate_url(newurl, HF_REDIRECT_HOSTS)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_response(url: str, timeout_seconds: int = 300) -> tuple[BinaryIO, int]:
    origin = _validate_url(url, ORIGIN_HOSTS)
    redirect_handler = _BoundedRedirectHandler(origin.hostname or "")
    opener = urllib.request.build_opener(redirect_handler)
    request = urllib.request.Request(url, headers={"User-Agent": "quant-research-kronos-v2/1"})
    response = opener.open(request, timeout=timeout_seconds)
    return response, redirect_handler.redirect_count


def _sanitized_transport(final_url: str, status: int, redirect_count: int) -> dict[str, object]:
    allowed = HF_REDIRECT_HOSTS if redirect_count else ORIGIN_HOSTS
    final = _validate_url(final_url, allowed)
    return {
        "final_host": final.hostname,
        "final_path": final.path,
        "http_status": status,
        "redirect_count": redirect_count,
    }


def _stream_and_publish(
    row: dict[str, object],
    response: BinaryIO,
    redirect_count: int,
    target: Path,
) -> dict[str, object]:
    status = int(getattr(response, "status"))
    if status != 200:
        raise ValueError("http_status")
    transport = _sanitized_transport(str(response.geturl()), status, redirect_count)
    if target.exists():
        raise FileExistsError("target_preexists")
    target.parent.mkdir(parents=True, exist_ok=True)
    expected_bytes = int(row["expected_bytes"])
    digest = hashlib.sha256()
    git_blob_digest = None
    if row["source_kind"] in {"HF_GIT_BLOB", "GITHUB_BLOB"}:
        git_blob_digest = hashlib.sha1()
        git_blob_digest.update(b"blob " + str(expected_bytes).encode("ascii") + b"\0")
    total = 0
    descriptor, temporary_name = tempfile.mkstemp(prefix=".kronos-v2-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            while True:
                block = response.read(min(1024 * 1024, expected_bytes - total + 1))
                if not block:
                    break
                total += len(block)
                if total > expected_bytes:
                    raise ValueError("body_oversize")
                output.write(block)
                digest.update(block)
                if git_blob_digest is not None:
                    git_blob_digest.update(block)
            output.flush()
            os.fsync(output.fileno())
        if total != expected_bytes:
            raise ValueError("body_short")
        actual_sha = digest.hexdigest()
        if actual_sha != row["expected_sha256"]:
            raise ValueError("body_hash")
        if git_blob_digest is not None and git_blob_digest.hexdigest() != row["authority_oid"]:
            raise ValueError("git_blob_oid")
        os.replace(temporary_name, target)
        return {
            "bytes": total,
            "immutable_url": row["immutable_url"],
            "ordinal": row["ordinal"],
            "path": row["path"],
            "sha256": actual_sha,
            **transport,
        }
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _target_for(row: dict[str, object]) -> Path:
    target = (ROOT / str(row["path"])).resolve()
    if os.path.commonpath((str(target), str(RAW_ROOT.resolve()))) != str(RAW_ROOT.resolve()):
        raise ValueError("target_scope")
    return target


def _tree_paths(raw_root: Path = RAW_ROOT) -> set[str]:
    if not raw_root.exists():
        return set()
    return {
        path.relative_to(ROOT).as_posix()
        for path in raw_root.rglob("*")
        if path.is_file()
    }


def verify_raw_tree(manifest: dict[str, object], raw_root: Path = RAW_ROOT) -> list[dict[str, object]]:
    expected_rows = manifest["files"]
    expected_paths = {str(row["path"]) for row in expected_rows}
    if _tree_paths(raw_root) != expected_paths:
        raise ValueError("raw_path_bijection")
    verified: list[dict[str, object]] = []
    for row in expected_rows:
        target = _target_for(row)
        size = target.stat().st_size
        digest = _sha256_path(target)
        if size != row["expected_bytes"] or digest != row["expected_sha256"]:
            raise ValueError("raw_file_binding")
        verified.append({"bytes": size, "path": row["path"], "sha256": digest})
    if sum(row["bytes"] for row in verified) != EXPECTED_TOTAL_BYTES:
        raise ValueError("raw_total")
    return verified


def verify_vendored_sources(manifest: dict[str, object]) -> list[dict[str, object]]:
    by_suffix = {str(row["path"]).split("/source/", 1)[-1].split("/", 1)[-1]: row for row in manifest["files"] if row["source_kind"] == "GITHUB_BLOB"}
    evidence: list[dict[str, object]] = []
    for relative in SOURCE_PATHS:
        expected = by_suffix.get(relative)
        vendor = VENDOR_ROOT / relative
        if expected is None or not vendor.is_file() or vendor.stat().st_size != expected["expected_bytes"] or _sha256_path(vendor) != expected["expected_sha256"]:
            raise ValueError("vendored_source_binding")
        evidence.append({"bytes": expected["expected_bytes"], "path": vendor.relative_to(ROOT).as_posix(), "sha256": expected["expected_sha256"]})
    return evidence


def acquire(expected_manifest_sha256: str, opener: Callable[[str], tuple[BinaryIO, int]] = _open_response) -> dict[str, object]:
    manifest = load_expected_manifest(expected_sha256=expected_manifest_sha256)
    if RAW_ROOT.exists() or ACQUISITION_MANIFEST.exists():
        raise FileExistsError("formal_preexists")
    receipts: list[dict[str, object]] = []
    for row in manifest["files"]:
        response, redirects = opener(str(row["immutable_url"]))
        try:
            receipts.append(_stream_and_publish(row, response, redirects, _target_for(row)))
        finally:
            response.close()
    verified = verify_raw_tree(manifest)
    vendored = verify_vendored_sources(manifest)
    document = {
        "application_network_policy": "HTTPS_ALLOWLIST_MAX_ONE_REDIRECT_NO_RETRY",
        "expected_manifest_sha256": manifest["expected_manifest_sha256"],
        "files": receipts,
        "logical_request_count": EXPECTED_ROWS,
        "network_retry_count": 0,
        "raw_file_count": len(verified),
        "raw_total_bytes": sum(row["bytes"] for row in verified),
        "schema_version": "KRONOS_ACQUISITION_MANIFEST_V2",
        "vendored_sources": vendored,
    }
    ACQUISITION_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(ACQUISITION_MANIFEST, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, "wb") as output:
        output.write(_canonical_json(document))
        output.flush()
        os.fsync(output.fileno())
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args(argv)
    acquire(args.expected_manifest_sha256)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
