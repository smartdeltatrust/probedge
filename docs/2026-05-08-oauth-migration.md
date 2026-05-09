# 2026-05-08 — Migración OAuth de tastytrade y arreglos asociados

## Resumen ejecutivo

El cono de probabilidad estaba caído en Render (`502` en todos los endpoints de opciones, Streamlit imposible de usar) porque tastytrade rechazaba el flujo de auth heredado (login/password/remember-token) desde las IPs de Render. Migramos a **OAuth2 Personal Grant** (renovable sin OTP, soportado oficialmente por tastytrade) y arreglamos un bug pre-existente que ya hacía fallar los handlers FastAPI de opciones aun con auth correcta.

Resultado: cono renderizando en Render con auth permanente y sin intervención manual.

---

## 1. Estado del problema (antes)

### Síntomas observados
- `GET /options/{ticker}/{chain,rnd,probabilities}` → `502` desde el FastAPI.
- Streamlit en Render mostraba "No se pudo conectar con tastytrade. Verifica TASTYTRADE_LOGIN y TASTYTRADE_PASSWORD".
- Cron `scripts/renew_tt_token.py` fallaba con `HTTP 401` (remember token expirado) y `HTTP 403` (login bloqueado por IP) incluso desde la máquina local.

### Diagnóstico (causas raíz)

**Causa A — auth heredada bloqueada por tastytrade.** El flujo `_get_tt_token()` confiaba en uno de:
1. `TASTYTRADE_SESSION_TOKEN` pre-generado (frágil, expira ~24 h, requería un cron desde la máquina local).
2. `/sessions` con `LOGIN`+`PASSWORD` (rechazado con `403` desde Render por IP "no confiable" y a veces también desde local cuando el "trusted device" caduca).
3. `/sessions` con `REMEMBER_TOKEN` (rechazado con `401`, expirado).

Resultado: ninguno de los 3 caminos funciona en Render. Cualquier intento muere en el `RuntimeError` final.

**Causa B — `asyncio.run()` dentro de un event loop activo.** Los adaptadores `fetch_options_snapshot()` (en `tastytrade_options.py:502`) y `get_quotes()` (en `dxfeed_quotes.py:337`) son funciones síncronas que internamente hacen `asyncio.run(...)`. Cuando un handler FastAPI `async def` los llamaba directamente, Python tira `RuntimeError: asyncio.run() cannot be called from a running event loop` y FastAPI lo encapsula como `502`. Bug independiente de la auth — los endpoints estaban rotos aun si la auth funcionaba.

**Causa C — bit-rot en los tests.** `tests/test_api.py` tenía hardcodeado `EXPIRY = "2026-03-23"` (fecha que ya había pasado hace 1.5 meses al momento de la migración).

**Causa D — diagnóstico engañoso en Streamlit.** El bloque de error en `app.py` solo verificaba `TASTYTRADE_LOGIN`/`TASTYTRADE_PASSWORD` y mostraba "verificá esas variables" aun cuando el problema real fuera otro (token corrupto, scope mal seteado, etc.).

---

## 2. Cambios implementados

### Commit `d50cc3e` — `feat: OAuth Personal Grant para tastytrade (renovable sin OTP en Render)`

**Nuevo: `modules/data_provider/tt_oauth.py`** — cliente OAuth2 con cache en memoria.

- `is_oauth_configured()` → bool: chequea las 3 vars en `os.environ` + `.env`.
- `get_oauth_access_token(force_refresh=False) → str`: devuelve un access_token válido. Cachea con expiración real (`expires_in` del response, default 900 s) menos un margen de 120 s. Renueva automáticamente al vencerse.
- `_refresh_access_token() → (access, expires_in)`: `POST /oauth/token` con `grant_type=refresh_token` + `client_id` + `client_secret` + `refresh_token`, body `application/x-www-form-urlencoded`.
- Carga las 3 variables OAuth desde `os.environ` y, si faltan, desde `.env` del repo.
- En errores HTTP del endpoint OAuth, levanta `RuntimeError` con código y body — clave para diagnóstico.

**Modificado: `modules/data_provider/tastytrade_options.py:127`** — `_get_tt_token()` ahora prioriza OAuth.

