from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import sys
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parents[4]
RAW = ROOT / "data/raw/kronos_official_v1"
THIRD = ROOT / "third_party/kronos/67b630e67f6a18c9e9be918d9b4337c960db1e9a"
ART = ROOT / "experiments/exp_20260827_007/artifacts"
COMMIT = "67b630e67f6a18c9e9be918d9b4337c960db1e9a"
MODELS = {
    "Kronos-mini": "f4e68697d9d5aed55cef5c96aabc3376bcad9f81",
    "Kronos-small": "901c26c1332695a2a8f243eb2f37243a37bea320",
    "Kronos-base": "2b554741eca47781b64468546e77fef3e85130e6",
    "Kronos-Tokenizer-2k": "26966d0035065a0cae0ebad7af8ece35bc1fb51c",
    "Kronos-Tokenizer-base": "0e0117387f39004a9016484a186a908917e22426",
}
MAX = {"config.json": 65536, "model.safetensors": 600_000_000, "source": 2_000_000, "csv": 20_000_000}

def _fetch(url: str, target: Path, cap: int) -> dict:
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "quant-research-kronos-freezer/1"})
    with urllib.request.urlopen(req, timeout=60) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP_{response.status}")
        data = response.read(cap + 1)
        final_url = response.geturl()
    if len(data) > cap:
        raise RuntimeError("BODY_LIMIT")
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=".download-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name): os.unlink(name)
    parsed=urllib.parse.urlsplit(final_url)
    return {"path":target.relative_to(ROOT).as_posix(),"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"url":url,"final_host":parsed.hostname,"final_path":parsed.path}

def sanitize_and_freeze() -> None:
    path=ART/"download_manifest.json"; doc=json.loads(path.read_text(encoding="utf-8"))
    for row in doc["entries"]:
        final=row.pop("final_url",None)
        if final:
            parsed=urllib.parse.urlsplit(final); row["final_host"]=parsed.hostname; row["final_path"]=parsed.path
    path.write_text(json.dumps(doc,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    files=[{k:r[k] for k in ("path","bytes","sha256")} for r in doc["entries"] if r.get("path","").endswith(("config.json","model.safetensors"))]
    (ART/"checkpoint_manifest.json").write_text(json.dumps({"files":files},indent=2,sort_keys=True)+"\n",encoding="utf-8")

def fetch_golden() -> None:
    doc=json.loads((ART/"download_manifest.json").read_text(encoding="utf-8"))
    for rel in ("tests/data/generate_regression_output.py","tests/data/regression_output_256.csv","tests/data/regression_output_512.csv"):
        doc["entries"].append(_fetch(f"https://raw.githubusercontent.com/shiyu-coder/Kronos/{COMMIT}/{rel}",RAW/"samples"/rel,MAX["csv"]))
    doc["network_request_count"]+=3
    (ART/"download_manifest.json").write_text(json.dumps(doc,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")

def main() -> int:
    entries = []
    for repo, rev in MODELS.items():
        for filename in ("config.json", "model.safetensors"):
            entries.append(_fetch(f"https://huggingface.co/NeoQuasar/{repo}/resolve/{rev}/{filename}", RAW/repo/rev/filename, MAX[filename]))
    source_files = ("LICENSE", "model/__init__.py", "model/kronos.py", "model/module.py")
    for rel in source_files:
        raw = RAW/"source"/COMMIT/rel
        entries.append(_fetch(f"https://raw.githubusercontent.com/shiyu-coder/Kronos/{COMMIT}/{rel}", raw, MAX["source"]))
        dst = THIRD/rel; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(raw, dst)
    for rel in ("finetune_csv/data/HK_ali_09988_kline_5min_all.csv", "tests/data/regression_input.csv"):
        try: entries.append(_fetch(f"https://raw.githubusercontent.com/shiyu-coder/Kronos/{COMMIT}/{rel}", RAW/"samples"/rel, MAX["csv"]))
        except Exception as exc: entries.append({"path": rel, "outcome": type(exc).__name__})
    ART.mkdir(parents=True, exist_ok=True)
    out = json.dumps({"entries": entries, "network_request_count": len(entries), "retry_count": 0}, ensure_ascii=False, indent=2, sort_keys=True)+"\n"
    (ART/"download_manifest.json").write_text(out, encoding="utf-8")
    return 0

if __name__ == "__main__":
    if "--fetch-golden" in sys.argv: fetch_golden(); raise SystemExit(0)
    if "--sanitize-existing" in sys.argv: sanitize_and_freeze(); raise SystemExit(0)
    raise SystemExit(main())
