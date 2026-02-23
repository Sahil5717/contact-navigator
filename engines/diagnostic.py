"""
EY ServiceEdge — Diagnostic Engine
CR-009: Root cause analysis with fallback for no-red queues
CR-011: Cost analysis with missing field handling
CR-016: Mismatch summary with unique channel rationales
"""
import math

HEALTH_WEIGHTS = {'aht':0.25,'fcr':0.20,'csat':0.25,'escalation':0.15,'cpc':0.15}
METRIC_DIRECTIONS = {'aht':'lower','fcr':'higher','csat':'higher','escalation':'lower','cpc':'lower'}

def run_diagnostic(data):
    queues = data['queues']; roles = data['roles']; params = data['params']
    benchmarks = data.get('benchmarks',{})
    total_vol = data['totalVolume']; total_fte = data['totalFTE']
    avg_csat = data['avgCSAT']

    # ── Per-queue scoring ──
    queue_scores = []
    for q in queues:
        scores = {}; details = {}
        for m in HEALTH_WEIGHTS:
            val = q.get(m); bench = _get_bench(benchmarks, q['channel'], m, params)
            if val is None or bench is None:
                scores[m] = 50; details[m] = {'value':0,'benchmark':0,'gap':0,'score':50,'rating':'grey'}; continue
            gap = _calc_gap(val, bench, m)
            sc = max(0, min(100, 50 + gap*50))
            scores[m] = sc
            details[m] = {'value':round(val,2),'benchmark':round(bench,2),'gap':round(gap,3),
                          'score':round(sc,1),'rating':'green' if sc>=70 else 'amber' if sc>=40 else 'red'}
        overall = sum(scores[m]*HEALTH_WEIGHTS[m] for m in HEALTH_WEIGHTS)
        queue_scores.append({
            'queue':q['queue'],'channel':q['channel'],'volume':q['volume'],
            'overallScore':round(overall,1),'rating':'green' if overall>=70 else 'amber' if overall>=40 else 'red',
            'metrics':details,'complexity':q.get('complexity',0.5)
        })

    # ── Summary metrics ──
    green = sum(1 for qs in queue_scores if qs['rating']=='green')
    amber = sum(1 for qs in queue_scores if qs['rating']=='amber')
    red = sum(1 for qs in queue_scores if qs['rating']=='red')

    # ── Problem areas ──
    problem_areas = []
    for qs in queue_scores:
        for m, d in qs['metrics'].items():
            if d['rating'] == 'red':
                problem_areas.append({'queue':qs['queue'],'channel':qs['channel'],'metric':m,
                                      'value':d['value'],'benchmark':d['benchmark'],'gap':d['gap'],'score':d['score']})
    problem_areas.sort(key=lambda x: x['score'])

    # ── CR-009: Root cause analysis (with fallback) ──
    root_causes = _build_root_causes(queue_scores, problem_areas, queues, params)

    # ── CR-011: Cost analysis ──
    cost_analysis = _build_cost_analysis(queues, roles, params, benchmarks, queue_scores)

    # ── Channel summary ──
    channel_summary = _build_channel_summary(queues, queue_scores, benchmarks, params)

    # ── CR-016: Mismatch summary ──
    mismatch = _build_mismatch_summary(queues, queue_scores, params)

    return {
        'queueScores': queue_scores,
        'summary': {'green':green,'amber':amber,'red':red,'total':len(queue_scores),
                    'avgScore':round(sum(qs['overallScore'] for qs in queue_scores)/max(len(queue_scores),1),1)},
        'problemAreas': problem_areas,
        'rootCauses': root_causes,
        'costAnalysis': cost_analysis,
        'channelSummary': channel_summary,
        'mismatch': mismatch,
    }


def _get_bench(benchmarks, channel, metric, params):
    ch_bench = benchmarks.get(channel, {})
    if metric in ch_bench: return ch_bench[metric]
    defaults = {'aht':{'Voice':360,'Chat':420,'Email':600,'IVR':120,'App/Self-Service':180,'SMS/WhatsApp':300,'Social Media':300,'Retail/Walk-in':600},
                'fcr':{'Voice':0.75,'Chat':0.72,'Email':0.65,'IVR':0.80,'App/Self-Service':0.85,'SMS/WhatsApp':0.70,'Social Media':0.65,'Retail/Walk-in':0.80},
                'csat':{'Voice':4.0,'Chat':3.8,'Email':3.5,'IVR':3.5,'App/Self-Service':4.0,'SMS/WhatsApp':3.8,'Social Media':3.5,'Retail/Walk-in':4.2},
                'escalation':{'Voice':0.12,'Chat':0.10,'Email':0.08,'IVR':0.05,'App/Self-Service':0.03,'SMS/WhatsApp':0.08,'Social Media':0.10,'Retail/Walk-in':0.15},
                'cpc':{'Voice':8.50,'Chat':5.00,'Email':4.00,'IVR':1.50,'App/Self-Service':0.50,'SMS/WhatsApp':3.00,'Social Media':4.00,'Retail/Walk-in':15.00}}
    return defaults.get(metric,{}).get(channel)


