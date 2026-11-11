from scanner.sports_policy import excluded
from scanner.schedule import series_rows

def test_womens_competitions_and_cached_cards_excluded():
    for row in [{'league':{'name':"Women's League"}},{'tournament':'Ignis Cup 2026'},{'metadata':{'title':'LoL: Equal Esports Cup'}},{'gender':'female'}]:assert excluded(row)
    assert not excluded({'league':{'name':'Worlds'}})
    assert series_rows([{'league':{'name':"Women's League"},'match':{}}])==[]
