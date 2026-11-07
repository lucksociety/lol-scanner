import pytest
from scanner import forecast_v2


class _E:
    games = []
    def last_lineup(self, g, d):
        return None, None


def test_team_without_history_raises_nohistory():
    with pytest.raises(forecast_v2.NoHistory):
        forecast_v2.lineup_status(_E(), None, [], ["Worlds 2026"], "2026-10-30")


def test_live_roster_replacement_is_detected_and_never_claimed_confirmed():
    from scanner import draftmodel as dm
    from types import SimpleNamespace
    old=[{'pid':f'old-{i}','player':f'Player{i}','role':role,'side':'blue'} for i,role in enumerate(dm.ROLES)]
    backup={'pid':'replacement','player':'NewTop','role':dm.ROLES[0],'side':'blue'}
    games=[{'tournament':'Other','date':'2026-10-20','blue':'Other Team','red':'X','players':[backup]},
           {'tournament':'Worlds 2026','date':'2026-11-05','blue':'Team','red':'X','players':old}]
    roster=[{'players':[{'summonerName':'NewTop','role':'top'}]+[{'summonerName':f'Player{i}','role':role.lower()} for i,role in enumerate(dm.ROLES) if i]}]
    out=forecast_v2.lineup_status(SimpleNamespace(games=games),'Team',roster,['Worlds 2026'],'2026-11-07')
    assert out[0]['pid']=='replacement' and out[0]['roster_changed']
    assert all(x['status']!='CONFIRMED' for x in out)
