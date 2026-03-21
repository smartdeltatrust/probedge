# PROJECT STATUS — RND SaaS
**Última actualización:** 2026-03-21
**Autor:** ClawDio GPTierrez

---

## 🎯 Visión del producto

SaaS para traders retail avanzados que visualiza la **densidad neutral al riesgo (RND)** implícita en opciones de acciones USA. Tipo "Bloomberg / Tastytrade" sin plataforma institucional.

**Modelo de negocio:** Suscripción mensual $19–29 USD via Stripe US → Mercury

---

## ✅ LO QUE ESTÁ HECHO

### Fase 0 — Fundación (completada 2026-03-20)
- [x] Migración de Finviz/yfinance → Massive API + FMP
- [x] Tema Bloomberg dark (fondo negro, velas verde/rojo)
- [x] Fix compatibilidad NumPy 2.0 (np.trapz → np.trapezoid)
- [x] Capa data_provider modularizada (massive.py + fmp.py)
- [x] App Streamlit funcional con nuevos providers

### Fase 1 — Backend FastAPI (completada 2026-03-21)
- [x] Scaffolding FastAPI (api/, routes/, models/, core/)
- [x] `GET /health` — health check
- [x] `GET /options/{ticker}/expiries` — vencimientos reales Massive
- [x] `GET /options/{ticker}/chain` — cadena de opciones (302 contratos SPY, Greeks, IV)
- [x] `GET /options/{ticker}/rnd` — RND real Breeden-Litzenberger (integral = 1.0 ✅)
- [x] `GET /options/{ticker}/probabilities` — P(S_T > K) y P(S_T < K)
- [x] `GET /market/{ticker}/quote` — precio spot + datos de mercado FMP
- [x] `GET /market/{ticker}/history` — historial OHLCV hasta 252 días FMP
- [x] Schemas Pydantic completos
- [x] Logging estructurado + middleware de timing
- [x] 6 tests de integración (6/6 pasando)
- [x] Dockerfile + render.yaml listos para deploy
- [x] systemd service (rnd-api.service) — arranca automático con la máquina

---

## 🚧 LO QUE FALTA

### Fase 2 — Auth + Stripe (BLOQUEADA — necesita keys de Stripe)
- [ ] JWT auth (login, registro, refresh tokens)
- [ ] Base de datos usuarios (PostgreSQL o SQLite para MVP)
- [ ] Integración Stripe: suscripciones recurrentes
- [ ] Webhook Stripe (activar/desactivar acceso según pago)
- [ ] Middleware de autorización en todos los endpoints
- [ ] Rate limiting por plan

**🔑 Pendiente de Leo:** API keys de Stripe (dashboard.stripe.com → Developers → API keys)

### Fase 3 — MVP Launch
- [ ] Frontend: Streamlit consume el API (no cálculos directos)
- [ ] Deploy en Render (render.yaml ya listo)
- [ ] Dominio propio
- [ ] Migración licencias de datos: FMP+Massive para redistribución

### Fase 4 — Post-MVP
- [ ] Frontend React/Next.js
- [ ] Múltiples vencimientos simultáneos
- [ ] Análisis comparativo de tickers
- [ ] Panel de analytics de uso

---

## 📊 KPIs del producto

### Técnicos (Fase 1 ✅)
| KPI | Meta | Actual |
|-----|------|--------|
| Endpoints funcionales | 7 | 7 ✅ |
| Tests pasando | 100% | 6/6 (100%) ✅ |
| RND normalizada (integral ≈ 1) | ≈ 1.0 | 1.000 ✅ |
| E[S_T] vs Forward teórico | < 0.5% error | 0.02% ✅ |
| Latencia /health | < 50ms | ~1.4ms ✅ |
| Latencia /expiries | < 10s | ~7.4s (API externa) ✅ |

### De negocio (Fase 2–3, pendientes)
| KPI | Meta MVP |
|-----|----------|
| Usuarios pagantes mes 1 | 10 |
| MRR mes 1 | $190–290 USD |
| Churn mensual | < 20% |
| CAC | < $30 |

---

## 🔧 Stack técnico

| Capa | Tecnología |
|------|-----------|
| Backend | FastAPI + Uvicorn |
| Datos opciones | Massive (ex-Polygon) API |
| Datos mercado | FMP (Financial Modeling Prep) |
| Pagos | Stripe US + Mercury |
| Deploy | Render (render.yaml listo) |
| Contenedor | Docker (Dockerfile listo) |
| Tests | pytest + httpx |
| Proceso | systemd user service |

---

## 📁 Estructura actual del repo

```
Risk-Neutral-Density-Probabilities/
├── app.py                    ← Streamlit (Fase 0, sigue funcional)
├── api/
│   ├── main.py               ← FastAPI app + logging + CORS
│   ├── core/config.py        ← Settings (pydantic-settings)
│   ├── models/schemas.py     ← Schemas Pydantic completos
│   └── routes/
│       ├── options.py        ← /expiries /chain /rnd /probabilities
│       └── market.py         ← /quote /history
├── modules/
│   ├── data_provider/
│   │   ├── massive.py        ← Opciones via Massive
│   │   └── fmp.py            ← OHLC via FMP
│   ├── utils.py              ← compute_rnd_from_calls + helpers
│   └── plots.py              ← Visualización Streamlit
├── tests/test_api.py         ← 6 tests de integración
├── Dockerfile                ← Listo para producción
├── render.yaml               ← Deploy en Render
├── rnd-api.service           ← systemd service
└── requirements.txt
```
