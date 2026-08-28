import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
import torch
from quant_research.alpha_models.external.kronos_adapter import KronosRequest, _validate_request, load_offline, PAIRINGS, ROOT, _check_checkpoint

class KronosContractTests(unittest.TestCase):
    def request(self):
        return KronosRequest(((1.,2.,.5,1.5,10.,15.),), (1000,), (2000,), 1000, 1000)
    def test_valid_request(self): _validate_request(self.request(), 2048)
    def test_label_isolation(self):
        with self.assertRaises(ValueError): _validate_request(KronosRequest(self.request().values,(1000,),(2000,),1000,1000,labels=()),2048)
    def test_future_known_at_rejected(self):
        with self.assertRaises(ValueError): _validate_request(KronosRequest(self.request().values,(1000,),(2000,),1000,1001),2048)
    def test_bad_feature_width_rejected(self):
        with self.assertRaises(ValueError): _validate_request(KronosRequest(((1.,2.),),(1000,),(2000,),1000,1000),2048)
    def test_context_limit(self):
        with self.assertRaises(ValueError): _validate_request(KronosRequest(self.request().values*2,(500,1000),(2000,),1000,1000),1)
    def test_duplicate_timestamp_rejected(self):
        with self.assertRaises(ValueError): _validate_request(KronosRequest(self.request().values*2,(1000,1000),(2000,),1000,1000),2048)
    def test_bad_ohlc_and_negative_flow_rejected(self):
        with self.assertRaises(ValueError): _validate_request(KronosRequest(((2.,1.,.5,1.5,-1.,1.),),(1000,),(2000,),1000,1000),2048)
    def test_exact_pairing_registry_and_wrong_pair_rejected(self):
        self.assertEqual(set(PAIRINGS),{"Kronos-mini","Kronos-small","Kronos-base"})
        with self.assertRaises(ValueError): load_offline("Kronos-mini",tokenizer_name="Kronos-Tokenizer-base")
    def test_runtime_offline(self):
        with patch("urllib.request.urlopen",side_effect=AssertionError("network")):
            predictor,_=load_offline("Kronos-mini","cpu")
            self.assertEqual(predictor.max_context,2048)
    def test_manifest_tamper_rejected_before_safetensors(self):
        model_path=ROOT/"data/raw/kronos_official_v1/Kronos-mini"/PAIRINGS["Kronos-mini"][0]
        with patch("quant_research.alpha_models.external.kronos_adapter._sha",return_value="0"*64):
            with self.assertRaisesRegex(ValueError,"checkpoint_binding"): _check_checkpoint(model_path)
    def test_official_small_256_golden(self):
        predictor,_=load_offline("Kronos-small","cpu","Kronos-Tokenizer-base")
        root=ROOT/"data/raw/kronos_official_v1/samples/tests/data"
        frame=pd.read_csv(root/"regression_input.csv",parse_dates=["timestamps"])
        expected=pd.read_csv(root/"regression_output_256.csv")
        random_seed=123; np.random.seed(random_seed); torch.manual_seed(random_seed)
        cols=["open","high","low","close","volume","amount"]
        with torch.inference_mode(): actual=predictor.predict(frame.iloc[:256][cols],frame.timestamps.iloc[:256],frame.timestamps.iloc[256:264].reset_index(drop=True),8,T=1.0,top_k=1,top_p=1.0,sample_count=1,verbose=False)
        np.testing.assert_allclose(actual[cols].to_numpy(),expected[cols].to_numpy(),rtol=2e-5,atol=2e-5)
if __name__ == "__main__": unittest.main()
