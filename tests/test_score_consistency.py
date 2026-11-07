from scanner.edge import score_supports

def test_score_and_unknown_order():
    c={'bo':5,'team_a':{'code':'T1','name':'T1'},'team_b':{'code':'KC','name':'Karmine Corp'},'sim':{'locked_score':'3-1'}}
    for leg,want in [({'type':'moneyline','label':'ML T1'},True),({'type':'moneyline','label':'ML KC'},False),({'type':'map_handicap','label':'KC +1.5 maps','line':1.5},False),({'type':'map_handicap','label':'KC +2.5 maps','line':2.5},True),({'type':'totals_over','label':'Over 4.5 maps','line':4.5},False),({'type':'totals_under','label':'Under 4.5 maps','line':4.5},True),({'type':'game','label':'Game 1 T1'},False),({'type':'game','label':'Game 4 T1'},True),({'type':'game','label':'NOT Game 5 T1'},False)]: assert score_supports(leg,c)==want
    assert not score_supports({'type':'moneyline','label':'ML T1'},{})
