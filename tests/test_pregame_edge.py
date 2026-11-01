from datetime import datetime,timezone
from scanner.edge import pregame_reason
from scanner.edgeview import best_summary
from scanner.discord_report import _series_field

def test_clock_and_live_event_override_stale_schedule():
    now=datetime(2026,11,1,17,tzinfo=timezone.utc)
    c={'start_utc':'2026-11-01T18:00:00Z'}
    assert pregame_reason(c,now=now)is None
    assert pregame_reason(c,{'isLive':True},now=now)
    assert pregame_reason({'start_utc':'2026-11-01T17:00:00Z'},now=now)
    assert pregame_reason({},now=now)
    assert pregame_reason({'start_utc':'2026-11-01T18:00:00'},now=now)

def test_cached_report_never_recommends_started_series():
    c={'start_utc':'2000-01-01T00:00:00Z','team_a':{'code':'A'},'team_b':{'code':'B'},
       'sim':{'locked_score':'0-2','locked_score_prob':.3,'p_series_a':.4},'bo':3,'start_et':'Sat 7 PM ET'}
    row={'status':'ok','best_bet':{'label':'STALE_SELECTION'}}
    rendered=str(_series_field(c,row,True))
    assert 'STALE_SELECTION'not in rendered and 'NO BET'in rendered
    assert row['best_bet']['label']=='STALE_SELECTION'
    assert best_summary({'status':'not_prematch','reason':'started'})['verdict']=='NO BET'
