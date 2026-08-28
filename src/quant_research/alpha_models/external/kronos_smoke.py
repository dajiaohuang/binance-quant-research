from __future__ import annotations
import argparse, json, pandas as pd
from pathlib import Path
import torch
from .kronos_adapter import KronosRequest, infer, load_offline, ROOT

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--device", choices=("cpu","cuda:0"), required=True); a=p.parse_args()
    if a.device.startswith("cuda") and not torch.cuda.is_available(): raise RuntimeError("CUDA_UNAVAILABLE")
    predictor,evidence=load_offline(device=a.device)
    frame=pd.read_csv(ROOT/"data/raw/kronos_official_v1/samples/tests/data/regression_input.csv").iloc[:256]
    times=tuple(int(x.timestamp()*1000) for x in pd.to_datetime(frame.timestamps,utc=True)); formation=times[-1]; step=times[-1]-times[-2]
    values=tuple(tuple(float(row[c]) for c in ("open","high","low","close","volume","amount")) for _,row in frame.iterrows())
    req=KronosRequest(values,times,tuple(formation+(i+1)*step for i in range(24)),formation,formation)
    if a.device.startswith("cuda"): torch.cuda.reset_peak_memory_stats()
    with torch.inference_mode(): output, provenance=infer(predictor,req)
    metrics={"device":a.device,"rows":len(output),"finite":True,"provenance_sha256":provenance,"peak_cuda_bytes":torch.cuda.max_memory_allocated() if a.device.startswith("cuda") else 0,"checkpoint":evidence,"empirical_authorized":False}
    path=ROOT/f"experiments/exp_20260827_007/artifacts/{'gpu' if a.device.startswith('cuda') else 'cpu'}_smoke_metrics.json"
    path.write_text(json.dumps(metrics,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return 0
if __name__=="__main__": raise SystemExit(main())
