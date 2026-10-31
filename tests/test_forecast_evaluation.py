"""Original unit-only synthetic sequences; no database or released dataset files."""
from copy import deepcopy
from datetime import date,timedelta
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch,MagicMock
import numpy as np
from forecasting.features import row,training
from forecasting.candidates import CANDIDATES,Candidate,baseline,predict
from forecasting.evaluation import plan,fold,score,aggregate,evaluate,select_champion
from forecasting.data import prepare_history
from ingestion.synthetic import SOURCE
from scripts.backtest import config,output_path,worker,guarded


def history(days=90,branch=1,sku=1):
    start=date(2001,1,1);end=(start+timedelta(days=days-1)).isoformat()
    contract=dict(version='inventraiq-forecast-data-v1',branch_id=branch,sku_id=sku,timezone='UTC',source_id=SOURCE,
        source_revision='synthetic-unit-test',manifest_hash='c'*64,provenance='synthetic_fixture',start=start.isoformat(),
        as_of=end,training_end=end,source_start=start.isoformat(),source_end=end)
    facts=[dict(business_date=(start+timedelta(days=i)).isoformat(),units=i%7,coverage='observed',
        branch_id=branch,sku_id=sku,source_revision=contract['source_revision'],provenance='synthetic_fixture') for i in range(days)]
    return prepare_history(contract,facts)


def cheap(candidate,values,start,horizon=7):
    return dict(predictions=[next((v for v in reversed(values) if v is not None),0)]*7,
        native=True,fallback_reason=None,error_type=None,fit_ms=0,inference_ms=0)


class FeatureTests(unittest.TestCase):
    def test_shifted_lag_and_rolling_arithmetic(self):
        values=list(range(40));r=row(values,35,'2001-01-01')
        self.assertEqual(r[3:7],[34,28,21,7]);self.assertEqual(r[7],31);self.assertEqual(r[8],20.5)
        self.assertEqual(r[9:],[7,28])

    def test_no_current_or_future_label_in_features(self):
        values=list(range(60));a=row(values,35,'2001-01-01')
        values[35:]=[1000000]*25
        np.testing.assert_equal(a,row(values,35,'2001-01-01'))

    def test_missing_lag_and_observed_zero(self):
        values=[0]*35;values[34]=None;r=row(values,35,'2001-01-01')
        self.assertTrue(math.isnan(r[3]));self.assertEqual(r[7],0);self.assertEqual(r[9],6)
        x,y=training(values,'2001-01-01');self.assertEqual(len(y),6);self.assertTrue(np.all(y==0))


class ProtocolTests(unittest.TestCase):
    def test_full_locked_and_validation_boundaries(self):
        p=plan(168)
        self.assertEqual(p['validation_origins'],list(range(55,111,7)))
        self.assertEqual(p['test_origins'],list(range(111,167,7)))
        self.assertEqual(p['locked_test_start'],112)

    def test_medium_limited_four_origins_no_test(self):
        p=plan(90);self.assertEqual(p['validation_origins'],[61,68,75,82]);self.assertEqual(p['test_origins'],[])
        self.assertEqual(plan(14)['validation_origins'],[])

    def test_fold_never_uses_future_training_labels(self):
        h=history();a=fold(h,61)
        for day in h['days'][62:]:day['units']=999999
        b=fold(h,61)
        self.assertEqual(a['training'],b['training']);self.assertEqual(len(a['evaluation']),7)
        self.assertEqual(a['contract']['training_end'],'2001-03-03')

    def test_locked_selection_isolation_and_once_only(self):
        h=history(168);calls=[]
        def recorder(c,v,s,horizon=7):calls.append((c.name,len(v)));return cheap(c,v,s)
        with patch('forecasting.evaluation.predict',side_effect=recorder):a=evaluate([h])
        test_names={name for name,length in calls if length>=112}
        self.assertEqual(test_names,{a['selection']['champion'],a['selection']['reference']})
        self.assertEqual(a['locked_test_evaluations'],1);self.assertEqual(len(a['folds']),8)
        changed=deepcopy(h)
        for d in changed['days'][112:]:d['units']=99999
        with patch('forecasting.evaluation.predict',side_effect=cheap):b=evaluate([changed])
        self.assertEqual(a['selection'],b['selection']);self.assertEqual(a['validation'],b['validation'])
        self.assertNotEqual(a['locked_test'],b['locked_test'])

    def test_deterministic_repeated_evaluation(self):
        with patch('forecasting.evaluation.predict',side_effect=cheap):
            a=evaluate([history(),history(branch=2)]);b=evaluate([history(),history(branch=2)])
        self.assertEqual(a,b);self.assertEqual(a['full_protocol_eligible_series'],0)

    def test_branch_sku_not_duplicated_or_merged(self):
        with self.assertRaises(ValueError):evaluate([history(),history()])
        with patch('forecasting.evaluation.predict',side_effect=cheap):r=evaluate([history(),history(branch=2)])
        self.assertEqual(set(r['validation']['naive']['groups']['branch']),{'1','2'})
        self.assertEqual(r['validation']['naive']['overall']['eligible_series'],2)


