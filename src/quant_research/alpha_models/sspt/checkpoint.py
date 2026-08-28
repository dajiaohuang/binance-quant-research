import hashlib,json
from pathlib import Path
from safetensors.torch import save_file,load_file
from .core import SSPTConfig,SSPTModel
def save(model,path):
    path=Path(path); path.mkdir(parents=True,exist_ok=False); w=path/"model.safetensors"; save_file(model.state_dict(),str(w)); raw=w.read_bytes(); meta={"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"config":model.config.__dict__}; (path/"manifest.json").write_text(json.dumps(meta,sort_keys=True)+"\n")
def load(path):
    path=Path(path); meta=json.loads((path/"manifest.json").read_text()); raw=(path/"model.safetensors").read_bytes()
    if len(raw)!=meta["bytes"] or hashlib.sha256(raw).hexdigest()!=meta["sha256"]: raise ValueError("tamper")
    m=SSPTModel(SSPTConfig(**meta["config"])); m.load_state_dict(load_file(str(path/"model.safetensors")),strict=True); return m
