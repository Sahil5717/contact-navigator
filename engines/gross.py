"""
EY Contact Navigator — Gross Impact Engine
Computes lever-specific gross FTE impact for each initiative.

Instead of a single generic formula (affected_FTE × impact% × adoption),
each lever type has its own physics:

  Deflection:    contacts_removed → hours_saved → FTE
  AHT Reduction: seconds_saved_per_contact × eligible_contacts → hours → FTE
  Transfer:      transfers_avoided × extra_time_per_transfer → hours → FTE
  Escalation:    escalations_avoided × extra_time_per_escalation → hours → FTE
  Repeat:        repeat_contacts_eliminated × AHT → hours → FTE
  Location:      FTE_migrated × cost_arbitrage (NO workload reduction)
  Shrinkage:     shrinkage_pct_reduced × total_FTE
"""
import math


def compute_gross_impact(initiative, enriched_queues, roles, pools_data, params):
    """
    Compute the gross FTE impact for a single initiative using lever-specific physics.
    
    Args:
        initiative: dict with id, lever, impact, adoption, channels, roles, etc.
        enriched_queues: output of intent_profile.enrich_intents()
        roles: list of role dicts
        pools_data: output of pools.compute_pools() — for reference ceilings
        params: system parameters
    
    Returns:
        dict with:
          gross_fte: raw FTE impact before netting
          gross_contacts: contacts affected (for deflection/repeat)
          gross_seconds: seconds saved (for AHT)
          gross_saving: annual saving at full ramp
          mechanism: explanation of how the number was derived
          eligible_volume: volume this initiative touches
    """
    lever = initiative.get('lever', 'aht_reduction')
    impact = initiative.get('impact', 0)
    adoption = initiative.get('adoption', 0.80)
    channels = set(initiative.get('channels', []))
    target_roles = set(initiative.get('roles', []))
    
    # Get queues matching this initiative's channels
    matching_queues = [q for q in enriched_queues if q.get('channel', '') in channels]
    if not matching_queues:
        matching_queues = enriched_queues  # fallback: all queues
    
    # Get roles matching this initiative
    affected_roles = [r for r in roles if r['role'] in target_roles]
    affected_fte = sum(r['headcount'] for r in affected_roles)
    if affected_fte > 0:
        weighted_cost = sum(r['headcount'] * r['costPerFTE'] for r in affected_roles) / affected_fte
    else:
        weighted_cost = 55000
    
    # Net productive hours
    shrinkage = params.get('shrinkage', 0.30)
    net_prod_hours = params.get('grossHoursPerYear', 2080) * (1 - shrinkage)
    
    total_matching_volume = sum(q['volume'] for q in matching_queues)
    
    # ── Dispatch to lever-specific formula ──
    if lever == 'deflection':
        return _gross_deflection(initiative, matching_queues, affected_fte, weighted_cost,
                                  net_prod_hours, impact, adoption, pools_data)
    
    elif lever == 'aht_reduction':
        return _gross_aht_reduction(initiative, matching_queues, affected_fte, weighted_cost,
                                     net_prod_hours, impact, adoption, total_matching_volume, pools_data)
    
    elif lever == 'escalation_reduction':
        return _gross_escalation(initiative, matching_queues, affected_fte, weighted_cost,
                                  net_prod_hours, impact, adoption, pools_data)
    
    elif lever == 'repeat_reduction':
        return _gross_repeat(initiative, matching_queues, affected_fte, weighted_cost,
                              net_prod_hours, impact, adoption, pools_data)
    
    elif lever == 'transfer_reduction':
        return _gross_transfer(initiative, matching_queues, affected_fte, weighted_cost,
                                net_prod_hours, impact, adoption, pools_data)
    
    elif lever == 'cost_reduction':
        return _gross_location(initiative, matching_queues, affected_fte, weighted_cost,
                                impact, adoption, pools_data, params)
    
    elif lever == 'shrinkage_reduction':
        return _gross_shrinkage(initiative, affected_fte, weighted_cost,
                                 impact, adoption, pools_data, params)
    
    else:
        # Unknown lever — use generic formula with conservative cap
        return _gross_generic(initiative, affected_fte, weighted_cost, impact, adoption)


