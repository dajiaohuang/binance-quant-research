from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import json
from pathlib import Path
import sys
from typing import Sequence

import numpy as np
import pandas as pd
import torch
from safetensors import safe_open

ROOT = Path(__file__).resolve().parents[4]
RAW = ROOT / "data/raw/kronos_official_v1"
VENDOR = ROOT / "third_party/kronos/67b630e67f6a18c9e9be918d9b4337c960db1e9a"
PAIRINGS = {
 "Kronos-mini": ("f4e68697d9d5aed55cef5c96aabc3376bcad9f81","Kronos-Tokenizer-2k","26966d0035065a0cae0ebad7af8ece35bc1fb51c",2048),
 "Kronos-small": ("901c26c1332695a2a8f243eb2f37243a37bea320","Kronos-Tokenizer-base","0e0117387f39004a9016484a186a908917e22426",512),
 "Kronos-base": ("2b554741eca47781b64468546e77fef3e85130e6","Kronos-Tokenizer-base","0e0117387f39004a9016484a186a908917e22426",512),
}

@dataclass(frozen=True)
class KronosRequest:
    values: tuple[tuple[float, ...], ...]
    context_timestamps_ms: tuple[int, ...]
    future_timestamps_ms: tuple[int, ...]
    formation_time_ms: int
    known_at_ms: int
    labels: None = None

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _validate_request(req: KronosRequest, maximum: int) -> None:
    if req.labels is not None or type(req.formation_time_ms) is not int or type(req.known_at_ms) is not int or req.known_at_ms > req.formation_time_ms:
        raise ValueError("clock_or_label")
    if not req.values or len(req.values) > maximum or len(req.values) != len(req.context_timestamps_ms) or not req.future_timestamps_ms:
        raise ValueError("shape")
    if any(len(row) != 6 or not all(np.isfinite(x) for x in row) for row in req.values): raise ValueError("ohlcva")
    if any(b<=a for a,b in zip(req.context_timestamps_ms,req.context_timestamps_ms[1:])) or any(b<=a for a,b in zip(req.future_timestamps_ms,req.future_timestamps_ms[1:])): raise ValueError("timestamps")
    for o,h,l,c,v,a in req.values:
        if min(o,h,l,c)<=0 or h<max(o,l,c) or l>min(o,h,c) or v<0 or a<0: raise ValueError("market_schema")
    if req.context_timestamps_ms[-1] > req.formation_time_ms or req.future_timestamps_ms[0] <= req.formation_time_ms: raise ValueError("formation")

def _check_checkpoint(path: Path) -> dict:
    config_path, weight_path = path/"config.json", path/"model.safetensors"
    rows=json.loads((ROOT/"experiments/exp_20260827_007/artifacts/checkpoint_manifest.json").read_text(encoding="utf-8"))["files"]
    index={r["path"]:r for r in rows}
    for candidate in (config_path,weight_path):
        expected=index.get(candidate.relative_to(ROOT).as_posix())
        if expected is None or candidate.stat().st_size!=expected["bytes"] or _sha(candidate)!=expected["sha256"]: raise ValueError("checkpoint_binding")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    with safe_open(weight_path, framework="pt", device="cpu") as f:
        keys = tuple(sorted(f.keys()))
        if not keys: raise ValueError("empty_checkpoint")
        for key in keys:
            t = f.get_tensor(key)
            if t.dtype not in (torch.float16, torch.bfloat16, torch.float32, torch.int64): raise ValueError("dtype")
    return {"config": config, "config_sha256": _sha(config_path), "weights_sha256": _sha(weight_path), "tensor_count": len(keys)}

def load_offline(model_name: str = "Kronos-mini", device: str = "cpu", tokenizer_name: str | None = None):
    if model_name not in PAIRINGS: raise ValueError("unsupported_pair")
    mrev, tok, trev, maximum = PAIRINGS[model_name]
    if tokenizer_name is not None and tokenizer_name != tok: raise ValueError("unsupported_pair")
    model_path, tok_path = RAW/model_name/mrev, RAW/tok/trev
    evidence = {"model": _check_checkpoint(model_path), "tokenizer": _check_checkpoint(tok_path)}
    sys.path.insert(0, str(VENDOR))
    try:
        module = importlib.import_module("model")
        tokenizer = module.KronosTokenizer.from_pretrained(tok_path, local_files_only=True)
        model = module.Kronos.from_pretrained(model_path, local_files_only=True)
    finally:
        if sys.path[0] == str(VENDOR): sys.path.pop(0)
    tokenizer.eval(); model.eval()
    return module.KronosPredictor(model, tokenizer, device=device, max_context=maximum), evidence

def infer(predictor, req: KronosRequest, seed: int = 20260827) -> tuple[pd.DataFrame, str]:
    _validate_request(req, predictor.max_context)
    torch.manual_seed(seed)
    cols = ["open", "high", "low", "close", "volume", "amount"]
    frame = pd.DataFrame(np.asarray(req.values), columns=cols)
    x = pd.to_datetime(req.context_timestamps_ms, unit="ms", utc=True)
    y = pd.to_datetime(req.future_timestamps_ms, unit="ms", utc=True)
    output = predictor.predict(frame, pd.Series(x), pd.Series(y), len(y), sample_count=1, verbose=False)
    if output.shape != (len(y), 6) or not np.isfinite(output.to_numpy()).all(): raise ValueError("output")
    preimage = json.dumps({"formation_time_ms": req.formation_time_ms, "horizon": len(y), "known_at_ms": req.known_at_ms, "output_sha256": hashlib.sha256(output.to_csv(index=True).encode()).hexdigest()}, sort_keys=True, separators=(",", ":"))
    return output, hashlib.sha256(preimage.encode()).hexdigest()
