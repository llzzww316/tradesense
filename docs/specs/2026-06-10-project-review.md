# TradeSense Project Review - 2026-06-10

## Scope

This note records a lightweight project review covering the FastAPI backend,
local VIPDOC data provider, backtest framework, static frontend, tests, and
deployment documentation.

No code changes were made during the review.

## Findings

### 1. API input validation is too loose

- `server.py` documents `count` as max 2000, but the FastAPI `Query` definition
  does not enforce `le=2000`.
- `ma_period`, date strings, and replay range strings are accepted without
  strict range or format validation.
- `backtest/api.py` accepts values such as `initial_capital`, `slippage_ticks`,
  `margin_rate`, `fee_per_lot`, `lot_size`, and stock fee rates without
  positive/range constraints.

Recommended fix:

- Add Pydantic/FastAPI constraints such as `gt=0`, `ge=0`, `le=...`.
- Validate date/range fields before passing them to `pandas.Timestamp`.
- Add tests for rejected negative, zero, oversized, and malformed inputs.

### 2. Frontend simulated trade log has an HTML injection surface

- `frontend/app.js` renders `simAccount.tradeLogs` with `innerHTML`.
- Trade logs can be restored from `localStorage`, so local data pollution can
  inject HTML into the page.
- `frontend/backtest.js` already has `htmlEscape`; the replay page should follow
  the same pattern.

Recommended fix:

- Escape all dynamic fields before inserting them into HTML strings, or render
  table rows with DOM APIs and `textContent`.

### 3. Production deployment details are in README

- `README.md` contains production IP address, root SSH/scp examples, deployment
  path, and service-management commands.
- This is acceptable only for a strictly private repository.

Recommended fix:

- Move private deployment details into a private operations note.
- Keep only generic deployment examples in `README.md`.

### 4. Backtest loop has potential performance bottlenecks

- `backtest/engine.py` converts DataFrame rows using `iterrows()`.
- Strategy calls happen once per bar, and some strategies repeatedly scan
  `ctx.history`.

Recommended fix:

- Replace `iterrows()` with `itertuples()` or a vectorized conversion path.
- Keep strategy indicators incremental in `ctx.state` where possible.
- Add a benchmark for typical 5m/1m replay and backtest workloads.

### 5. Data-provider failures can become indistinguishable from empty data

- `data_provider.py` catches broad exceptions during file reads and returns an
  empty DataFrame.
- The API can then report only "no data", losing whether the root cause was a
  missing file, corrupt file, unsupported period, or parsing failure.

Recommended fix:

- Use typed exceptions or structured error results for file-not-found,
  parse-failure, and invalid-input cases.
- Preserve diagnostic detail in logs and user-facing API errors where safe.

### 6. Annualized metrics use a fixed bars-per-year assumption

- `backtest/metrics.py` defaults to `252 * 24 * 12`, which does not fit every
  period or market session.

Recommended fix:

- Derive `bars_per_year` from `BacktestConfig.period` and instrument/session
  assumptions, or expose it as a configurable field.

## Optimization Opportunities

- Add a `pyproject.toml` with common commands/config for `pytest`, linting, and
  formatting.
- Add API workload guards for maximum date span, maximum returned bars, and
  expensive backtest requests.
- Make CORS origins configurable by environment for production deployments.
- Split `frontend/app.js` into smaller modules such as replay, chart, and
  simulated trading.
- Confirm all project documentation is UTF-8 and avoid relying on shell default
  encodings.
- Avoid depending on local `.venv` state; document reproducible setup with
  `uv sync` or a standard virtualenv command.

## Verification Notes

- Python syntax compilation completed successfully with the Codex bundled
  Python runtime.
- Full pytest execution was not completed:
  - `pytest` was not on PATH.
  - `.venv/Scripts/python.exe` failed with an uv trampoline permission error.
  - Codex bundled Python did not have `pytest` installed.

## Suggested Priority

1. Add API input validation and tests.
2. Fix frontend trade-log escaping.
3. Move private deployment details out of `README.md`.
4. Improve data-provider error reporting.
5. Add benchmark and performance cleanup for the backtest loop.
