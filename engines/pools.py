"""
EY Contact Navigator — Opportunity Pool Engine
Computes hard ceilings for each benefit lever from actual client data.

Pool Types:
  1. Deflection Pool — max contacts that could be self-served / automated
  2. AHT Pool — max seconds saveable from search+wrap reduction
  3. Transfer Pool — max preventable transfers that can be avoided
  4. Escalation Pool — max preventable escalations
  5. Location Pool — max FTE migratable to lower-cost locations
  6. Shrinkage Pool — max shrinkage reduction from WFM improvements

Each pool is computed from the enriched intent data (intent_profile.py output)
and provides a ceiling that the waterfall netting engine cannot exceed.
"""


def compute_pools(enriched_queues, roles, params):
    """
    Compute all opportunity pools from enriched queue data.
    
    Args:
        enriched_queues: output of intent_profile.enrich_intents()
        roles: list of role dicts with headcount, costPerFTE
        params: system params (shrinkage, productive hours, etc.)
    
    Returns:
        dict with pool name → {ceiling, unit, breakdown, fte_equivalent}
    """
    total_volume_raw = sum(q['volume'] for q in enriched_queues)
    total_fte = sum(r['headcount'] for r in roles)
    
    # ── Net productive hours per FTE per year ──
    # Standard: 2080 gross hours - shrinkage
    shrinkage = params.get('shrinkage', 0.30)
    gross_hours_year = params.get('grossHoursPerYear', 2080)
    net_prod_hours = gross_hours_year * (1 - shrinkage)
    
    # ── Volume annualization (CR-021 v5 fix) ──
    # NEVER auto-guess annualization from capacity — this caused 239x inflation in v4.
    # The consultant MUST set volumeAnnualizationFactor explicitly in parameters.xlsx:
    #   - If raw volumes are MONTHLY → set factor = 12
    #   - If raw volumes are ANNUAL  → set factor = 1 (or omit)
    #   - If raw volumes are DAILY   → set factor = 250 (working days)
    #   - If raw volumes are from a SAMPLE (e.g. 1 month of CCaaS) → set factor = 12
    # Default = 12 because most CCaaS exports are monthly extracts.
    annualization = params.get('volumeAnnualizationFactor', 12)
    
    total_volume = total_volume_raw * annualization
    
    # Apply annualization to queue volumes for pool calculations
    ann_queues = []
    for q in enriched_queues:
        aq = dict(q)
        aq['annual_volume'] = q['volume'] * annualization
        ann_queues.append(aq)
    
    # Weighted avg cost per FTE
    if total_fte > 0:
        weighted_cost = sum(r['headcount'] * r['costPerFTE'] for r in roles) / total_fte
    else:
        weighted_cost = 55000
    
    pools = {}
    
    # ════════════════════════════════════════════
    # 1. DEFLECTION POOL
    # ════════════════════════════════════════════
    # Ceiling = Σ(Volume_i × DeflectionEligible_i × ContainmentFeasibility_i) across all intents
    # This represents the *achievable* deflection ceiling, not just eligible contacts.
    # Unit: contacts/year
    deflectable_contacts = 0
    deflection_breakdown = []
    for q in ann_queues:
        eligible_pct = q.get('deflection_eligible_pct', 0)
        containment = q.get('containment_feasibility', 0)
        # Achievable = eligible × how many can actually be contained
        achievable = q['annual_volume'] * eligible_pct * containment
        if achievable > 0:
            deflection_breakdown.append({
                'intent': q.get('intent', ''),
                'channel': q.get('channel', ''),
                'volume': q['annual_volume'],
                'eligible_pct': eligible_pct,
                'containment': containment,
                'achievable_contacts': round(achievable),
            })
        deflectable_contacts += achievable
    
    # Convert to FTE: deflected contacts × avg_handle_time → hours saved → FTE
    avg_aht_sec = sum(q['aht'] * 60 * q['annual_volume'] for q in ann_queues) / max(total_volume, 1)
    deflection_hours = (deflectable_contacts * avg_aht_sec) / 3600
    deflection_fte = deflection_hours / max(net_prod_hours, 1)
    
    pools['deflection'] = {
        'ceiling_contacts': round(deflectable_contacts),
        'ceiling_pct': round(deflectable_contacts / max(total_volume, 1), 4),
        'ceiling_fte': round(deflection_fte, 1),
        'ceiling_saving': round(deflection_fte * weighted_cost),
        'unit': 'contacts',
        'remaining_contacts': round(deflectable_contacts),  # consumed by waterfall
        'remaining_fte': round(deflection_fte, 1),
        'breakdown': sorted(deflection_breakdown, key=lambda x: x['achievable_contacts'], reverse=True)[:20],
    }
    
    # ════════════════════════════════════════════
    # 2. AHT REDUCTION POOL
    # ════════════════════════════════════════════
    # Only Search + Wrap seconds are reducible (talk time is value-add, hold is separate)
    # Ceiling = Σ(Volume_i × Reducible_seconds_i)
    total_reducible_seconds = 0
    aht_breakdown = []
    for q in ann_queues:
        decomp = q.get('aht_decomp', {})
        reducible = decomp.get('reducible_sec', 0)
        vol_reducible = q['annual_volume'] * reducible
        if vol_reducible > 0:
            aht_breakdown.append({
                'intent': q.get('intent', ''),
                'channel': q.get('channel', ''),
                'volume': q['annual_volume'],
                'search_sec': decomp.get('search_sec', 0),
                'wrap_sec': decomp.get('wrap_sec', 0),
                'reducible_sec': reducible,
                'total_reducible_hours': round(vol_reducible / 3600, 1),
            })
        total_reducible_seconds += vol_reducible
    
    aht_hours = total_reducible_seconds / 3600
    aht_fte = aht_hours / max(net_prod_hours, 1)
    
    pools['aht_reduction'] = {
        'ceiling_seconds': round(total_reducible_seconds),
        'ceiling_hours': round(aht_hours, 1),
        'ceiling_fte': round(aht_fte, 1),
        'ceiling_saving': round(aht_fte * weighted_cost),
        'unit': 'seconds',
        'remaining_seconds': round(total_reducible_seconds),
        'remaining_fte': round(aht_fte, 1),
        'breakdown': sorted(aht_breakdown, key=lambda x: x['total_reducible_hours'], reverse=True)[:20],
    }
    
    # ════════════════════════════════════════════
    # 3. TRANSFER / ESCALATION POOL
    # ════════════════════════════════════════════
    # Only preventable transfers are in the pool
    # Each preventable transfer costs ~2x AHT (original + receiving agent)
    total_preventable_transfers = 0
    transfer_breakdown = []
    total_preventable_escalations = 0
    escalation_breakdown = []
    
    for q in ann_queues:
        tc = q.get('transfer_class', {})
        prev_transfers = q['annual_volume'] * tc.get('preventable_rate', 0)
        if prev_transfers > 0:
            transfer_breakdown.append({
                'intent': q.get('intent', ''),
                'channel': q.get('channel', ''),
                'volume': q['annual_volume'],
                'preventable_rate': tc.get('preventable_rate', 0),
                'preventable_count': round(prev_transfers),
            })
        total_preventable_transfers += prev_transfers
        
        # Escalations (separate from transfers)
        esc_rate = q.get('escalation', 0)
        complexity = q.get('complexity', 0.5)
        # Preventable escalation share: inverse of complexity
        prev_esc_share = max(0.10, 0.60 - complexity * 0.50)
        prev_esc = q['annual_volume'] * esc_rate * prev_esc_share
        if prev_esc > 0:
            escalation_breakdown.append({
                'intent': q.get('intent', ''),
                'channel': q.get('channel', ''),
                'volume': q['annual_volume'],
                'escalation_rate': esc_rate,
                'preventable_count': round(prev_esc),
            })
        total_preventable_escalations += prev_esc
    
    # Transfer cost: each preventable transfer adds ~3 min extra handle time
    transfer_extra_sec = 180  # 3 min average per unnecessary transfer
    transfer_hours = (total_preventable_transfers * transfer_extra_sec) / 3600
    transfer_fte = transfer_hours / max(net_prod_hours, 1)
    
    pools['transfer_reduction'] = {
        'ceiling_contacts': round(total_preventable_transfers),
        'ceiling_fte': round(transfer_fte, 1),
        'ceiling_saving': round(transfer_fte * weighted_cost),
        'unit': 'transfers',
        'remaining_contacts': round(total_preventable_transfers),
        'remaining_fte': round(transfer_fte, 1),
        'breakdown': sorted(transfer_breakdown, key=lambda x: x['preventable_count'], reverse=True)[:20],
    }
    
    escalation_extra_sec = 300  # 5 min per unnecessary escalation
    esc_hours = (total_preventable_escalations * escalation_extra_sec) / 3600
    esc_fte = esc_hours / max(net_prod_hours, 1)
    
    pools['escalation_reduction'] = {
        'ceiling_contacts': round(total_preventable_escalations),
        'ceiling_fte': round(esc_fte, 1),
        'ceiling_saving': round(esc_fte * weighted_cost),
        'unit': 'escalations',
        'remaining_contacts': round(total_preventable_escalations),
        'remaining_fte': round(esc_fte, 1),
        'breakdown': sorted(escalation_breakdown, key=lambda x: x['preventable_count'], reverse=True)[:20],
    }
    
    # ════════════════════════════════════════════
    # 4. REPEAT / FCR POOL
    # ════════════════════════════════════════════
    # Repeat contacts that could be eliminated with better FCR
    # V6 fix: If raw repeat rates are implausibly low (short CCaaS sample), 
    # derive from FCR gap — same logic as gross.py
    total_repeat_contacts = 0
    repeat_breakdown = []
    
    weighted_repeat = sum(q.get('repeat', 0) * q['annual_volume'] for q in ann_queues) / max(total_volume, 1)
    REPEAT_FLOOR = 0.02
    use_repeat_fallback = weighted_repeat < REPEAT_FLOOR
    if use_repeat_fallback:
        weighted_fcr = sum(q.get('fcr', 0.75) * q['annual_volume'] for q in ann_queues) / max(total_volume, 1)
        fallback_repeat = max(0.05, (1 - weighted_fcr) * 0.60)
    
    for q in ann_queues:
        repeat_rate = fallback_repeat if use_repeat_fallback else q.get('repeat', 0)
        fcr = q.get('fcr', 0.70)
        # Reducible repeat = repeat_rate × (1 - FCR floor)
        # FCR floor ~ 0.85 for simple, 0.70 for complex
        fcr_target = 0.90 - q.get('complexity', 0.4) * 0.15
        fcr_gap = max(0, fcr_target - fcr)
        reducible_repeat = q['annual_volume'] * repeat_rate * min(1.0, fcr_gap / max(repeat_rate, 0.01))
        reducible_repeat = min(reducible_repeat, q['annual_volume'] * repeat_rate * 0.70)  # Can't fix more than 70% of repeats
        
        if reducible_repeat > 0:
            repeat_breakdown.append({
                'intent': q.get('intent', ''),
                'channel': q.get('channel', ''),
                'volume': q['annual_volume'],
                'repeat_rate': repeat_rate,
                'reducible_contacts': round(reducible_repeat),
            })
        total_repeat_contacts += reducible_repeat
    
    repeat_hours = (total_repeat_contacts * avg_aht_sec) / 3600
    repeat_fte = repeat_hours / max(net_prod_hours, 1)
    
    pools['repeat_reduction'] = {
        'ceiling_contacts': round(total_repeat_contacts),
        'ceiling_fte': round(repeat_fte, 1),
        'ceiling_saving': round(repeat_fte * weighted_cost),
        'unit': 'contacts',
        'remaining_contacts': round(total_repeat_contacts),
        'remaining_fte': round(repeat_fte, 1),
        'breakdown': sorted(repeat_breakdown, key=lambda x: x['reducible_contacts'], reverse=True)[:20],
    }
    
    # ════════════════════════════════════════════
    # 5. LOCATION POOL
    # ════════════════════════════════════════════
    # FTE that could be migrated to lower-cost locations (no workload reduction)
    # Based on migration readiness of the volume they handle
    migratable_volume = sum(q['annual_volume'] * q.get('migration_readiness', 0) for q in ann_queues)
    migratable_share = migratable_volume / max(total_volume, 1)
    
    # Only certain roles can be migrated
    migratable_roles = ['Agent L1', 'Agent L2 / Specialist', 'Back-Office / Processing']
    migratable_fte = sum(r['headcount'] for r in roles if r['role'] in migratable_roles)
    migratable_fte_adjusted = migratable_fte * migratable_share
    
    # Cost arbitrage: typically 30-50% cost saving per migrated FTE
    cost_arbitrage = params.get('locationArbitrage', 0.35)
    location_saving = migratable_fte_adjusted * weighted_cost * cost_arbitrage
    
    pools['location'] = {
        'ceiling_fte': round(migratable_fte_adjusted, 1),
        'ceiling_saving': round(location_saving),
        'migratable_share': round(migratable_share, 3),
        'cost_arbitrage': cost_arbitrage,
        'unit': 'fte',
        'remaining_fte': round(migratable_fte_adjusted, 1),
        'remaining_saving': round(location_saving),
    }
    
    # ════════════════════════════════════════════
    # 6. SHRINKAGE POOL
    # ════════════════════════════════════════════
    # Shrinkage improvement potential
    current_shrinkage = shrinkage
    best_practice_shrinkage = params.get('targetShrinkage', 0.22)
    shrinkage_gap = max(0, current_shrinkage - best_practice_shrinkage)
    
    # FTE equivalent of shrinkage reduction
    shrinkage_fte = total_fte * shrinkage_gap
    shrinkage_saving = shrinkage_fte * weighted_cost
    
    pools['shrinkage_reduction'] = {
        'current_shrinkage': round(current_shrinkage, 3),
        'target_shrinkage': round(best_practice_shrinkage, 3),
        'gap': round(shrinkage_gap, 3),
        'ceiling_fte': round(shrinkage_fte, 1),
        'ceiling_saving': round(shrinkage_saving),
        'unit': 'fte',
        'remaining_fte': round(shrinkage_fte, 1),
        'remaining_saving': round(shrinkage_saving),
    }
    
    # ════════════════════════════════════════════
    # SUMMARY
    # ════════════════════════════════════════════
    total_pool_fte = sum(p.get('ceiling_fte', 0) for p in pools.values())
    total_pool_saving = sum(p.get('ceiling_saving', 0) for p in pools.values())
    
    return {
        'pools': pools,
        'summary': {
            'total_pool_fte': round(total_pool_fte, 1),
            'total_pool_saving': round(total_pool_saving),
            'total_fte': total_fte,
            'total_volume': total_volume,
            'net_prod_hours': round(net_prod_hours, 1),
            'weighted_cost_per_fte': round(weighted_cost),
            'shrinkage': round(shrinkage, 3),
        },
        'annualization_factor': annualization,
    }


