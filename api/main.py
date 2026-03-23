import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.auth.router import router as auth_router
from api.billing.router import router as billing_router
from api.core.config import settings
from api.core.database import init_db
from api.credits.router import router as credits_router
from api.routes import market, options

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rnd_api")

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Risk-Neutral Density Probabilities API — backend para análisis de opciones.",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8080", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event() -> None:
    await init_db()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = round((time.perf_counter() - start) * 1000, 1)
    logger.info(f"{request.method} {request.url.path} → {response.status_code} ({elapsed}ms)")
    response.headers["X-Response-Time-Ms"] = str(elapsed)
    return response


app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(credits_router)
app.include_router(options.router)
app.include_router(market.router)


@app.get("/health", tags=["health"])
async def health_check():
    return {
        "status": "ok",
        "version": settings.app_version,
        "message": f"{settings.app_name} running",
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Error no capturado en {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Error interno del servidor."})
