# Copyright & Intellectual Property Declaration — Prob-Edge

**Project:** Prob-Edge — Risk-Neutral Density SaaS for Options Markets
**Repository:** `github.com/leoromero-quant/prob-edge`
**Author and sole copyright holder:** Leonardo Suárez Romero, PhD
**Email:** leonardosuarezromero@gmail.com
**Public profile:** [leoromero.dev](https://leoromero.dev) · [LinkedIn](https://linkedin.com/in/leonardo-suarez-romero)
**Effective date of this declaration:** 22 May 2026
**Repository state at declaration:** commit `8dbc85a` on branch `main`
**First-commit timestamp of the codebase:** 3 December 2025, 15:59 −06:00 (commit `c6c18f6`)

---

## 1. Statement of authorship and copyright

I, **Leonardo Suárez Romero**, declare that I am the sole author and the sole owner
of the copyright in the source code, written materials, analytical methodology,
documentation, visual design, and any other original work of authorship contained in
this repository as of the effective date above. This includes — without limitation —
all Python source code, the formal methodology document (`metodologia.md`), product
specifications (`PROJECT_SPEC.md`, `PROJECT_STATUS.md`), the data-loading layer, the
quantitative pipeline, the visualization layer, the FastAPI backend, the OAuth and
streaming integrations, and the test suite.

> Copyright © 2025–2026 Leonardo Suárez Romero. All rights reserved.

This declaration is filed as a **timestamped public record** in the project's Git
history, in addition to whatever other registrations or notarization the author may
pursue.

---

## 2. Technical scope of the intellectual property claim

The technical IP claimed under this declaration covers the entire codebase as of the
effective date, and in particular the following original work:

### 2.1. Quantitative core — Breeden–Litzenberger + PCHIP pipeline

A specific implementation of the **Breeden–Litzenberger** identity for recovering
the risk-neutral density (RND) *q(K)* of the underlying terminal price *S_T* from a
discrete observed call surface, formally derived in `metodologia.md`. The
implementation lives in `modules/utils.py` and consists of the following pipeline,
all of which is original work of the author:

1. **Put-call parity reconciliation.** A clean call surface is built from the raw
   chain by combining direct call quotes with synthetic calls derived from put
   prices via parity
   `C_parity(K) = P(K) + S₀ · e^(−qT) − K · e^(−rT)`,
   averaging both signals when available. Implemented in
   `build_clean_calls_from_chain()`.

2. **PCHIP interpolation.** The clean call price function is interpolated across
   strikes using **Piecewise Cubic Hermite Interpolating Polynomial** with no
   extrapolation, deliberately chosen over splines to preserve monotonicity and
   convexity properties of *C(K)* without introducing oscillations near the wings.

3. **Numerical second derivative w.r.t. strike.** The RND is recovered as
   *q(K) = e^(rT) · ∂²C/∂K²*, computed via centered finite differences on the
   interpolated grid, with non-negativity enforcement on the resulting density.

4. **Forward-correction normalization.** A two-step normalization that
   (a) enforces *∫q(K) dK ≈ 1* by trapezoidal integration, and (b) applies a
   bounded affine rescaling of the strike axis so that
   *E_Q[S_T] ≈ S₀ · e^((r−q)T)*,
   matching the no-arbitrage forward. The rescaling factor is clipped to a safe
   range to avoid degeneracies under sparse chains.

5. **Moneyness filtering.** A configurable moneyness window
   `[0.5·S₀, 1.6·S₀]` is applied to the strike set before interpolation to discard
   illiquid deep-OTM strikes that would otherwise distort the density.

### 2.2. Time–price density construction and probability cone

The transformation of the static RND at expiry into a **time-evolving probability
cone** rendered over historical OHLC candles, including the 68% / 95% credibility
bands and the risk-neutral median. Implemented in `build_time_price_density()` and
the plotting helpers under `modules/plots.py`.

### 2.3. Data-loading and integration layer

Original integration code under `modules/data_provider/`:

- `tastytrade_options.py` — OAuth2 Personal Grant flow against tastytrade's
  production API, nested-chain symbol resolution, and a dxLink WebSocket client
  that subscribes to **Greeks** and **Quote** events to assemble a real-time
  options snapshot.
- `dxfeed_quotes.py` — dxLink client for equity Trade / Quote / Summary events.
- `tt_oauth.py` — token rotation and refresh logic for the Personal Grant flow.
- `fmp.py`, `fmp_*.py` — Financial Modeling Prep adapters for historical OHLCV and
  fundamentals.
- `massive.py` — legacy Massive options adapter, retained for fallback.

### 2.4. Streamlit application and FastAPI service

- `app.py` — the consumer-facing Streamlit application, the dark "terminal" theme,
  and the entire UX layer.
- `api/` — FastAPI service with JWT authentication, asynchronous SQLAlchemy
  credit ledger, Stripe billing, per-plan rate limiting, and tastytrade OAuth
  token rotation.
- `tests/` — integration tests for the API surface.

### 2.5. Documentation and product design

- `metodologia.md` — the formal derivation of the RND pipeline.
- `PROJECT_SPEC.md` — product specification: audience, scope, screens.
- `PROJECT_STATUS.md` — phase plan and KPIs.
- `README.md` — public-facing project description.

---

## 3. Project status at the effective date

The project is in **active development**. As of the effective date of this
declaration, the working repository contains:

- A functional Streamlit application running locally and deployed on Render at
  `risk-neutral-density-probabilities-3.onrender.com`.
- A FastAPI backend scaffolded with `/health`, `/options/{ticker}/expiries`,
  `/options/{ticker}/chain`, `/options/{ticker}/rnd`,
  `/options/{ticker}/probabilities`, `/market/{ticker}/quote`,
  `/market/{ticker}/history`.
- A real-time data path through tastytrade OAuth2 + dxLink WebSocket
  (Greeks + Quote streaming) for the options chain, and Financial Modeling Prep
  for historical OHLCV.
- An empirically validated RND pipeline:
  `∫q(K) dK = 1.0000` and `E_Q[S_T]` matching the theoretical forward to within
  the configured tolerance (verified on SPY, expiry 2026-06-18, on 2026-05-22).
- Dockerfile, `render.yaml`, and `.streamlit/config.toml` for container and
  cloud deployment.
- Roughly 8,100 lines of Python under `app.py`, `modules/`, `api/`, and `tests/`.

Work in progress, not yet completed at the effective date, includes the full
Stripe billing flow, the JWT-protected production tier, and the migration of the
Streamlit front-end to consume the FastAPI service exclusively (today it still
calls the quant core directly).

---

## 4. License and use restrictions

This declaration does **not** grant any license to any third party. The repository
is published **all rights reserved**, consistent with the `README.md` statement
"Private — all rights reserved. Demo and source available for inspection;
commercial use requires permission." Inspection of the public source code does not
constitute permission to copy, distribute, sublicense, create derivative works, or
use the code or the methodology for commercial purposes. Any such use requires an
**explicit written license** from the author.

The author reserves all rights under applicable copyright law, including but not
limited to those granted by the Berne Convention, the Universal Copyright
Convention, the WIPO Copyright Treaty, the U.S. Copyright Act (17 U.S.C.), and
the Mexican Federal Copyright Law (*Ley Federal del Derecho de Autor*).

The mathematical results underlying the methodology (Breeden & Litzenberger, 1978,
*Journal of Business* 51(4), 621–651) are in the public scientific domain; what is
claimed here is the **specific implementation, parameter choices, regularization
scheme, software architecture, and product**, not the underlying theorem.

---

## 5. Third-party components

The codebase uses third-party libraries (NumPy, SciPy, pandas, Streamlit, FastAPI,
SQLAlchemy, Plotly, websockets, and others) under their respective open-source
licenses (BSD, MIT, Apache-2.0). External services consumed by the application
(tastytrade, Financial Modeling Prep, Stripe, Anthropic) remain the property of
their respective owners and are accessed under their published terms of service.
No part of this declaration is intended to claim ownership over those components
or services.

---

## 6. Timestamping mechanism

This document is committed to the Git history of the repository
`github.com/leoromero-quant/prob-edge`. The commit that introduces this file
constitutes a publicly verifiable, cryptographically-hashed, timestamped record
of authorship by Leonardo Suárez Romero on the date of the commit. The full
history of the codebase up to this point is preserved in the same repository and
is independently verifiable.

---

— Leonardo Suárez Romero, PhD
22 May 2026

---

## Apéndice en español — Declaración de derechos de autor

Yo, **Leonardo Suárez Romero**, declaro ser el **único autor y único titular** de
los derechos de autor sobre el código fuente, la metodología analítica, la
documentación, el diseño visual y cualquier otra obra original contenida en este
repositorio a la fecha efectiva indicada arriba. Esto incluye, sin limitación,
todo el código en Python, el documento formal de metodología (`metodologia.md`),
las especificaciones de producto (`PROJECT_SPEC.md`, `PROJECT_STATUS.md`), la
capa de carga de datos, el pipeline cuantitativo Breeden–Litzenberger + PCHIP
descrito en la sección 2 de este documento, la capa de visualización, el backend
FastAPI, las integraciones OAuth y de *streaming*, y la suite de pruebas.

> Derechos de autor © 2025–2026 Leonardo Suárez Romero. Todos los derechos reservados.

Esta declaración se inscribe como registro público con marca temporal en el
historial de Git del repositorio. Ninguna disposición de este documento concede
licencia alguna a terceros para copiar, distribuir, sublicenciar, generar obras
derivadas o usar comercialmente el código o la metodología sin **autorización
escrita expresa** del autor. El autor se reserva todos los derechos al amparo de
la Ley Federal del Derecho de Autor (México) y de las convenciones
internacionales aplicables (Berna, OMPI, UCC).

— Leonardo Suárez Romero, PhD
22 de mayo de 2026