def _gross_deflection(init, queues, affected_fte, cost, net_hours, impact, adoption, pools):
    """
    Deflection: contacts_removed × avg_handle_time → hours_saved → FTE
    
    Gross = Σ(Volume_i × Eligible_i × Impact × Adoption) across eligible intents
    
    NOTE (V6 fix): deflection_eligible_pct already incorporates containment_feasibility
    (eligible = repeatability × containment × auth_penalty). So we must NOT multiply
    by containment again here. The initiative's `impact` represents its own effectiveness
    rate, applied directly to the eligible pool.
    """
    total_deflectable = 0
    mechanism_parts = []
    
    for q in queues:
        eligible_pct = q.get('deflection_eligible_pct', 0)
        containment = q.get('containment_feasibility', 0)
        
        # V6: eligible_pct is now containment-free (repeatability × auth_penalty only).
        # Containment is applied here as a cap: the initiative can't contain more
        # than what's physically feasible for this intent.
        effective_rate = eligible_pct * min(impact, containment) * adoption
        
        contacts_deflected = q['volume'] * effective_rate
        total_deflectable += contacts_deflected
        
        if contacts_deflected > 10:
            mechanism_parts.append(f"{q.get('intent','?')}/{q.get('channel','?')}: "
                                   f"{q['volume']:,} × {effective_rate:.1%} = {contacts_deflected:,.0f}")
    
    # Convert contacts to hours to FTE
    avg_aht_sec = sum(q['aht'] * 60 * q['volume'] for q in queues) / max(sum(q['volume'] for q in queues), 1)
    hours_saved = (total_deflectable * avg_aht_sec) / 3600
    gross_fte = hours_saved / max(net_hours, 1)
    
    return {
        'gross_fte': round(gross_fte, 1),
        'gross_contacts': round(total_deflectable),
        'gross_seconds': 0,
        'gross_saving': round(gross_fte * cost),
        'mechanism': f"Deflection: {total_deflectable:,.0f} contacts × {avg_aht_sec:.0f}s AHT → "
                     f"{hours_saved:,.0f} hrs → {gross_fte:.1f} FTE",
        'mechanism_detail': mechanism_parts[:5],
        'eligible_volume': sum(q['volume'] for q in queues),
    }


def _gross_aht_reduction(init, queues, affected_fte, cost, net_hours, impact, adoption, total_vol, pools):
    """
    AHT Reduction: seconds_saved_per_contact × eligible_contacts → hours → FTE
    Only search + wrap time is reducible.
    """
    total_seconds_saved = 0
    mechanism_parts = []
    
    for q in queues:
        decomp = q.get('aht_decomp', {})
        reducible_sec = decomp.get('reducible_sec', q['aht'] * 60 * 0.35)  # fallback: 35% of AHT (search+wrap+talk efficiency)
        
        # Initiative's impact represents fraction of reducible time it can save
        seconds_per_contact = reducible_sec * impact * adoption
        total_saved = q['volume'] * seconds_per_contact
        total_seconds_saved += total_saved
        
        if total_saved > 100:
            mechanism_parts.append(f"{q.get('intent','?')}: {q['volume']:,} × {seconds_per_contact:.1f}s = "
                                   f"{total_saved/3600:.0f}hrs")
    
    hours_saved = total_seconds_saved / 3600
    gross_fte = hours_saved / max(net_hours, 1)
    
    return {
        'gross_fte': round(gross_fte, 1),
        'gross_contacts': 0,
        'gross_seconds': round(total_seconds_saved),
        'gross_saving': round(gross_fte * cost),
        'mechanism': f"AHT: {total_seconds_saved/3600:,.0f} hrs saved across {total_vol:,} contacts → {gross_fte:.1f} FTE",
        'mechanism_detail': mechanism_parts[:5],
        'eligible_volume': total_vol,
    }


