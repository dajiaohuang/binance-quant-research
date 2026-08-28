import tempfile,unittest
from pathlib import Path
import numpy as np, torch
from quant_research.alpha_models.sspt.core import *
from quant_research.alpha_models.sspt.data import *
from quant_research.alpha_models.sspt.checkpoint import save,load
class SSPTTests(unittest.TestCase):
 def test_config_unknown_and_shape_label(self):
  with self.assertRaises(ValueError): SSPTConfig.from_dict({'x':1})
  m=SSPTModel(SSPTConfig()); r=SSPTInferenceRequest(torch.zeros(2,16,25),1,1); self.assertEqual(m.infer(r).shape,(2,))
  with self.assertRaises(ValueError): m.infer(SSPTInferenceRequest(r.features,1,1,labels=()))
 def test_scaler_train_only(self):
  tr=np.arange(100.).reshape(2,10,5); s=TrainMinMax().fit(tr); a=s.transform(tr); s.transform(np.ones_like(tr)*1e9); np.testing.assert_array_equal(a,s.transform(tr))
 def test_rolling_is_causal(self):
  x=np.arange(100.).reshape(20,5); a=causal_features(x); x[11:]=1e9; np.testing.assert_array_equal(a[:11],causal_features(x)[:11])
 def test_purge_embargo(self):
  a,b,c=purged_split_indices(30,10,20,1,0); self.assertLess(a[-1],9); self.assertEqual(b[0],10); self.assertEqual(c[0],20)
 def test_losses_known_tie_mask_permutation(self):
  p=torch.tensor([0.,1.,9.]); y=torch.tensor([0.,2.,9.]); mask=torch.tensor([1,1,0],dtype=torch.bool); a=finetune_loss(p,y,mask); b=finetune_loss(p[[1,0,2]],y[[1,0,2]],mask[[1,0,2]]); self.assertAlmostEqual(float(a),float(b))
 def test_exact_equation5_vector(self):
  self.assertAlmostEqual(float(finetune_loss(torch.tensor([.1,.2]),torch.tensor([.2,.1]),torch.tensor([1,1],dtype=torch.bool),5.)),.12,places=6)
 def test_gamma_zero_blocks_map_gradient(self):
  a=torch.zeros(2,2,requires_grad=True); b=torch.zeros(2,2,requires_grad=True); c=torch.ones(2,requires_grad=True); pretrain_loss((a,b,c),torch.zeros(2,dtype=torch.long),torch.zeros(2,dtype=torch.long),torch.zeros(2)).backward(); self.assertTrue(c.grad is None or not c.grad.any())
 def test_freeze_modes(self):
  m=SSPTModel(SSPTConfig()); self.assertTrue(m.set_freeze_mode('embedding')); self.assertFalse(m.embed.weight.requires_grad); self.assertTrue(m.returns.weight.requires_grad)
 def test_checkpoint_roundtrip_tamper(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'c'; save(SSPTModel(SSPTConfig()),p); load(p); w=p/'model.safetensors'; w.write_bytes(w.read_bytes()+b'x')
   with self.assertRaises(ValueError): load(p)
 def test_backward_updates(self):
  m=SSPTModel(SSPTConfig()); x=torch.randn(4,16,25); before=m.embed.weight.detach().clone(); opt=torch.optim.SGD(m.parameters(),.01); pretrain_loss(m.pretrain(x),torch.zeros(4,dtype=torch.long),torch.zeros(4,dtype=torch.long),torch.zeros(4)).backward(); opt.step(); self.assertFalse(torch.equal(before,m.embed.weight))
if __name__=='__main__': unittest.main()