Nuevo orden de resolución:
1. **OAuth Personal Grant** → retorna `f"Bearer {access_token}"`. Único camino que funciona en Render.
2. `TASTYTRADE_SESSION_TOKEN` (legacy fallback de emergencia).
3. `/tmp/tt_token.txt` (cache local validado).
4. `LOGIN`+`PASSWORD` directo (sólo IPs "trusted").
5. `REMEMBER_TOKEN` (último fallback).

El valor retornado se inyecta tal cual en `Authorization: <token>`. OAuth devuelve con prefijo `Bearer `, los pasos legacy retornan el session-token raw — ambos son aceptados por la API y por dxFeed (`/api-quote-tokens`).

**Modificado: `api/routes/options.py`** — handlers usan `asyncio.to_thread()` para los adaptadores tastytrade.

Tres handlers (`get_expiries`, `get_options_chain`, `_prepare_rnd_data`) envuelven `_get_tt_token()`, `fetch_available_expiries()`, `fetch_options_snapshot()` y `get_spot_price()` con `await asyncio.to_thread(...)`. Esto aísla el `asyncio.run()` interno en un thread auxiliar y permite que el handler `async def` siga corriendo en el event loop principal.

**Modificado: `tests/test_api.py`** — fixture `expiry()` dinámica.

Reemplaza el `EXPIRY = "2026-03-23"` hardcodeado por una fixture que pide `/options/{TICKER}/expiries` en runtime y devuelve el primer expiry ≥ 14 días. Los 3 tests afectados (`test_chain_returns_contracts`, `test_rnd_is_valid_density`, `test_credit_consumption`) ahora pasan automáticamente.

**Modificado: `render.yaml`** — env vars OAuth en ambos servicios (`rnd-api`, `rnd-streamlit`).

Quitamos `TASTYTRADE_LOGIN`/`PASSWORD`/`REMEMBER_TOKEN` (ya no se usan en el camino feliz). Agregamos `TASTYTRADE_CLIENT_ID`, `TASTYTRADE_CLIENT_SECRET`, `TASTYTRADE_REFRESH_TOKEN`. Todas con `sync: false` (se cargan manualmente en el dashboard).

**Nuevo: `.env.example`, `CLAUDE.md`** — documentación.

`.env.example` lista las 3 vars OAuth con nota explicando dónde generarlas (`my.tastytrade.com → Manage → API Access → Manage OAuth Grants`). `CLAUDE.md` documenta toda la arquitectura del repo para futuras sesiones de Claude Code.

**Modificado: `.gitignore`** — sumamos `rnd.db` (SQLite con datos de test) y `tastytrade/` (carpeta personal con screenshots). El `rnd.db` se removió del tracking con `git rm --cached`.

### Commit `5d113e7` — `fix: diagnóstico tastytrade muestra estado OAuth + legacy`

Mejora `app.py` para que el bloque de error muestre el estado real de las 6 variables (3 OAuth + 2 legacy + el mensaje de error original):

```
OAuth (recomendado en Render): CLIENT_ID ✅|❌ | CLIENT_SECRET ✅|❌ | REFRESH_TOKEN ✅|❌
Legacy (sólo IPs conocidas): LOGIN ✅|❌ | PASSWORD ✅|❌
Error: <traza real>
```

### Commit `9b2ab9f` — `fix: OAuth propaga error real en vez de caer silenciosamente al legacy`

`_get_tt_token()` ya no envuelve OAuth en `try/except`. Si las 3 vars OAuth están presentes y OAuth falla, propaga directamente el `RuntimeError` con el detalle (`Invalid JWT`, `Client secret mismatch`, etc.). Antes el error se silenciaba con un `logger.warning` y el usuario veía sólo el mensaje genérico de fallback.

Justificación: si OAuth está configurado, es porque queremos que sea la fuente de verdad. Caer al fallback en Render es imposible (IP bloqueada), así que silenciarlo solo oculta la causa real.

---

## 3. Verificaciones realizadas