def consume_pool(pools, lever, amount_fte, amount_contacts=0, amount_seconds=0):
    """
    Consume from a pool during waterfall netting.
    Returns the actual consumed amount (capped by remaining pool).
    
    Args:
        pools: the pools dict (modified in place)
        lever: which pool to consume from
        amount_fte: FTE requested
        amount_contacts: contacts requested (for deflection/repeat/transfer)
        amount_seconds: seconds requested (for AHT)
    
    Returns:
        dict with actual consumed amounts
    """
    # Map lever names to pool keys
    lever_pool_map = {
        'deflection': 'deflection',
        'aht_reduction': 'aht_reduction',
        'escalation_reduction': 'escalation_reduction',
        'transfer_reduction': 'transfer_reduction',
        'repeat_reduction': 'repeat_reduction',
        'cost_reduction': 'location',
        'shrinkage_reduction': 'shrinkage_reduction',
    }
    
    pool_key = lever_pool_map.get(lever, lever)
    pool = pools.get(pool_key)
    
    if pool is None:
        # Unknown lever — fail closed: no savings for unmodeled levers (v5 fix)
        import logging
        logging.warning(f"consume_pool: unknown lever '{lever}' — returning 0 (fail closed)")
        return {'consumed_fte': 0, 'capped': True, 'pool_exhausted': False, 'unknown_lever': True}
    
    remaining_fte = pool.get('remaining_fte', 0)
    
    if remaining_fte <= 0:
        return {'consumed_fte': 0, 'capped': True, 'pool_exhausted': True}
    
    # Cap by remaining pool
    actual_fte = min(amount_fte, remaining_fte)
    cap_ratio = actual_fte / max(amount_fte, 0.001)
    
    # Deduct from pool
    pool['remaining_fte'] = round(remaining_fte - actual_fte, 1)
    
    # Also deduct contacts/seconds proportionally
    if 'remaining_contacts' in pool and amount_contacts > 0:
        pool['remaining_contacts'] = max(0, round(pool['remaining_contacts'] - amount_contacts * cap_ratio))
    if 'remaining_seconds' in pool and amount_seconds > 0:
        pool['remaining_seconds'] = max(0, round(pool['remaining_seconds'] - amount_seconds * cap_ratio))
    if 'remaining_saving' in pool:
        pool['remaining_saving'] = max(0, round(pool['remaining_saving'] - actual_fte * pool.get('ceiling_saving', 0) / max(pool.get('ceiling_fte', 1), 0.1)))
    
    return {
        'consumed_fte': round(actual_fte, 1),
        'consumed_contacts': round(amount_contacts * cap_ratio) if amount_contacts else 0,
        'consumed_seconds': round(amount_seconds * cap_ratio) if amount_seconds else 0,
        'capped': cap_ratio < 0.95,
        'pool_exhausted': pool.get('remaining_fte', 0) <= 0.5,
        'pool_remaining_fte': pool.get('remaining_fte', 0),
    }
