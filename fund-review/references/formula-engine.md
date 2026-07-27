# formula-engine — 公式 / 单价 / 系数 计算引擎

Pure-Python deterministic computations used by quote-optimize (labor-pricing scanner) and quote-review (compliance pass).

## Public functions

### `compute_software_dev_cost(fp, category, reuse, direct_non_labor=0)`
```
cost = fp × productivity_baseline / man_hours_per_month × man_month_rate + direct_non_labor
```
Where (from standard p.13-14):
- `productivity_baseline = 6.51 人时/FP`（电子政务领域 P50, ±20% adjustable）
- `man_hours_per_month = 174`
- `man_month_rate = 17000 元/人月`（广西取值）
- `category_factor` lookup: {业务处理: 0.8-1.0, 应用集成: 1.0-1.2, 大数据多媒体: 1.0-1.3, 人工智能: 1.0-1.5}
- `reuse_factor` lookup: {高: 1/3, 中: 2/3, 低: 1}

Returns `(min_cost, max_cost)` — a range, because category factor is a range.

### `recommend_per_day_rate(category, reuse)`
For rows priced "by person-day" (most platform sheets), derive a defensible per-day floor/ceiling:
```
floor = man_month_rate × category_factor.min × reuse_factor / workdays_per_month
ceiling = man_month_rate × category_factor.max × reuse_factor / workdays_per_month
```
Returns `(floor, ceiling)` — e.g., 业务处理/低复用 → (~625, ~782) 元/人天.

### `check_unit_price_within_range(actual, range_min, range_max)`
Tri-state: `ok | warn | fail`. Warn = within ±10% of bounds.

### `check_function_point_present(row)`
For software rows: returns `True` if row has plausible FP fields (UFP, ILF/EIF/EI/EO/EQ); else flags as labor-pricing finding.

### `compute_total(unit_price, qty)` / `compute_summary(rows)`
Standard arithmetic; trivial but centralized so rewrite + review share identical rounding (currently `round-half-even, 0 decimals`).

## Why pure-Python?
LLM is bad at consistent arithmetic and citation. All numeric findings should come from this module, then the LLM adds rationale and prose.

## Tests
- `tests/test_formula_engine.py` should cover each function with at least 3 cases each (in-range, edge, out-of-range)
- Property-based tests welcome (hypothesis) for `compute_software_dev_cost`
