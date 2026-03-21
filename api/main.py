import logging
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.core.config import settings
from api.routes import options, market

# --- Logging estructurado ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rnd_api")

# --- App ---
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Risk-Neutral Density Probabilities API — backend para análisis de opciones.",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8080", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Middleware: request logging + timing ---
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = round((time.perf_counter() - start) * 1000, 1)
    logger.info(f"{request.method} {request.url.path} → {response.status_code} ({elapsed}ms)")
    response.headers["X-Response-Time-Ms"] = str(elapsed)
    return response

# --- Routers ---
app.include_router(options.router)
app.include_router(market.router)

# --- Health ---
@app.get("/health", tags=["health"])
async def health_check():
    return {
        "status": "ok",
        "version": settings.app_version,
        "message": f"{settings.app_name} running",
    }

# --- Handler global de errores no capturados ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Error no capturado en {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Error interno del servidor."})