class CandidateTests(unittest.TestCase):
    def test_all_predictions_finite_nonnegative(self):
        for c in CANDIDATES:
            with self.subTest(c=c.name):
                result=predict(c,[float(i%7) for i in range(70)],'2001-01-01')
                self.assertEqual(len(result['predictions']),7)
                self.assertTrue(all(math.isfinite(p) and p>=0 for p in result['predictions']))
                self.assertIsNone(result['error_type'])

    def test_sba_sparse_and_all_zero(self):
        c=next(c for c in CANDIDATES if c.name=='sba_croston')
        self.assertEqual(baseline(c,[0]*56,7)[0],[0]*7)
        self.assertTrue(all(p>=0 for p in baseline(c,[0]*13+[3]+[0]*14+[4]+[0]*27,7)[0]))
        p,reason=baseline(c,[0,None,2]*20,7);self.assertEqual(reason,'croston_incomplete_calendar')
        self.assertTrue(all(v>=0 for v in p))

    def test_incomplete_legacy_is_withheld_and_fallback_labeled(self):
        c=next(c for c in CANDIDATES if c.name=='legacy_regression')
        with patch('ai_model.forecast_demand',side_effect=AssertionError('Unsafe zero fill')):
            r=predict(c,[1,None]*30,'2001-01-01')
        self.assertFalse(r['native']);self.assertEqual(r['fallback_reason'],'legacy_requires_complete_calendar')

    def test_failure_does_not_hide_behind_fallback(self):
        c=next(c for c in CANDIDATES if c.name=='observed_trend')
        with patch('sklearn.linear_model.LinearRegression.fit',side_effect=RuntimeError('private detail')):
            r=predict(c,[1]*70,'2001-01-01')
        self.assertEqual(r['error_type'],'RuntimeError');self.assertFalse(r['native'])
        self.assertNotIn('private detail',str(r));self.assertEqual(r['predictions'],[1]*7)

    def test_nonfinite_estimator_output_rejected_before_clipping(self):
        c=next(c for c in CANDIDATES if c.name=='observed_trend')
        with patch('sklearn.linear_model.LinearRegression.predict',return_value=np.asarray([math.nan])):
            r=predict(c,[1]*70,'2001-01-01')
        self.assertEqual(r['error_type'],'ValueError');self.assertFalse(r['native'])
        self.assertEqual(r['predictions'],[1]*7)

    def test_unapproved_candidate_and_horizon_refused(self):
        with self.assertRaises(ValueError):predict(Candidate('external','ml'),[1]*70,'2001-01-01')
        with self.assertRaises(ValueError):predict(CANDIDATES[0],[1]*70,'2001-01-01',28)

    def test_fold_only_preprocessor_training(self):
        from sklearn.impute import SimpleImputer
        original=SimpleImputer.fit;seen=[]
        def fitted(self,x,*args,**kw):seen.append(np.asarray(x).copy());return original(self,x,*args,**kw)
        c=next(c for c in CANDIDATES if c.name=='ridge1')
        with patch.object(SimpleImputer,'fit',new=fitted):r=predict(c,[i%7 if i%17 else None for i in range(62)],'2001-01-01')
        self.assertIsNone(r['error_type']);self.assertEqual(len(seen),1)
        self.assertLessEqual(np.nanmax(seen[0][:,0]),61)


