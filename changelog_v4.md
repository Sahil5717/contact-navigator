# Contact Navigator v4 — Changelog

## Architecture: Pool-Based Benefits Engine (CR-015v2)

### What Changed
The entire benefits calculation engine has been rewritten from exponential diminishing-returns curves to a **pool-based netting methodology**. This is the consulting-grade approach used by EY/McKinsey/BCG for contact centre transformation business cases.

### Why
The v3 engine used a single generic formula for all levers:
```
effective = cap × (1 - e^(-k × cumulative/cap))
```
This produced mathematically correct diminishing returns but:
- Was disconnected from actual client data (pools were hardcoded % caps)
- Treated all levers identically (deflection, AHT, location all used same formula)
- Could not trace back to intent-level opportunity
- No auditable gross → net waterfall for partner review

### New Architecture (4 new engine modules)

#### 1. `engines/intent_profile.py` — Intent Enrichment
Enriches each queue/intent with:
- **Deflection eligibility**: repeatability, emotional risk, auth requirement, containment feasibility
- **AHT decomposition**: talk/hold/search/wrap breakdown (only search+wrap are reducible)
- **Transfer classification**: preventable vs structural transfers
- **Migration readiness**: digital channel suitability score

When detailed intent data isn't available, heuristics derive from complexity, channel, and volume patterns.

#### 2. `engines/pools.py` — Opportunity Pool Calculator
Computes **finite ceilings** for each lever from actual data:

| Pool | Ceiling Formula | Unit |
|------|----------------|------|
| Deflection | Σ(Volume × Eligible% × Containment) | contacts/yr |
| AHT Reduction | Σ(Volume × Reducible_seconds) | seconds/yr |
| Transfer | Σ(Volume × Preventable_rate × Extra_time) | transfers/yr |
| Escalation | Σ(Volume × Preventable_escalations × Extra_time) | escalations/yr |
| Repeat/FCR | Σ(Volume × Repeat_rate × FCR_gap) | contacts/yr |
| Location | Σ(FTE × Migratable_share × Cost_arbitrage) | FTE |
| Shrinkage | Total_FTE × (Current − Target_shrinkage) | FTE |

All pools convert to FTE equivalent via: `hours_saved / net_productive_hours_per_FTE`

Includes **automatic volume annualization** — detects when raw data is a sample period and scales to annual capacity.

#### 3. `engines/gross.py` — Lever-Specific Gross Impact
Each lever type has its own physics instead of a single generic formula:

- **Deflection**: contacts_deflected × AHT → hours → FTE
- **AHT Reduction**: seconds_saved_per_contact × eligible_volume → hours → FTE  
- **Transfer Reduction**: transfers_avoided × extra_time_per_transfer → FTE
- **Escalation Reduction**: escalations_prevented × extra_time → FTE
- **Location Strategy**: FTE_migrated × cost_arbitrage (NO workload reduction — cost only)
- **Shrinkage**: shrinkage_%_reduction × total_FTE

#### 4. `engines/waterfall.py` — Pool Consumption Netting (rewritten)
New `run_waterfall()` flow:
```
1. Enrich intents → compute pools
2. Sort initiatives: Layer → Lever → Score
3. For each initiative:
   a. Compute gross impact (lever-specific physics)
   b. Net = min(gross, remaining_pool)  ← POOL NETTING
   c. Apply safety caps (per-initiative, per-role)
   d. Consume from pool
   e. Phase with ramp-up
4. Financial projection (NPV, IRR, scenarios, sensitivity)
```

### New Frontend Sections (Impact Dashboard)

1. **🏊 Opportunity Pool Utilization** — Visual progress bars showing ceiling vs consumed for each pool. Color-coded: green (<50%), amber (50-80%), red (>80%).

2. **🔍 Benefit Audit Trail** — Full table showing each initiative's gross FTE → net FTE → saving, with lever tags, cap indicators (🔒 Pool cap, ⚡ Safety cap, ✅ Full), and mechanism descriptions.

3. **Updated Initiative Contributions** — Now shows Gross FTE, Net FTE, and Pool Status columns.

### API Changes
- `GET /api/data` and `GET /api/waterfall` now include:
  - `poolUtilization`: per-pool ceiling/consumed/remaining/utilization%
  - `poolSummary`: aggregate pool statistics
  - `auditTrail`: per-initiative gross→net detail with mechanisms
- All existing endpoints remain backward-compatible

### Validation Results
| Metric | v3 (Old) | v4 (New) | Notes |
|--------|----------|----------|-------|
| 3 initiatives enabled | 433 FTE (35.6%) | 5 FTE (0.4%) | v3 was wildly inflated |
| 55 initiatives enabled | N/A | 272 FTE (22.4%) | Realistic consulting range |
| AHT pool at 55 inits | N/A | 100% utilized | Natural ceiling works |
| Single init max | ~40% of role | ≤12% of role | Safety caps maintained |
| Per-role max | ~50% | ≤35% | Per-role saturation maintained |

### Files Modified
- `engines/waterfall.py` — Complete rewrite of `run_waterfall()`, new imports
- `app.py` — Added pool data to `_build_demo_object()`
- `templates/index.html` — Added pool utilization + audit trail sections, Jinja raw blocks

### Files Added
- `engines/intent_profile.py` — Intent enrichment engine (220 lines)
- `engines/pools.py` — Opportunity pool calculator (370 lines)  
- `engines/gross.py` — Lever-specific gross impact formulas (260 lines)
