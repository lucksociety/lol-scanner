import csv
from scanner import grade,config

def setup_grade(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'CALIB_FILE',tmp_path/'grades.csv')
    path=tmp_path/'frozen.json';path.write_text('{}')
    monkeypatch.setattr(grade,'frozen_path',lambda *a:path)
    card={'match_id':'123','bo':3,'team_a':{'code':'CFO','name':'CTBC Flying Oyster'},'team_b':{'code':'KC','name':'Karmine Corp'},'sim':{'p_series_a':.2614,'locked_score':'0-2','exact':{'0-2':.4466,'2-0':.1181}}}
    monkeypatch.setattr(grade,'load_frozen',lambda *a:{'model_version':'v2','cards':[card]})
    row={'match_id':'123','state':'completed','a':'KC','a_name':'Karmine Corp','b':'CFO','b_name':'CTBC Flying Oyster','a_w':2,'b_w':0}
    monkeypatch.setattr(grade.schedule,'series_rows',lambda events:events)
    return row

def test_reverse_order_and_reconcile_existing_error(tmp_path,monkeypatch):
    row=setup_grade(tmp_path,monkeypatch)
    result=grade.grade('2026-10-29',[row],version='v2')
    assert result[0]['actual']=='0-2' and result[0]['a_won']==0 and result[0]['lock_hit']==1
    assert result[0]['brier']==round(.2614**2,4)
    assert grade.grade('2026-10-29',[row],version='v2')==[]
    path=config.CALIB_FILE
    with path.open() as f:rows=list(csv.DictReader(f))
    rows[0]['actual']='2-0';rows[0]['a_won']='1'
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)
    assert grade.grade('2026-10-29',[row],version='v2')[0]['actual']=='0-2'
    with path.open() as f:assert len(list(csv.DictReader(f)))==1

def test_same_order_invalid_identity_and_incomplete_score(tmp_path,monkeypatch):
    row=setup_grade(tmp_path,monkeypatch)
    assert grade.grade('2026-10-29',[{**row,'b':'OTHER','b_name':'Other'}],version='v2')==[]
    assert grade.grade('2026-10-29',[{**row,'a_w':1}],version='v2')==[]
    same={**row,'a':'CFO','a_name':'CTBC Flying Oyster','b':'KC','b_name':'Karmine Corp','a_w':0,'b_w':2}
    assert grade.grade('2026-10-29',[same],version='v2')[0]['actual']=='0-2'
