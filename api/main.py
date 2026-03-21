from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.core.config import settings
from api.routes import options, market

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Risk-Neutral Density Probabilities API — backend para análisis de opciones.",
)

# CORS — permite localhost (desarrollo)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8080", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(options.router)
app.include_router(market.router)


@app.get("/health", tags=["health"])
async def health_check():
    return {
        "status": "ok",
        "version": settings.app_version,
        "message": f"{settings.app_name} running",
    }
