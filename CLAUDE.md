# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Two surfaces over the same math + data layer:

1. **Streamlit app** (`app.py`) — interactive RND probability-cone visualisation (terminal-style dark theme, OHLC + 68/95% RND bands + optional density heatmap). This is the original product.
2. **FastAPI service** (`api/`) — same RND math exposed as a REST API with auth/billing/credits, intended to back a SaaS frontend.

Both share `modules/utils.py` (math) and `modules/data_provider/` (market-data adapters).

**Workspace context:** Per `/home/leo/projects/CLAUDE.md` and `docs/superpowers/specs/2026-04-29-prob-edge-roadmap-design.md`, this repo is **scheduled to be merged into `prob-edge-api`** (currently `Fast_API_Login/`). Phase-1 RND endpoints are done (6/6 tests). Don't invest in restructuring the FastAPI side that will be undone by the merge — instead make changes that port cleanly. Read `PROJECT_STATUS.md` for current Fase 0/1/2 status.

## Common commands

All commands assume the repo's `.venv` is active and a populated `.env` exists (see `.env.example`).

```bash
# Streamlit (legacy front)
streamlit run app.py

# FastAPI dev (auto-reload)
uvicorn api.main:app --reload --port 8000
# Docs: http://localhost:8000/docs

# All API integration tests (requires live FMP + tastytrade keys in .env)
pytest tests/test_api.py -v

# Single test
pytest tests/test_api.py::test_rnd_is_valid_density -v

# Run the API as a long-running user service (already installed)
systemctl --user start rnd-api.service
systemctl --user status rnd-api.service
journalctl --user -u rnd-api.service -f

# Docker (production-shaped)
docker build -t rnd-api .
docker run --env-file .env -p 8000:8000 rnd-api
```

There is no Makefile, no linter config, and no type-checker config — keep new code consistent with the existing style, don't introduce ruff/mypy unless the user asks.

## Architecture: where things live

```
app.py                         Streamlit entrypoint (Fase 0, still working)
api/
  main.py                      FastAPI app, CORS, request-timing middleware, init_db on startup
  core/                        config (pydantic-settings), database (SQLAlchemy async + SQLite),
                               security (JWT), rate_limit (per-plan limits via `limits` lib)
  auth/                        registration / login / refresh, User model, get_current_user dep
  billing/                     Stripe checkout + webhook (activates plans on checkout.session.completed)
  credits/                     Credit wallet + ledger; `require_credits_dependency(N, reason)`
                               is what protects expensive endpoints (RND=25, Probabilities=10)
  routes/
    options.py                 /options/{ticker}/{expiries,chain,rnd,rnd/preview,probabilities}
    market.py                  /market/{ticker}/{quote,history}  (FMP)
modules/
  utils.py                     ★ The math. Breeden-Litzenberger pipeline, PCHIP interpolation,
                               clean-call construction via put-call parity, forward correction.
  plots.py                     Plotly figures for the Streamlit app
  data_provider/
    tastytrade_options.py      Live options chain + expiries + spot (via dxFeed quotes)
    dxfeed_quotes.py           Spot/quote streaming used by tastytrade adapter
    fmp.py                     Historical OHLC (Financial Modeling Prep)
    massive.py                 Legacy Polygon/Massive options adapter (kept; tastytrade is preferred)
    fmp_*.py                   Fundamentals adapters used by Streamlit fundamentals tab
  domain/, services/           Used by the Streamlit fundamentals views
  llm_anthropic.py             Anthropic-backed analysis used in the Streamlit app
tests/test_api.py              All integration tests (single file, async, hits real FMP + tastytrade)
```

**Routing chain (FastAPI):** `api/main.py` mounts `auth`, `billing`, `credits`, `options`, `market`. Protected endpoints stack three deps: `rate_limit_dependency` → `require_credits_dependency(N, reason)` (only on paid endpoints) → `get_current_user`.

