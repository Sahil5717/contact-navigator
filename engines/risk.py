"""
EY ServiceEdge — Risk Assessment Engine
Per-initiative risk scoring with mitigation recommendations.
"""

RISK_FACTORS = {
    'complexity': {'weight':0.25,'high':0.85,'medium':0.50,'low':0.20},
    'adoption':   {'weight':0.20,'threshold':0.70},
    'integration':{'weight':0.20,'multi_channel':0.70,'single':0.30},
    'timeline':   {'weight':0.15,'strategic':0.80,'medium_term':0.50,'quick_win':0.20},
    'change':     {'weight':0.20,'high':0.80,'medium':0.45,'low':0.15},
}

def run_risk(initiatives, data):
    results = []
    for init in initiatives:
        if not init.get('enabled'): continue
        scores = {}
        # Complexity risk
        eff = init.get('effort','medium').lower()
        scores['complexity'] = RISK_FACTORS['complexity'].get(eff, 0.50)
        # Adoption risk
        adopt = init.get('adoption', 0.80)
        scores['adoption'] = max(0, 1 - adopt) * 1.5
        # Integration risk
        nch = len(init.get('channels',[]))
        scores['integration'] = min(1.0, 0.30 + (nch-1)*0.15)
        # Timeline risk
        tl = init.get('timeline','medium_term')
        scores['timeline'] = RISK_FACTORS['timeline'].get(tl, 0.50)
        # Change management risk
        nr = len(init.get('roles',[]))
        scores['change'] = min(1.0, 0.20 + (nr-1)*0.15)

        overall = sum(scores[f] * RISK_FACTORS[f]['weight'] for f in scores)
        rating = 'high' if overall > 0.65 else 'medium' if overall > 0.35 else 'low'

        mitigations = []
        if scores['complexity'] > 0.6:
            mitigations.append('Phase implementation with pilot → scale approach')
        if scores['adoption'] > 0.4:
            mitigations.append('Invest in change management and user training')
        if scores['integration'] > 0.5:
            mitigations.append('Establish integration testing environment early')
        if scores['timeline'] > 0.6:
            mitigations.append('Build buffer into timeline and set interim milestones')
        if scores['change'] > 0.5:
            mitigations.append('Appoint change champions across affected teams')

        results.append({
            'id': init['id'], 'name': init['name'], 'layer': init['layer'],
            'overallRisk': round(overall, 2), 'rating': rating,
            'scores': {k:round(v,2) for k,v in scores.items()},
            'mitigations': mitigations,
            'fteImpact': init.get('_fteImpact',0),
            'annualSaving': init.get('_annualSaving',0),
        })

    results.sort(key=lambda x: x['overallRisk'], reverse=True)
    summary = {
        'high': sum(1 for r in results if r['rating']=='high'),
        'medium': sum(1 for r in results if r['rating']=='medium'),
        'low': sum(1 for r in results if r['rating']=='low'),
        'avgRisk': round(sum(r['overallRisk'] for r in results)/max(len(results),1),2),
    }
    return {'initiatives':results, 'summary':summary}