def _gross_escalation(init, queues, affected_fte, cost, net_hours, impact, adoption, pools):
    """
    Escalation reduction: preventable escalations avoided × extra time per escalation → FTE
    """
    total_prevented = 0
    for q in queues:
        esc_rate = q.get('escalation', 0)
        complexity = q.get('complexity', 0.5)
        prev_share = max(0.10, 0.60 - complexity * 0.50)
        preventable = q['volume'] * esc_rate * prev_share * impact * adoption
        total_prevented += preventable
    
    extra_sec_per_esc = 900  # V6: 15 min — prevented escalation avoids full L2/L3 handle time
    hours_saved = (total_prevented * extra_sec_per_esc) / 3600
    gross_fte = hours_saved / max(net_hours, 1)
    
    return {
        'gross_fte': round(gross_fte, 1),
        'gross_contacts': round(total_prevented),
        'gross_seconds': 0,
        'gross_saving': round(gross_fte * cost),
        'mechanism': f"Escalation: {total_prevented:,.0f} prevented × 15min = {hours_saved:,.0f}hrs → {gross_fte:.1f} FTE",
        'mechanism_detail': [],
        'eligible_volume': sum(q['volume'] for q in queues),
    }


def _gross_transfer(init, queues, affected_fte, cost, net_hours, impact, adoption, pools):
    """
    Transfer reduction: preventable transfers avoided × extra time per transfer → FTE
    
    Transfers differ from escalations: transfers are lateral (agent-to-agent within same tier),
    while escalations go up-tier. Transfer extra time is typically shorter (3 min vs 5 min).
    """
    total_prevented = 0
    for q in queues:
        transfer_rate = q.get('transfer', q.get('transfer_rate', 0))
        complexity = q.get('complexity', 0.5)
        # Preventable share: higher for simple intents (routing errors), lower for complex
        prev_share = q.get('preventable_transfer_pct', max(0.15, 0.55 - complexity * 0.40))
        preventable = q['volume'] * transfer_rate * prev_share * impact * adoption
        total_prevented += preventable
    
    extra_sec_per_transfer = 180  # 3 min per prevented transfer (shorter than escalation)
    hours_saved = (total_prevented * extra_sec_per_transfer) / 3600
    gross_fte = hours_saved / max(net_hours, 1)
    
    return {
        'gross_fte': round(gross_fte, 1),
        'gross_contacts': round(total_prevented),
        'gross_seconds': 0,
        'gross_saving': round(gross_fte * cost),
        'mechanism': f"Transfer: {total_prevented:,.0f} prevented × 3min = {hours_saved:,.0f}hrs → {gross_fte:.1f} FTE",
        'mechanism_detail': [],
        'eligible_volume': sum(q['volume'] for q in queues),
    }


def _gross_repeat(init, queues, affected_fte, cost, net_hours, impact, adoption, pools):
    """
    Repeat reduction: repeat contacts eliminated × AHT → FTE
    
    V6 fix: Raw CCaaS exports often cover 1-3 months, making repeat detection
    unreliable (customer rarely contacts twice in one month). If weighted avg
    repeat rate < 2%, fall back to industry default (1 - avgFCR) as proxy.
    """
    # V6: Check if repeat data is implausibly low (data artifact from short sample)
    total_vol = sum(q['volume'] for q in queues)
    if total_vol > 0:
        weighted_repeat = sum(q.get('repeat', 0) * q['volume'] for q in queues) / total_vol
    else:
        weighted_repeat = 0
    
    REPEAT_FLOOR = 0.02  # Below this, data is unreliable
    use_fallback = weighted_repeat < REPEAT_FLOOR
    
    if use_fallback:
        # Derive from FCR: repeat ≈ (1 - FCR) × 0.6 — not all non-FCR contacts are repeats
        weighted_fcr = sum(q.get('fcr', 0.75) * q['volume'] for q in queues) / max(total_vol, 1)
        fallback_repeat = max(0.05, (1 - weighted_fcr) * 0.60)
    
    total_eliminated = 0
    for q in queues:
        repeat_rate = fallback_repeat if use_fallback else q.get('repeat', 0)
        eliminable = q['volume'] * repeat_rate * impact * adoption
        # Cap at 70% of actual repeats
        eliminable = min(eliminable, q['volume'] * repeat_rate * 0.70)
        total_eliminated += eliminable
    
    avg_aht_sec = sum(q['aht'] * 60 * q['volume'] for q in queues) / max(sum(q['volume'] for q in queues), 1)
    hours_saved = (total_eliminated * avg_aht_sec) / 3600
    gross_fte = hours_saved / max(net_hours, 1)
    
    return {
        'gross_fte': round(gross_fte, 1),
        'gross_contacts': round(total_eliminated),
        'gross_seconds': 0,
        'gross_saving': round(gross_fte * cost),
        'mechanism': f"Repeat: {total_eliminated:,.0f} contacts eliminated → {hours_saved:,.0f}hrs → {gross_fte:.1f} FTE"
                     + (f" (using FCR-derived {fallback_repeat:.0%} rate — raw data too sparse)" if use_fallback else ""),
        'mechanism_detail': [],
        'eligible_volume': sum(q['volume'] for q in queues),
    }


