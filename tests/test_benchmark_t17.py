"""Harness tests only on generated targets or mocked child processes."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch,Mock
from benchmarks.t17 import percentile,summary,limits,workload,rss,PROFILES
from scripts import benchmark_t17 as cli
from scripts.summarize_t17 import render,physical_ram


class HarnessTests(unittest.TestCase):
    def test_owned_windows_worker_is_waited_before_file_cleanup(self):
        kernel=Mock()
        kernel.OpenProcess.return_value=123
        kernel.WaitForSingleObject.return_value=0
        with patch.object(cli.ctypes,'WinDLL',return_value=kernel,create=True),patch.object(cli.os,'kill') as kill:
            cli._terminate_windows_worker(42)
        kill.assert_called_once_with(42,cli.signal.SIGTERM)
        kernel.WaitForSingleObject.assert_called_once_with(123,10000)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_owned_windows_worker_wait_failure_closes_handle_and_fails(self):
        kernel=Mock()
        kernel.OpenProcess.return_value=123
        kernel.WaitForSingleObject.return_value=258
        with patch.object(cli.ctypes,'WinDLL',return_value=kernel,create=True),patch.object(cli.os,'kill'):
            with self.assertRaisesRegex(RuntimeError,'did not terminate'):
                cli._terminate_windows_worker(42)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_physical_ram_unknown_and_numeric_metadata(self):
        for environment in ({}, {'physical_memory':None}, {'physical_memory':{}},
                            {'physical_memory':{'total_bytes':None}}):
            with self.subTest(environment=environment):
                self.assertEqual(physical_ram(environment),'unknown physical RAM')
        self.assertEqual(physical_ram({'physical_memory':{'total_bytes':8*1073741824}}),
                         '8.00 GiB physical RAM')

    def test_nearest_rank_quantiles(self):
        values=list(range(1,101))
        self.assertEqual([percentile(values,p) for p in (50,95,99)],[50,95,99])
        self.assertEqual(percentile([9,1],50),1);self.assertIsNone(percentile([],95))
        with self.assertRaises(ValueError):percentile([1],0)

    def test_accounting_includes_expected_denials_and_timeouts(self):
        data=[dict(ms=10,status=200,queries=2,bytes=3),dict(ms=20,status=403,queries=1,bytes=5,expected_denial=True),
              dict(ms=30,status=599,queries=4,bytes=0,timeout=True)]
        result=summary(data,elapsed=2)
        self.assertEqual((result['errors'],result['timeouts'],result['expected_denials']),(1,1,1))
        self.assertEqual(result['throughput_per_second'],1.5);self.assertEqual(result['max_queries'],4)
        self.assertEqual(result['p99_ms'],30)
        self.assertEqual(result['server_errors'],1);self.assertEqual(result['conflicts'],0)

    def test_deterministic_roles_and_profiles(self):
        self.assertEqual(workload(50),workload(50));self.assertEqual(len(workload(50)),50)
        self.assertIn((903,None),workload(50));self.assertIn((923,3),workload(50))
        self.assertEqual(PROFILES['large-operational-proxy'],('medium',80,120))

    def test_limits_fail_closed(self):
        for key,value in [('samples',0),('samples',61),('workers',26),('rounds',4),('seconds',901),('memory_mb',1),('disk_mb',2049)]:
            with self.subTest(key=key),self.assertRaises(ValueError):limits(**{key:value})
        with self.assertRaises(ValueError):limits(workers=True)

    def test_existing_targets_and_real_sources_are_not_inputs(self):
        for profile in ('m5','mysql://production','C:/existing.sqlite'):
            with self.assertRaises(ValueError):cli.guarded(profile,limits())
        with TemporaryDirectory() as temp:
            control=Path(temp)/'control.json';control.write_text('{}')
            with self.assertRaises(ValueError):cli.worker(control)

    def fake_guard(self,kind):
        class Process:
            pid=987654321;returncode=None
            def poll(self):return self.returncode
            def wait(self,timeout=None):return self.returncode
        process=Process()
        def terminate(value):value.returncode=-15
        kwargs={}
        if kind=='rss_limit':kwargs.update(memory_mb=256)
        if kind=='temporary_disk_limit':kwargs.update(disk_mb=64)
        if kind=='wall_time_limit':kwargs.update(seconds=30)
        config=limits(**kwargs)
        memory=300*1024*1024 if kind=='rss_limit' else 1
        disk=65*1024*1024 if kind=='temporary_disk_limit' else 1
        times=iter([0,31 if kind=='wall_time_limit' else 1,32])
        with (patch.object(cli.subprocess,'Popen',return_value=process),patch.object(cli,'terminate_tree',side_effect=terminate) as stop,
             patch.object(cli,'rss',return_value=dict(peak=memory,available=True)),patch.object(cli,'disk_bytes',return_value=disk),
             patch.object(cli.time,'perf_counter',side_effect=lambda:next(times))):
            result=cli.guarded('small',config)
        stop.assert_called_once_with(process);self.assertEqual(result['status'],'INCOMPLETE');self.assertEqual(result['resource_stop'],kind)

    def test_memory_guard(self):self.fake_guard('rss_limit')
    def test_disk_guard(self):self.fake_guard('temporary_disk_limit')
    def test_wall_guard(self):self.fake_guard('wall_time_limit')

    def test_rss_measurement_is_real(self):
        value=rss();self.assertTrue(value['available']);self.assertGreater(value['peak'],0)

    def test_worker_redacts_exception_text(self):
        with TemporaryDirectory(prefix='inventraiq-t17-') as temp:
            root=Path(temp);control=root/'control.json'
            control.write_text(json.dumps(dict(purpose='new-synthetic-t17-only',profile='small',limits=limits())))
            with patch('benchmarks.t17.execute',side_effect=RuntimeError('SECRET-MUST-NOT-APPEAR')):
                with self.assertRaises(SystemExit):cli.worker(control)
            text=(root/'result.json').read_text();self.assertNotIn('SECRET-MUST-NOT-APPEAR',text)
            self.assertEqual(json.loads(text)['error_type'],'RuntimeError')


class DisposableSmokeTests(unittest.TestCase):
    def test_repeated_round_comparability_failure_injection_and_reconciliation(self):
        result=cli.guarded('small',limits(samples=2,rounds=2,workers=2,seconds=120))
        self.assertEqual(result['status'],'COMPLETED',result.get('result'))
        rounds=result['result']['rounds'];self.assertEqual(len(rounds),2)
        self.assertEqual(rounds[0]['facts'],rounds[1]['facts'])
        self.assertEqual(rounds[0]['imports'][0]['manifest_hash'],rounds[1]['imports'][0]['manifest_hash'])
        self.assertGreater(result['peak_rss_bytes'],10*1024*1024)
        for row in rounds:
            self.assertTrue(row['concurrency']['injected_rollback']['unchanged'])
            self.assertTrue(row['concurrency']['final_reconciliation']['ok'])
            self.assertGreater(row['concurrency']['final_reconciliation']['native_audit_records'],0)
            self.assertIn(row['revoked_assignment']['status'],(302,403))
            self.assertTrue(all(d['expected_denial'] for d in row['expected_denials']))
        self.assertIn('Independent manifest equation:',render(dict(profiles=[result])))
        rounds[0]['concurrency']['final_reconciliation']['stock']+=1
        with self.assertRaises(AssertionError):render(dict(profiles=[result]))

    def test_real_worker_deadline_termination_preserves_partial_evidence(self):
        result=cli.guarded('small',limits(samples=60,rounds=3,workers=25,seconds=30))
        self.assertEqual(result['status'],'INCOMPLETE')
        self.assertEqual(result['resource_stop'],'wall_time_limit')
        self.assertLess(result['wall_seconds'],45)
        self.assertIn('partial',result)
