import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import models  # noqa: F401  (registers tables on Base)
from config import settings
from database import Base, engine
from routes import appointments, auth, billing, doctors, patients
from services import ServiceError

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("hospital_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)  # swap for Alembic migrations later
    logger.info("Database ready")
    yield


app = FastAPI(
    title="Hospital Management API",
    version="2.0.0",
    description=(
        "Doctor/Patient/Appointment management API with JWT auth and "
        "role-based access control (admin, doctor). Authenticate via "
        "/api/v1/auth/login, then use the Authorize button with your token."
    ),
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Auth", "description": "Register a login and exchange credentials for a JWT."},
        {"name": "Doctors", "description": "Admin-managed doctor records. Reads are public; writes need admin."},
        {
            "name": "Patients",
            "description": "Patient records. A doctor-role login only ever sees/edits their own patients.",
        },
        {
            "name": "Appointments",
            "description": "30-minute appointment slots per doctor; overlapping bookings are rejected.",
        },
        {
            "name": "Billing",
            "description": "Consultation billing per patient/doctor/appointment, plus admin revenue reports.",
        },
        {"name": "Health", "description": "Liveness check."},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,  # explicit list from .env, never "*" with credentials
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------- Level 16: basic in-memory rate limiting ----------
# Not for production (per-process, resets on restart) - a real deployment
# should use Redis-backed limiting instead. Good enough to demonstrate the pattern.
RATE_LIMIT_REQUESTS = settings.RATE_LIMIT_REQUESTS
RATE_LIMIT_WINDOW_SECONDS = settings.RATE_LIMIT_WINDOW_SECONDS
_request_log: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def rate_limiter(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    log = _request_log[client_ip]
    while log and now - log[0] > RATE_LIMIT_WINDOW_SECONDS:
        log.popleft()
    if len(log) >= RATE_LIMIT_REQUESTS:
        return JSONResponse(
            status_code=429,
            content={"error": {"code": "rate_limited", "message": "Too many requests, slow down"}},
        )
    log.append(now)
    return await call_next(request)


# ---------- Level 14: measure + log response time on every request ----------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    response.headers["X-Process-Time-Ms"] = f"{ms:.1f}"
    logger.info("%s %s -> %s (%.1f ms)", request.method, request.url.path, response.status_code, ms)
    return response


# ---------- Level 16: uniform error format for every kind of failure ----------
@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    # Catches 401/403/404/405 raised by FastAPI/Starlette itself (auth, routing)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "http_error", "message": exc.detail}},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # Pydantic/FastAPI validation errors (422) get the same {"error": {...}} shape
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "details": exc.errors(),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    # Last-resort safety net: never leak a raw traceback to the client
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "An unexpected error occurred"}},
    )


for router in (auth.router, doctors.router, patients.router, appointments.router, billing.router):
    app.include_router(router, prefix=settings.API_PREFIX)


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok"}