def _gross_location(init, queues, affected_fte, cost, impact, adoption, pools, params):
    """
    Location: FTE migrated × cost arbitrage. NO workload reduction.
    The initiative moves people, not removes work.
    """
    # What % of affected FTE can actually be migrated
    migratable_share = 0
    total_vol = sum(q['volume'] for q in queues)
    if total_vol > 0:
        migratable_vol = sum(q['volume'] * q.get('migration_readiness', 0) for q in queues)
        migratable_share = migratable_vol / total_vol
    
    fte_migrated = affected_fte * migratable_share * impact * adoption
    cost_arbitrage = params.get('locationArbitrage', 0.35)
    saving = fte_migrated * cost * cost_arbitrage
    
    return {
        'gross_fte': 0,  # Location doesn't reduce FTE — it reduces cost
        'gross_contacts': 0,
        'gross_seconds': 0,
        'gross_saving': round(saving),
        'gross_fte_migrated': round(fte_migrated, 1),
        'mechanism': f"Location: {fte_migrated:.1f} FTE migrated × {cost_arbitrage:.0%} arbitrage = ${saving:,.0f}/yr",
        'mechanism_detail': [],
        'eligible_volume': total_vol,
        '_is_location': True,
    }


def _gross_shrinkage(init, affected_fte, cost, impact, adoption, pools, params):
    """
    Shrinkage: reduce shrinkage % → release capacity → FTE equivalent
    """
    current_shrinkage = params.get('shrinkage', 0.30)
    shrinkage_reduction = current_shrinkage * impact * adoption
    # Can't go below target floor
    target = params.get('targetShrinkage', 0.22)
    max_reduction = max(0, current_shrinkage - target)
    shrinkage_reduction = min(shrinkage_reduction, max_reduction)
    
    fte_freed = affected_fte * shrinkage_reduction
    saving = fte_freed * cost
    
    return {
        'gross_fte': round(fte_freed, 1),
        'gross_contacts': 0,
        'gross_seconds': 0,
        'gross_saving': round(saving),
        'mechanism': f"Shrinkage: {shrinkage_reduction:.1%} reduction on {affected_fte} FTE → {fte_freed:.1f} FTE",
        'mechanism_detail': [],
        'eligible_volume': 0,
    }


def _gross_generic(init, affected_fte, cost, impact, adoption):
    """
    Fallback generic formula for unknown levers.
    Conservative: 50% haircut on raw impact.
    """
    raw_fte = affected_fte * impact * adoption * 0.75  # 25% safety haircut (pools provide primary ceiling)
    saving = raw_fte * cost
    
    return {
        'gross_fte': round(raw_fte, 1),
        'gross_contacts': 0,
        'gross_seconds': 0,
        'gross_saving': round(saving),
        'mechanism': f"Generic: {affected_fte} FTE × {impact:.0%} × {adoption:.0%} × 50% safety = {raw_fte:.1f} FTE",
        'mechanism_detail': [],
        'eligible_volume': 0,
    }
