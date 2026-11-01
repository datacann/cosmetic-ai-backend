from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.analyze import router as analyze_router
from app.api.auth import router as auth_router
from app.api.subscriptions import router as subscriptions_router
from app.database.session import get_session_factory


app = FastAPI(
    title="Cosmetic AI API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        "http://localhost:8082",
        "http://127.0.0.1:8082",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "cosmetic-ai-api",
    }


@app.get("/ready")
def ready():
    factory = get_session_factory()
    if factory is None:
        raise HTTPException(status_code=503, detail="Veritabanı yapılandırılmamış.")
    try:
        with factory() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Veritabanına ulaşılamıyor. Docker ve PostgreSQL durumunu kontrol edin.",
        ) from exc
    return {"status": "ready", "database": "ok"}


app.include_router(analyze_router)
app.include_router(auth_router)
app.include_router(subscriptions_router)