def _calc_gap(val, bench, metric):
    if bench == 0: return 0
    if METRIC_DIRECTIONS[metric] == 'lower':
        return (bench - val) / bench
    else:
        return (val - bench) / bench


def _build_root_causes(queue_scores, problem_areas, queues, params):
    """CR-009: Root cause for each red/amber queue; fallback if none red."""
    root_causes = []
    targets = [qs for qs in queue_scores if qs['rating']=='red']
    if not targets:
        targets = sorted([qs for qs in queue_scores if qs['rating']=='amber'], key=lambda x: x['overallScore'])[:5]
    if not targets:
        targets = sorted(queue_scores, key=lambda x: x['overallScore'])[:3]

    for qs in targets:
        worst = min(qs['metrics'].items(), key=lambda x: x[1]['score'])
        m_name, m_data = worst
        cause_map = {
            'aht': f"High handle time ({m_data['value']:.0f}s vs {m_data['benchmark']:.0f}s benchmark) — likely driven by agent skill gaps, complex processes, or inadequate knowledge tools.",
            'fcr': f"Low first-contact resolution ({m_data['value']:.0%} vs {m_data['benchmark']:.0%}) — repeat contacts suggest unresolved issues, incomplete information, or process gaps.",
            'csat': f"Below-target satisfaction ({m_data['value']:.1f} vs {m_data['benchmark']:.1f}) — root causes may include long wait times, agent capability gaps, or channel friction.",
            'escalation': f"Elevated escalation rate ({m_data['value']:.1%} vs {m_data['benchmark']:.1%}) — indicates L1 empowerment gaps or complex issues not matched to skill level.",
            'cpc': f"High cost per contact (${m_data['value']:.2f} vs ${m_data['benchmark']:.2f}) — driven by channel cost structure, overstaffing, or inefficient routing.",
        }
        root_causes.append({
            'queue': qs['queue'], 'channel': qs['channel'], 'rating': qs['rating'],
            'worstMetric': m_name, 'score': m_data['score'],
            'rootCause': cause_map.get(m_name, f"Performance gap in {m_name}"),
            'recommendation': _root_cause_recommendation(m_name, qs['channel']),
        })
    return root_causes


def _root_cause_recommendation(metric, channel):
    recs = {
        'aht': {'Voice':'Deploy AI Agent Assist + knowledge base search to cut handle time.',
                'Chat':'Implement canned responses and smart routing to reduce AHT.',
                'Email':'Automate templated responses and deploy document AI.',
                '_default':'Implement process streamlining and agent assist tooling.'},
        'fcr': {'Voice':'Improve agent training and empower L1 with decision authority.',
                'Chat':'Deploy guided resolution flows and escalation path redesign.',
                '_default':'Implement root-cause fix program and knowledge management overhaul.'},
        'csat': {'Voice':'Reduce wait times, improve agent soft skills, enable callback.',
                 'Chat':'Improve bot handoff experience and response speed.',
                 '_default':'Address primary dissatisfiers through journey mapping.'},
        'escalation': {'_default':'Redesign escalation paths and empower frontline agents.'},
        'cpc': {'Voice':'Migrate simple queries to digital channels and automate where possible.',
                '_default':'Optimize channel mix and automate low-complexity interactions.'},
    }
    mr = recs.get(metric, {})
    return mr.get(channel, mr.get('_default', 'Review processes and implement targeted improvements.'))


def _build_cost_analysis(queues, roles, params, benchmarks, queue_scores):
    """CR-011: Robust cost analysis with missing field handling."""
    total_cost = sum(r['headcount'] * r['costPerFTE'] for r in roles)
    total_vol = sum(q['volume'] for q in queues) or 1
    blended_cpc = total_cost / (total_vol * 12) if total_vol > 0 else 0

    channel_costs = {}
    for q in queues:
        ch = q['channel']
        if ch not in channel_costs:
            channel_costs[ch] = {'volume':0,'cost':0,'benchmark_cpc':0}
        channel_costs[ch]['volume'] += q['volume']
        cpc = q.get('cpc') or q.get('costPerContact') or blended_cpc
        channel_costs[ch]['cost'] += q['volume'] * cpc * 12
        b = _get_bench(benchmarks, ch, 'cpc', params)
        channel_costs[ch]['benchmark_cpc'] = b if b else blended_cpc

    cost_by_channel = []
    wasted = 0
    for ch, cd in channel_costs.items():
        v = cd['volume']; c = cd['cost']; cpc = c/(v*12) if v>0 else 0
        bench = cd['benchmark_cpc'] or cpc
        excess = max(0, cpc - bench) * v * 12
        wasted += excess
        cost_by_channel.append({'channel':ch,'annualCost':round(c),'volume':v,'cpc':round(cpc,2),
                                'benchmarkCpc':round(bench,2),'excess':round(excess),'pctOfTotal':round(c/max(total_cost,1)*100,1)})

    cost_by_channel.sort(key=lambda x: x['annualCost'], reverse=True)

    # Complexity cost tiers
    complexity_tiers = {'simple':[],'moderate':[],'complex':[]}
    for q in queues:
        cx = q.get('complexity', 0.5)
        tier = 'simple' if cx < 0.35 else 'complex' if cx > 0.55 else 'moderate'
        cpc = q.get('cpc') or q.get('costPerContact') or blended_cpc
        complexity_tiers[tier].append({'queue':q['queue'],'volume':q['volume'],'cpc':cpc})

    tier_summary = {}
    for tier, items in complexity_tiers.items():
        tv = sum(i['volume'] for i in items)
        tc = sum(i['volume']*i['cpc']*12 for i in items)
        tier_summary[tier] = {'queues':len(items),'volume':tv,'annualCost':round(tc),'avgCpc':round(tc/(tv*12),2) if tv>0 else 0}

    return {
        'totalAnnualCost': round(total_cost),
        'blendedCpc': round(blended_cpc, 2),
        'byChannel': cost_by_channel,
        'wastedSpend': round(wasted),
        'wastedPct': round(wasted/max(total_cost,1)*100, 1),
        'byComplexity': tier_summary,
    }


