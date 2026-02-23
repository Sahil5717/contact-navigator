# EY Contact Navigator — v5 Changelog

**Release Date:** 2025-02-21  
**Severity:** Critical bug-fix release — v4 had a volume inflation bug that overstated savings by ~20x

---

## CR-021: Volume Annualization Fix (CRITICAL)

### Problem
`pools.py` and `waterfall.py` used a capacity-based heuristic to auto-detect whether raw volumes were "small" relative to implied annual capacity. When CCaaS sample data contained monthly-scale volumes (27K contacts), the heuristic applied a **239x multiplier** — inflating opportunity pools, gross impact, and downstream savings by an order of magnitude.

### Root Cause
```python
# v4 — REMOVED in v5
if total_volume_raw < capacity_annual * 0.10:
    annualization = (capacity_annual * 0.70) / max(total_volume_raw, 1)
    annualization = min(annualization, 500)  # safety cap
```
With 1,215 FTE and ~11 min AHT, implied annual capacity ≈ 9.4M contacts. Raw volume of 27K is 0.3% of capacity → heuristic scaled up by 239x.

### Fix
- **Removed** capacity-based heuristic entirely
- Added explicit `volumeAnnualizationFactor` parameter (default: 12 for monthly data)
- Consultant sets this in `parameters.xlsx` → `Volume Annualization Factor` row
- Values: `1` = annual, `12` = monthly, `250` = daily (working days), `52` = weekly

### Impact
| Metric | v4 (broken) | v5 (fixed) |
|--------|-------------|------------|
| Ann. factor | 239x | 12x |
| Annual volume | 6,569,676 | 329,580 |
| Pool FTE ceiling | 810 | 414.5 |
| CPC | $2,429 | $202.40 |

---

## CR-021b: CPC Unit Consistency Fix

### Problem
`app.py` computed `avgCPC = totalCost / totalVolume` where `totalCost` was annual but `totalVolume` was raw (monthly). Result: $2,429/contact — an absurd value that should be ~$5–$15.

### Fix
- Backend: `avgCPC = totalCost / totalVolumeAnnual`
- ETL now exports both `totalVolume` (raw) and `totalVolumeAnnual` (annualized)
- Frontend fallback: `DEMO.totalCost / (DEMO.totalVolumeAnnual || DEMO.totalVolume * 12)`
- Volume KPI card now shows annual contacts with `/yr` suffix
- Blended CPC card uses annual volume denominator

### Files Changed
- `engines/data_loader.py` — `run_etl()` returns `totalVolumeAnnual`, `volumeAnnualizationFactor`, `avgAHT_unit`
- `app.py` — `_run_all()`, `_build_demo_object()`, `/api/export`
- `templates/index.html` — CPC fallback, volume KPI, blended CPC card

---

## CR-021c: AHT Unit Labeling

### Problem
AHT was computed in **minutes** throughout the ETL and pool engines (`q['aht']` = minutes, pools convert via `q['aht'] * 60`), but the export labeled it as seconds: `f"{data['avgAHT'] * 60:.0f}s"`. This was technically correct (converting to seconds) but confusing for audit.

### Fix
- ETL now includes `avgAHT_unit: 'minutes'` in data dict
- Export label: `"11.3 min (679s)"` — shows both units for clarity
- Frontend already used `avgAHT_min` correctly; no change needed

---

## CR-021d: Unknown Lever Fail-Closed

### Problem
`consume_pool()` gave unknown levers a 50% pass-through haircut. If a new lever type was added to `gross.py` but not to the pool map, it would silently generate half the savings with zero pool constraint.

### Fix
```python
# v5 — fail closed
if pool is None:
    logging.warning(f"consume_pool: unknown lever '{lever}' — returning 0")
    return {'consumed_fte': 0, 'capped': True, 'pool_exhausted': False, 'unknown_lever': True}
```

---

## CR-021e: Location Double-Counting Guard

### Problem (Potential)
Location initiatives (`lever='cost_reduction'`) produce savings via cost arbitrage, not FTE reduction. If these inadvertently entered `role_impact`, the savings would be double-counted: once via role_impact FTE × costPerFTE, and again via `location_yearly`.

### Fix
- Added documentation guard in `waterfall.py` confirming the `continue` at the end of the `is_location` block prevents leakage
- Verified: `_fteImpact = 0` for all cost_reduction initiatives
- Non-cost levers in Layer 3 (e.g. LS08 shrinkage, LS09 AHT) correctly flow through role_impact

---

## Files Modified

| File | Changes |
|------|---------|
| `engines/pools.py` | Removed heuristic, explicit `volumeAnnualizationFactor`, fail-closed unknown lever |
| `engines/data_loader.py` | Added `volumeAnnualizationFactor` to defaults + param_map, returns `totalVolumeAnnual` |
| `engines/waterfall.py` | Documentation guard for location double-counting |
| `app.py` | CPC uses annual volume, export shows both AHT units, demo object includes new fields |
| `templates/index.html` | CPC fallback, volume KPI `/yr`, blended CPC card |

---

## Configuration Note

When onboarding a new client, set the `Volume Annualization Factor` in `data/config/parameters.xlsx`:

| Data Source | Factor |
|-------------|--------|
| 1 month of CCaaS export | 12 |
| 1 year of CCaaS export | 1 |
| 1 week of CCaaS export | 52 |
| Demo / generated data | 12 (default) |

If omitted, defaults to **12** (monthly assumption).