**Math invariant to preserve:** `compute_rnd_from_calls` / `compute_rnd_from_clean_calls` must produce a density whose integral over `price_grid` is ≈ 1.0. `test_rnd_is_valid_density` enforces `|integral − 1| < 0.05` and is the single most important regression guard. The pipeline is documented mathematically in `metodologia.md` and at the section level in `PROJECT_SPEC.md` §4 — read those before changing anything in `compute_rnd_from_*`, `_clean_calls_from_chain`, the PCHIP interpolation, or the forward-correction step.

## Things that are easy to break

- **NumPy 2.0 compatibility.** This codebase uses `np.trapezoid` (not the removed `np.trapz`). Don't reintroduce `np.trapz`.
- **Streamlit and FastAPI share env vars.** Both sides of `render.yaml` declare `TASTYTRADE_*`, `FMP_API_KEY`, `ANTHROPIC_API_KEY`. If you add a new env var to one, mirror it in the other and in `.env.example`.
- **`api/routes/options.py` does `sys.path.insert(0, ...)`** to import `modules/`. The FastAPI app must be launched from the repo root (uvicorn already is, in both render.yaml and the systemd unit) — don't "fix" that import without verifying.
- **The auth model is async-first** (`SQLAlchemy` with `aiosqlite`). The integration tests use `AsyncSessionLocal` directly to credit-top-up test users (`tests/test_api.py:48`); follow that pattern instead of building sync helpers.

## tastytrade auth (operationally non-obvious)

The single read point is `_get_tt_token()` in `modules/data_provider/tastytrade_options.py:127`. It returns a string that is dropped raw into `Authorization: <token>` — when OAuth, it carries the `Bearer ` prefix; when legacy, it's a session-token raw. Don't second-guess that contract — both forms are valid against `api.tastytrade.com` and dxFeed's `/api-quote-tokens`.

Resolution order (most preferred first):

0. **OAuth Personal Grant** (`modules/data_provider/tt_oauth.py`). If `TASTYTRADE_CLIENT_ID` + `TASTYTRADE_CLIENT_SECRET` + `TASTYTRADE_REFRESH_TOKEN` are present, exchanges the refresh token at `POST /oauth/token` for a 15-min `access_token`, cached in process memory with a 2-min safety margin. **This is the only path that works on Render** — tastytrade blocks Render's IP ranges from the legacy `/sessions` login. Refresh token does not expire; rotates only if the user revokes the grant in `my.tastytrade.com → Manage → API Access → Manage OAuth Grants`.
1. **`TASTYTRADE_SESSION_TOKEN`** — pre-generated session token (legacy fallback for emergencies).
2. **`/tmp/tt_token.txt`** — local cache, validated against `/customers/me`.
3. **`TASTYTRADE_PASSWORD`** — `/sessions` login. Only works from "trusted" IPs (Leo's local machine), and tastytrade may still 403 if the trusted-device cookie has expired.
4. **`TASTYTRADE_REMEMBER_TOKEN`** — last-resort fallback (typically expires faster than expected).

Operational implications:

- The chain returns `option_type` as `C`/`P` from tastytrade (not `call`/`put` — fix in commit 622ac76). Anywhere that filters on this column should accept both forms.
- If OAuth starts returning 401 unexpectedly, the most likely cause is `client_secret` rotation — there's a `Regenerate` button on the OAuth Applications panel that invalidates the secret on click and only displays the new one once.

## Conventions specific to this repo

- Spanish is fine in docstrings, log messages, and HTTPException `detail` strings — that matches the existing code and what the user reads in production logs. Don't translate existing Spanish to English without being asked.
- Endpoints surface upstream provider errors as `502` with the underlying message (`detail=f"Error tastytrade API: {exc}"`). Keep that pattern — it's load-bearing for Render-side debugging when IPs get blocked.
- Numeric outputs go through `_safe_float` / `safe_float` to NaN-and-Inf-clean before JSON serialization. Don't return raw NumPy floats from new endpoints.
- Tests are integration tests against live providers, not unit tests. New endpoint tests should follow the existing fixture pattern (`auth_user` → top-up credits via `CreditService` → call protected endpoint).
