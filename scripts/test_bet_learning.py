import copy, tempfile, unittest
from pathlib import Path
from datetime import datetime,timezone
import bet_learning as b

class BetLearningTests(unittest.TestCase):
    def row(self,event='G',at='2026-11-06T20:00:00Z',bet=True):
        x={'type':'spreads','index':1,'side':'away','line':2.5,'label':'POR +2.5','probability':.6,'cost':.5} if bet else None
        return b.make_record(event,'2026-11-07T03:00:00Z','model',x,'verified-run',at)
    def test_actual_handicap_and_total_results_not_winner_accuracy(self):
        self.assertFalse(b.settle(self.row()['bet'],[124,118]))
        self.assertTrue(b.settle({'type':'spreads','index':1,'line':1.5},[3,5]))
        self.assertFalse(b.settle({'type':'spreads','index':1,'line':1.5},[3,1])) # KC +1.5 vs T1 3-1
        self.assertFalse(b.settle({'type':'totals','index':0,'side':'under','line':226.5},[139,132]))
    def test_postgame_records_are_excluded(self):
        self.assertIsNone(self.row(at='2026-11-07T03:00:00Z'))
        self.assertIsNone(self.row(at='2026-11-07T04:00:00Z'))
    def test_latest_no_bet_supersedes_prior_pick_and_duplicates_do_not_count(self):
        first=self.row();last=self.row(at='2026-11-06T22:00:00Z',bet=False)
        r=b.evaluate(b.merge([first,first,last]),{'G':{'scores':[124,118]}})
        self.assertEqual(r['settled_by_model_and_market'],{})
    def test_modified_archive_rejected(self):
        r=self.row();r['bet']['probability']=.9
        with self.assertRaises(AssertionError):b.merge([r])
    def test_conditional_games_and_pushes_not_false_losses(self):
        self.assertIsNone(b.settle({'type':'game','index':1},[3,0]))
        self.assertIsNone(b.settle({'type':'totals','index':0,'side':'under','line':6},[4,2]))
    def test_reliability_and_profit_are_separate(self):
        r=b.evaluate([self.row()],{'G':{'scores':[124,118]}})
        group=next(iter(r['settled_by_model_and_market'].values()))
        self.assertEqual(group['wins'],0);self.assertEqual(group['flat_cost_roi'],-1)
        self.assertAlmostEqual(group['brier'],.36)
if __name__=='__main__':unittest.main()
