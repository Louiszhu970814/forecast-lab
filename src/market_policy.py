"""Predeclared Market Pulse 26Q4 policy; never adjusted from open forecasts."""
VERSION = 'market_pulse_v1'
PROJECT_ID = 33131
SETTING = {'version':VERSION,'project_id':PROJECT_ID,'slug':'market-pulse-26q4',
           'first_forecast':'first_eligible_spare_capacity','updates':0,
           'comment':'private_per_child','priority':'after_main_and_minibench',
           'reserve_both_tiers_for_expected_arrivals':True}


def validate(policy):
    present='market_pulse' in policy.get('venues',[])
    if present and (policy.get('version')!='continuous_v11' or policy.get('market_pulse')!=SETTING):
        raise ValueError('exact predeclared Market Pulse policy required')
    if not present and 'market_pulse' in policy:
        raise ValueError('Market Pulse policy requires its venue')
