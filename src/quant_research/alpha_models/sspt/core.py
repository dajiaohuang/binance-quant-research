from __future__ import annotations
from dataclasses import dataclass, fields
import math
import torch
from torch import nn

@dataclass(frozen=True)
class SSPTConfig:
    lookback:int=16; feature_count:int=25; d_model:int=128; heads:int=4; layers:int=2; ffn_dim:int=512; dropout:float=.1; scc_classes:int=32; ssc_classes:int=16
    @classmethod
    def from_dict(cls,d):
        if set(d)!={f.name for f in fields(cls)}: raise ValueError("config_keys")
        return cls(**d)
    def __post_init__(self):
        if self.lookback not in (16,32) or self.feature_count!=25 or self.d_model!=128 or self.heads!=4 or self.layers!=2 or self.ffn_dim!=512 or not 0<=self.dropout<1: raise ValueError("config")

@dataclass(frozen=True)
class SSPTInferenceRequest:
    features:torch.Tensor; formation_time_ms:int; known_at_ms:int; labels:None=None
    def validate(self,c):
        if self.labels is not None or type(self.formation_time_ms)is not int or type(self.known_at_ms)is not int or self.known_at_ms>self.formation_time_ms: raise ValueError("request")
        if self.features.ndim!=3 or self.features.shape[1:]!=(c.lookback,c.feature_count) or not torch.isfinite(self.features).all(): raise ValueError("shape")

class SSPTModel(nn.Module):
    def __init__(self,c:SSPTConfig):
        super().__init__(); self.config=c; self.embed=nn.Linear(25,128); self.position=nn.Parameter(torch.zeros(1,c.lookback,128))
        layer=nn.TransformerEncoderLayer(128,4,512,c.dropout,batch_first=True,norm_first=True); self.encoder=nn.TransformerEncoder(layer,2)
        self.scc=nn.Linear(128,c.scc_classes); self.ssc=nn.Linear(128,c.ssc_classes); self.map=nn.Linear(128,1); self.returns=nn.Linear(128,1)
    def encode(self,x): return self.encoder(self.embed(x)+self.position[:,:x.shape[1]]).mean(1)
    def pretrain(self,x):
        z=self.encode(x); return self.scc(z),self.ssc(z),self.map(z).squeeze(-1)
    def infer(self,r): r.validate(self.config); return self.returns(self.encode(r.features)).squeeze(-1)
    def set_freeze_mode(self,mode):
        if mode not in ("none","embedding","embedding_attention","encoder","backbone"): raise ValueError("freeze")
        for p in self.parameters(): p.requires_grad=True
        groups={"embedding":[self.embed,self.position],"embedding_attention":[self.embed,self.position,self.encoder.layers[0].self_attn,self.encoder.layers[1].self_attn],"encoder":[self.encoder],"backbone":[self.embed,self.position,self.encoder]}.get(mode,[])
        for item in groups:
            for p in ([item] if isinstance(item,nn.Parameter) else item.parameters()): p.requires_grad=False
        return tuple(n for n,p in self.named_parameters() if p.requires_grad)

def pretrain_loss(outputs,scc,ssc,map_y,alpha=1.,beta=1.,gamma=0.):
    a,b,c=outputs; return alpha*nn.functional.cross_entropy(a,scc)+beta*nn.functional.cross_entropy(b,ssc)+gamma*nn.functional.mse_loss(c,map_y)

def finetune_loss(pred,target,mask,epsilon=1.):
    valid=mask.bool(); p=pred[valid]; y=target[valid]
    if not len(p): raise ValueError("empty")
    mse=((p-y)**2).sum(); dy=y[:,None]-y[None,:]; dp=p[:,None]-p[None,:]
    rank=torch.relu(-(dp*dy)).sum()
    return mse+epsilon*rank