def _build_channel_summary(queues, queue_scores, benchmarks, params):
    channels = {}
    for qs in queue_scores:
        ch = qs['channel']
        if ch not in channels:
            channels[ch] = {'queues':0,'volume':0,'scores':[],'ratings':{'green':0,'amber':0,'red':0}}
        channels[ch]['queues'] += 1
        channels[ch]['volume'] += qs['volume']
        channels[ch]['scores'].append(qs['overallScore'])
        channels[ch]['ratings'][qs['rating']] += 1

    result = []
    for ch, cd in channels.items():
        avg = sum(cd['scores'])/len(cd['scores']) if cd['scores'] else 0
        result.append({'channel':ch,'queueCount':cd['queues'],'totalVolume':cd['volume'],
                       'avgScore':round(avg,1),'rating':'green' if avg>=70 else 'amber' if avg>=40 else 'red',
                       'ratings':cd['ratings']})
    result.sort(key=lambda x: x['totalVolume'], reverse=True)
    return result


def _build_mismatch_summary(queues, queue_scores, params):
    """CR-016: Channel-specific mismatch rationales."""
    mismatches = []
    channel_rationale = {
        'Voice': {'high_simple':'High-volume simple queries on Voice should migrate to digital/self-service channels for cost efficiency.',
                  'low_complex':'Complex low-volume Voice queues may benefit from specialist routing or video support.',
                  'high_cost':'Voice channel cost exceeds benchmark — consider IVR containment and chatbot deflection.'},
        'Chat': {'high_simple':'Simple Chat queries are candidates for full bot automation.',
                 'low_complex':'Complex Chat interactions may need escalation to Voice/Video for better resolution.',
                 'high_cost':'Chat costs above benchmark suggest bot containment rate needs improvement.'},
        'Email': {'high_simple':'Simple Email queries should be auto-responded or deflected to self-service FAQ.',
                  'low_complex':'Complex Email threads indicate need for RPA or document AI processing.',
                  'high_cost':'Email processing costs indicate manual handling — automate with AI triage.'},
        'IVR': {'high_simple':'IVR handling simple queries effectively — ensure containment rate stays high.',
                'low_complex':'Complex queries reaching IVR should be fast-tracked to agents.',
                'high_cost':'IVR cost above benchmark suggests infrastructure modernisation needed.'},
        'App/Self-Service': {'high_simple':'Self-service adoption good for simple queries — expand coverage.',
                             'low_complex':'Complex queries on self-service need guided resolution flows.',
                             'high_cost':'Self-service cost should be lowest — review platform efficiency.'},
    }

    for qs in queue_scores:
        q = next((q for q in queues if q['queue']==qs['queue']), None)
        if not q: continue
        ch = qs['channel']; cx = q.get('complexity',0.5); vol = q['volume']
        is_simple = cx < 0.35; is_complex = cx > 0.55
        is_high_vol = vol > sum(q2['volume'] for q2 in queues)/max(len(queues),1)
        cpc_data = qs['metrics'].get('cpc',{})
        is_high_cost = cpc_data.get('rating') == 'red'

        reasons = []
        cr = channel_rationale.get(ch, {})
        if is_simple and is_high_vol and ch in ('Voice','Email'):
            reasons.append(cr.get('high_simple','Consider migrating simple high-volume queries to lower-cost channels.'))
        if is_complex and not is_high_vol:
            reasons.append(cr.get('low_complex','Low-volume complex queries may need specialist handling.'))
        if is_high_cost:
            reasons.append(cr.get('high_cost','Channel cost exceeds benchmark — review efficiency.'))

        if reasons:
            mismatches.append({'queue':qs['queue'],'channel':ch,'volume':vol,
                               'complexity':'simple' if is_simple else 'complex' if is_complex else 'moderate',
                               'score':qs['overallScore'],'reasons':reasons})

    mismatches.sort(key=lambda x: len(x['reasons']), reverse=True)
    return mismatches
