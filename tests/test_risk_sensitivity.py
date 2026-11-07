import copy, math
from scanner import edge, sim_r

def test_kc_handicap_edge_is_not_robust_to_existing_rating_uncertainty():
    card={'bo':5,'model':{'p_map_a_elo_only':.5590701879215795},'sim':sim_r.simulate(.5590701879215795,5,sims=100000,seed=20261028)}
    old=copy.deepcopy(card);f=edge.outcome_fn('map_handicap',False,1.5,None,5)
    base=edge.prob_from_exact(card['sim']['exact'],f)
    scenarios=edge.rating_stress(card);low=min(edge.prob_from_exact(s['exact'],f) for s in scenarios)
    assert base>.5 and base>.3761
    assert low<.5
    assert not edge.qualifies({'skip_reason':None,'our_prob':base,'cost':.3761,'ev_per_$1':base/.3761-1,'sensitivity_lower':low})
    assert old==card

def test_invalid_quotes_and_half_payout_are_not_recommendations():
    for bid in [None,0,float('nan'),.6,.2]:
        assert edge.sanity_skip({'ask':.5,'bid':bid,'volume':100,'cashes_on_lock':True})
    assert edge.sanity_skip({'ask':.5,'bid':.49,'volume':100,'cashes_on_lock':True}) is None
    assert edge.sanity_skip({'ask':.5,'bid':.49,'volume':100,'conditional_game':True})
