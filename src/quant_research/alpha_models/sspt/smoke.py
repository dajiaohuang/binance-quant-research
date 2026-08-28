import argparse,json,tempfile
from pathlib import Path
import torch
from .core import SSPTConfig,SSPTModel,SSPTInferenceRequest,pretrain_loss,finetune_loss
from .checkpoint import save,load
ROOT=Path(__file__).resolve().parents[4]
def main():
 p=argparse.ArgumentParser(); p.add_argument('--device',choices=('cpu','cuda:0'),required=True); a=p.parse_args(); torch.manual_seed(20260827)
 if a.device.startswith('cuda') and not torch.cuda.is_available(): raise RuntimeError('CUDA_UNAVAILABLE')
 d=torch.device(a.device); m=SSPTModel(SSPTConfig()).to(d); opt=torch.optim.Adam(m.parameters(),1e-3); x=torch.randn(8,16,25,device=d)
 if a.device.startswith('cuda'): torch.cuda.reset_peak_memory_stats()
 before=next(m.parameters()).detach().clone(); loss=pretrain_loss(m.pretrain(x),torch.arange(8,device=d)%32,torch.arange(8,device=d)%16,torch.zeros(8,device=d)); loss.backward(); opt.step(); opt.zero_grad()
 map_loss=pretrain_loss(m.pretrain(x),torch.arange(8,device=d)%32,torch.arange(8,device=d)%16,torch.zeros(8,device=d),0.,0.,1.); opt.zero_grad(); map_loss.backward(); opt.step(); m.set_freeze_mode('embedding'); frozen=m.embed.weight.detach().clone(); opt=torch.optim.Adam([p for p in m.parameters() if p.requires_grad],1e-3); pred=m.infer(SSPTInferenceRequest(x,1000,1000)); loss2=finetune_loss(pred,torch.linspace(-1,1,8,device=d),torch.ones(8,dtype=torch.bool,device=d)); opt.zero_grad(); loss2.backward(); opt.step(); assert torch.equal(frozen,m.embed.weight)
 updated=not torch.equal(before,next(m.parameters()).detach()); peak=torch.cuda.max_memory_allocated() if a.device.startswith('cuda') else 0
 if peak >= 2*1024**3: raise RuntimeError('GPU_CAP')
 with tempfile.TemporaryDirectory() as td: save(m.cpu(),Path(td)/'c'); rebuilt=load(Path(td)/'c'); out=rebuilt.infer(SSPTInferenceRequest(x.cpu(),1000,1000))
 metrics={'device':a.device,'pretrain_loss':float(loss.detach()),'finetune_loss':float(loss2.detach()),'finite':bool(torch.isfinite(out).all()),'parameter_updated':updated,'peak_cuda_bytes':peak}
 path=ROOT/f"experiments/exp_20260827_008/artifacts/{'gpu' if a.device.startswith('cuda') else 'cpu'}_smoke.json"; path.write_text(json.dumps(metrics,sort_keys=True,indent=2)+'\n'); return 0
if __name__=='__main__': raise SystemExit(main())
