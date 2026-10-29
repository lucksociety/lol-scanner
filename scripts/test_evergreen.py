import importlib.util,json,pathlib,tempfile,unittest,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class ArchiveTests(unittest.TestCase):
    def test_captures_are_market_free(self):
        m=module(ROOT/'scripts/model_learning.py','learning_test')
        rows,_=m.collect_local(ROOT,json.loads((ROOT/'learning/config.json').read_text())['project'])
        for row in rows:
            self.assertEqual(set(row),{'event_id','start','frozen_at','model','probabilities','labels','source','cutoff','key','schema_version','expected_scores'})
            self.assertLess(m.timestamp(row['frozen_at']),m.timestamp(row['start']))

class GradingRecoveryTests(unittest.TestCase):
    def test_old_slates_and_addenda_are_not_forgotten(self):
        from unittest.mock import patch
        from scanner import config,grade
        with tempfile.TemporaryDirectory() as d:
            for name in ('2026-01-01.v2.json','2026-10-29.v2.add1.json','2026-01-01.json','readme.json'):(pathlib.Path(d)/name).write_text('{}')
            with patch.object(config,'FORECAST_DIR',pathlib.Path(d)):
                self.assertEqual(grade.frozen_dates(),['2026-01-01','2026-10-29'])