| Verificación | Resultado | Notas |
|---|---|---|
| `POST /oauth/token` con refresh_token + client_id + client_secret | `HTTP 200` | `expires_in=900s`, `token_type=Bearer`, `scope=None` |
| `GET /customers/me` con Bearer access_token | `HTTP 200` | Confirmó `id=me`, email del usuario |
| `GET /option-chains/SPY/nested` con Bearer | `HTTP 200` | 35 expirations |
| `GET /api-quote-tokens` con Bearer (dxFeed streamer) | `HTTP 200` | URL WS: `wss://tasty-openapi-ws.dxfeed.com/realtime` |
| Cono RND end-to-end (script directo) | ✅ | SPY @ $737.52, exp 2026-05-22, 442 contratos, **integral RND = 1.0000**, E[S_T] = 738.79 (cuadra con forward teórico) |
| `pytest tests/test_api.py` | **11/13 pasando** | Los 2 que fallan son por bug Stripe pre-existente, ver §5 |
| Streamlit en producción (Render, commit `9b2ab9f`) | ✅ cono renderizando | Tras corregir el `TASTYTRADE_REFRESH_TOKEN` que se había truncado al pegarlo en el dashboard |

---

## 4. Operacional — configuración en Render

### Variables de entorno (en cada uno de los servicios `rnd-api` y `rnd-streamlit`)

Obligatorias:
- `TASTYTRADE_CLIENT_ID` → del panel "OAuth Applications" de tastytrade.
- `TASTYTRADE_CLIENT_SECRET` → del mismo panel; si se pierde, se regenera con el botón "Regenerate" (no rompe el grant existente).
- `TASTYTRADE_REFRESH_TOKEN` → del panel "Manage OAuth Grants"; **JWT de ~549 chars; verificar que el campo del dashboard no lo trunque al pegarlo**.
- `FMP_API_KEY` → para datos OHLC históricos.
- `ANTHROPIC_API_KEY` → para análisis Claude en Streamlit.
- (Solo `rnd-api`) `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `JWT_SECRET_KEY`.

Variables que **se pueden borrar** del dashboard (ya no se usan en el camino feliz):
- `TASTYTRADE_LOGIN`
- `TASTYTRADE_PASSWORD`
- `TASTYTRADE_REMEMBER_TOKEN`
- `TASTYTRADE_SESSION_TOKEN`

### Auto-deploy

Confirmado activo en `rnd-streamlit` (los Events muestran "New commit via Auto-Deploy" tras cada `push origin main`). Verificar el mismo flag en `rnd-api`. Tiempo típico de build → live: 1–3 min.

### Comportamiento esperado del refresh

- El `refresh_token` no expira mientras el grant esté activo en `my.tastytrade.com`.
- El `access_token` expira cada 15 min; `tt_oauth.py` lo renueva automáticamente (margen de 2 min).
- Si el usuario revoca el grant en tastytrade, todos los servicios fallan con `Invalid JWT` y hay que generar un grant nuevo + actualizar el env var en Render.

---

## 5. Pendientes para los próximos pulidos

### Alto impacto

1. **Bug Stripe — comparación naive vs aware datetime** (`api/billing/models.py:39`).
   Síntoma: `TypeError: can't compare offset-naive and offset-aware datetimes` en `Subscription.is_active`. Rompe los 2 tests Stripe (`test_stripe_checkout_flow`, `test_stripe_webhook_activates_subscription`) y muy probablemente también el flujo real de webhook al recibir un `checkout.session.completed`.
   Fix posibles:
   - Cambiar la columna en `models.py` a `DateTime(timezone=True)` y migrar la DB.
   - O wrap el valor leído con `.replace(tzinfo=timezone.utc)` antes de comparar.
   Recomendado: el camino columna `tz=True` para evitar bugs futuros.

2. **Rotar el GitHub Personal Access Token**.
   El remote en `.git/config` tiene un PAT embebido en la URL (`https://ghp_xxxxx@github.com/...`). Quedó expuesto durante la sesión actual. Acción: ir a `github.com → Settings → Developer settings → Personal access tokens`, revocar el actual, generar uno nuevo y reconfigurar el remote — preferentemente vía credential helper o cambiar a SSH.

### Limpieza / deuda técnica

3. **Borrar variables muertas del `.env` local** — `TASTYTRADE_TOKEN`, `TASTYTRADE_REMEMBER_TOKEN`, `TASTYTRADE_LOGIN`, `TASTYTRADE_PASSWORD`. El código nuevo no las necesita y crean confusión.

