"""
EY ServiceEdge — Workforce Transition Engine
Reskilling matrix, redeployment planning, and transition timeline.
"""

RESKILL_PATHS = {
    'Agent L1': [
        {'target':'Chat/Digital Agent','effort':'low','duration':4,'skills':['Digital literacy','Chat etiquette','Multi-tasking']},
        {'target':'AI Bot Trainer','effort':'medium','duration':8,'skills':['NLP basics','Intent design','Testing methodology']},
        {'target':'QA Analyst','effort':'medium','duration':12,'skills':['Quality frameworks','Calibration','Coaching']},
    ],
    'Agent L2 / Specialist': [
        {'target':'AI Operations Specialist','effort':'medium','duration':8,'skills':['AI monitoring','Exception handling','Escalation design']},
        {'target':'Knowledge Manager','effort':'medium','duration':6,'skills':['Content curation','Taxonomy design','Analytics']},
        {'target':'Process Analyst','effort':'high','duration':12,'skills':['Process mapping','Lean Six Sigma','Data analysis']},
    ],
    'Agent L3 / Expert': [
        {'target':'Solution Architect','effort':'high','duration':12,'skills':['System design','Integration patterns','Vendor management']},
        {'target':'CX Strategy Lead','effort':'high','duration':12,'skills':['Journey mapping','VoC analytics','Design thinking']},
    ],
    'Supervisor / Team Lead': [
        {'target':'Digital Operations Manager','effort':'medium','duration':8,'skills':['Digital KPIs','AI oversight','Change management']},
        {'target':'WFM Manager','effort':'medium','duration':6,'skills':['Forecasting','Scheduling','Analytics']},
    ],
    'Back-Office / Processing': [
        {'target':'RPA Developer','effort':'high','duration':12,'skills':['RPA tools','Process analysis','Testing']},
        {'target':'Data Entry Automation Analyst','effort':'medium','duration':6,'skills':['Automation tools','Quality checking','Reporting']},
    ],
}


def run_workforce(data, waterfall, initiatives):
    roles = data['roles']; params = data['params']
    horizon = params['horizon']
    role_impact = waterfall.get('roleImpact', {})
    redeployment_pct = params.get('redeploymentPct', 0.30)
    attrition_monthly = params.get('attritionMonthly', 0.015)
    annual_attrition = 1 - (1 - attrition_monthly) ** 12

    transitions = []
    for r in roles:
        rn = r['role']; hc = r['headcount']; cost = r['costPerFTE']
        ri = role_impact.get(rn, {'baseline':hc,'yearly':[0]*horizon})
        yearly_red = ri.get('yearly', [0]*horizon)

        for yr in range(horizon):
            red = yearly_red[yr] if yr < len(yearly_red) else 0
            if red <= 0: continue
            attrited = min(red, round(hc * annual_attrition))
            remaining = max(0, red - attrited)
            redeployed = round(remaining * redeployment_pct)
            separated = max(0, round(remaining - redeployed))

            sep_cost = separated * cost * params.get('severancePct', 0.25)
            reskill_cost = redeployed * params.get('reskillCostPerFTE', 5000)

            paths = RESKILL_PATHS.get(rn, [{'target':'General Reskill','effort':'medium','duration':6,'skills':['Transferable skills']}])

            transitions.append({
                'role': rn, 'year': yr+1, 'baseline': hc, 'reduction': round(red),
                'attrited': attrited, 'redeployed': redeployed, 'separated': separated,
                'separationCost': round(sep_cost), 'reskillCost': round(reskill_cost),
                'totalTransitionCost': round(sep_cost + reskill_cost),
                'reskillPaths': paths,
            })

    # Summary
    total_red = sum(t['reduction'] for t in transitions)
    total_attr = sum(t['attrited'] for t in transitions)
    total_redep = sum(t['redeployed'] for t in transitions)
    total_sep = sum(t['separated'] for t in transitions)
    total_sep_cost = sum(t['separationCost'] for t in transitions)
    total_reskill_cost = sum(t['reskillCost'] for t in transitions)

    # Reskill matrix
    reskill_matrix = {}
    for rn, paths in RESKILL_PATHS.items():
        reskill_matrix[rn] = paths

    return {
        'transitions': transitions,
        'summary': {
            'totalReduction': total_red, 'totalAttrited': total_attr,
            'totalRedeployed': total_redep, 'totalSeparated': total_sep,
            'totalSeparationCost': round(total_sep_cost),
            'totalReskillCost': round(total_reskill_cost),
            'totalTransitionCost': round(total_sep_cost + total_reskill_cost),
            'attritionRate': round(annual_attrition*100,1),
            'redeploymentRate': round(redeployment_pct*100,1),
        },
        'reskillMatrix': reskill_matrix,
    }
