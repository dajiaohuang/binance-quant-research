import numpy as np

class TrainMinMax:
    def fit(self,x):
        if hasattr(self,'lo') or not np.isfinite(x).all(): raise ValueError('fit')
        self.lo=np.min(x,axis=(0,1)); self.hi=np.max(x,axis=(0,1)); return self
    def transform(self,x): return (x-self.lo)/np.where(self.hi>self.lo,self.hi-self.lo,1)

def causal_features(ohlcv):
    x=np.asarray(ohlcv,float); out=[]
    for t in range(len(x)):
        row=[]
        for j in range(5):
            for w in (5,10,20,30): row.append(x[max(0,t-w+1):t+1,j].mean())
            row.append(x[t,j])
        out.append(row)
    return np.asarray(out)

def purged_split_indices(n,train_end,valid_end,horizon=1,embargo=0):
    train=np.arange(0,max(0,train_end-horizon)); valid=np.arange(train_end+embargo,max(train_end+embargo,valid_end-horizon)); test=np.arange(valid_end+embargo,n-horizon)
    return train,valid,test