4. **`scripts/renew_tt_token.py` quedó como código muerto.** OAuth lo reemplaza completamente. Opciones: (a) borrarlo, (b) ponerle un guard `print("DEPRECATED — use OAuth"); sys.exit(0)` arriba.

5. **Sacar `dxfeed_quotes.py` y `tastytrade_options.py` de `asyncio.run()`.** En `api/routes/options.py` lo workaroundeamos con `asyncio.to_thread()`, pero la solución limpia es exponer versiones `async` nativas de `fetch_options_snapshot()` y `get_quotes()`. Eso evita un thread por request y simplifica los handlers. No urgente.

6. **`compute_rnd_from_calls()` también es CPU-bound y bloquea el event loop.** Los handlers de RND deberían correr el cómputo numérico en `asyncio.to_thread()` también — son ~200 ms para `n_grid=400`, lo suficiente para frenar requests concurrentes en Fluent Compute. Bajo impacto al volumen actual, alto impacto si llegamos a 10+ rps.

### Mejora de UX / observabilidad

7. **Telemetría OAuth.** El logger de `tt_oauth.py` emite `OAuth access_token renovado (expira en 900s)` al renovar; sería útil estructurarlo (JSON) y enviarlo a un sistema de logs (Better Stack, Logtail, Datadog) para detectar token rotation issues antes de que afecten al usuario.

8. **Migrar fixtures de `pytest` a usar mock providers.** Los tests actuales son de integración real contra tastytrade + FMP, lo que los hace lentos (43s) y frágiles si tastytrade tiene incidentes. Idea: layer de "mock data provider" que devuelve un snapshot grabado, activado por env var `RND_TEST_MODE=mock`. Mantener una suite separada `tests/test_integration.py` con los reales.

### Pre-merge a `prob-edge-api`

9. Per `/home/leo/projects/CLAUDE.md`, este repo está pendiente de fusionarse con `Fast_API_Login/` como `prob-edge-api`. Antes del merge:
   - Verificar que `tt_oauth.py` esté importable desde la nueva estructura.
   - Revisar que `api/auth/`, `api/billing/`, `api/credits/` (que ya viven aquí) no choquen con la versión de `Fast_API_Login/` (probablemente más reciente).
   - Decidir el destino de `app.py` (Streamlit) — ¿queda en `prob-edge-api` o se mueve a `prob-edge-web` Next.js como dice el spec? El spec sugiere migrar a Next.js, pero hasta que el frontend esté listo, mantener Streamlit funcional.

---

## 6. Apéndice — comandos útiles

### Verificar OAuth localmente

```bash
.venv/bin/python3 -m modules.data_provider.tt_oauth
# Debería imprimir: "access_token (truncado): ... — expira en ~898s"
```

### Probar el endpoint del cono via curl

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"verify@test.com","password":"TestPass123!"}' | jq -r '.tokens.access_token')

curl -s "http://localhost:8000/options/SPY/rnd?expiration=2026-05-22" \
  -H "Authorization: Bearer $TOKEN" | jq '{ticker, expiration, spot, tau_days, n_grid: (.price_grid|length)}'
```

### Forzar renovación del access_token (debugging)

```python
from modules.data_provider.tt_oauth import get_oauth_access_token, reset_cache
reset_cache()
token = get_oauth_access_token()
```

### Pegar el refresh_token correcto en Render (sin truncar)

```bash
grep '^TASTYTRADE_REFRESH_TOKEN=' .env | cut -d= -f2-
```

(Copiar el output entero, pegarlo en el campo de Environment del servicio en Render, "Save Changes".)

---

## 7. Referencias

- Commits de la sesión: `d50cc3e`, `5d113e7`, `9b2ab9f`.
- Documentación oficial OAuth tastytrade: https://developer.tastytrade.com/api-overview/oauth/ (ver `/api-guides/oauth/` para snippets).
- Endpoint OAuth: `POST https://api.tastytrade.com/oauth/token` con `grant_type=refresh_token`, body `application/x-www-form-urlencoded`.
- `client_credentials` grant **no** está soportado por tastytrade — confirmado: `error_code: unsupported_grant_type`.