class MetricTests(unittest.TestCase):
    def test_known_arithmetic_and_signed_bias(self):
        r=score([1]*7,[2]*7,list(range(28)))
        self.assertEqual(r['absolute_error'],7);self.assertEqual(r['bias_units'],7);self.assertEqual(r['cumulative_error'],7)
        self.assertAlmostEqual(r['mase'],1/7)

    def test_constant_scales_and_zero_denominators_undefined(self):
        s=score([0]*7,[0]*7,[0]*56)
        r=aggregate([dict(score=s,identity='1:1',native=True,fallback_reason=None,error_type=None,fit_ms=0,inference_ms=0)])
        self.assertIsNone(r['wape_percent']);self.assertIsNone(r['mase']);self.assertIsNone(r['rmsse']);self.assertEqual(r['mae'],0)

    def test_partial_horizon_not_called_complete_week(self):
        r=score([1,None,1,1,1,1,1],[2]*7,[1]*56)
        self.assertEqual(r['n'],6);self.assertIsNone(r['cumulative_error']);self.assertFalse(r['complete_horizon'])

    def test_invalid_predictions_not_silently_scored(self):
        for p in ([1]*6,[math.nan]*7,[-1]*7):
            with self.assertRaises(ValueError):score([1]*7,p,[1]*56)

    def test_zero_baseline_cannot_force_ml_victory(self):
        with patch('forecasting.evaluation.predict',return_value=dict(predictions=[0]*7,native=True,fallback_reason=None,error_type=None,fit_ms=0,inference_ms=0)):
            h=history()
            for d in h['days']:d['units']=0
            result=evaluate([h])
        self.assertFalse(result['selection']['promoted']);self.assertEqual(result['selection']['champion'],'naive')

    def test_branch_deterioration_blocks_global_gain(self):
        with patch('forecasting.evaluation.predict',side_effect=cheap):r=evaluate([history(),history(branch=2)])
        results=r['validation'];reference=select_champion(results)['reference']
        for c in CANDIDATES:
            if c.family=='ml':
                results[c.name]['overall']['absolute_7day_total_error']=0
                results[c.name]['groups']['branch']['1']['absolute_7day_total_error']=99999
        selected=select_champion(results)
        self.assertEqual(selected['champion'],reference);self.assertFalse(selected['promoted'])


class SafetyTests(unittest.TestCase):
    def test_actual_windows_worker_must_be_measurable(self):
        process=MagicMock();process.poll.return_value=None;process.returncode=-1;process.pid=123
        def spawn(*args,**kwargs):
            (Path(args[0][-1]).parent/'worker.pid').write_text('456')
            return process
        with patch('scripts.backtest.subprocess.Popen',side_effect=spawn),\
            patch('benchmarks.t17.rss',side_effect=[dict(peak=1,available=True),dict(peak=0,available=False)]),\
            patch('benchmarks.t17.disk_bytes',return_value=0),patch('scripts.backtest.os.kill'):
            result=guarded(config())
        self.assertEqual(result['resource_stop'],'rss_unavailable');self.assertEqual(result['status'],'INCOMPLETE')
        process.kill.assert_called_once()

    def test_resource_controller_refuses_over_budget_or_unmeasurable_worker(self):
        for reason,memory,disk,clock in (
            ('rss_limit',dict(peak=1025*1048576,available=True),0,[0,1,1]),
            ('disk_limit',dict(peak=1,available=True),129*1048576,[0,1,1]),
            ('rss_unavailable',dict(peak=0,available=False),0,[0,1,1]),
            ('wall_time_limit',dict(peak=1,available=True),0,[0,301,302])):
            with self.subTest(reason=reason):
                process=MagicMock();process.poll.return_value=None;process.returncode=-1;process.pid=123
                with patch('scripts.backtest.subprocess.Popen',return_value=process),patch('benchmarks.t17.rss',return_value=memory),\
                    patch('benchmarks.t17.disk_bytes',return_value=disk),patch('scripts.backtest.time.perf_counter',side_effect=clock):
                    result=guarded(config())
                self.assertEqual(result['status'],'INCOMPLETE');self.assertEqual(result['resource_stop'],reason)
                process.kill.assert_called_once();process.wait.assert_called_once()

    def test_bounded_synthetic_only_configuration(self):
        for kw in (dict(profile='m5'),dict(profile='mysql://production'),dict(skus=13),dict(seconds=901),dict(memory_mb=1)):
            with self.assertRaises(ValueError):config(**kw)

    def test_output_and_worker_target_refusal(self):
        for value in ('public/model.json','data/raw/model.json','data/private/t19/../escape.json'):
            with self.assertRaises(ValueError):output_path(value)
        with TemporaryDirectory() as temp:
            p=Path(temp)/'control.json';p.write_text('{}')
            with self.assertRaises(ValueError):worker(p)
