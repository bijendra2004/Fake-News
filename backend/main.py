from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")

# WORKAROUND: Inject missing Render environment variables for production
if os.getenv("APP_ENV") == "production":
    if not os.getenv("DATA_ENCRYPTION_KEY"):
        os.environ["DATA_ENCRYPTION_KEY"] = "sachlens_prod_data_encryption_key_1234567890_32bytes_fallback"
    if not os.getenv("LLM_PROVIDER"):
        os.environ["LLM_PROVIDER"] = "groq"
    if not os.getenv("SMTP_HOST"):
        os.environ["SMTP_HOST"] = "smtp.gmail.com"
        os.environ["SMTP_PORT"] = "587"
        os.environ["SMTP_USERNAME"] = "sachlensuserauth@gmail.com"
        os.environ["SMTP_PASSWORD"] = "fgdpoylgqrxnmjvm"
        os.environ["SMTP_FROM_EMAIL"] = "sachlensuserauth@gmail.com"

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, sessionmaker

from .auth import (
    create_access_token,
    get_access_token_email,
    issue_otp,
    normalize_email,
    revoke_refresh_token,
    rotate_refresh_token,
    verify_refresh_token,
    verify_otp,
)
from .models import (
    SearchHistory,
    OTPChallenge,
    User,
    get_or_create_user,
    init_db,
)
from .ml.predict import PredictionService
from .cleanup import UPLOAD_DIR, start_cleanup_worker
from .gemini_explainer import GeminiExplainer, GeminiExplanationError
from .mailer import EmailDeliveryError, send_otp_email
from .media import MediaProcessingError, extract_text_from_image, extract_text_from_url, transcribe_audio_file
from .security import (
    apply_security_headers,
    build_https_redirect_url,
    enforce_body_size_limit,
    get_client_ip,
    get_device_fingerprint,
    is_public_api_path,
    is_state_changing_method,
    load_security_settings,
    rate_limiter,
    should_redirect_to_https,
    validate_captcha_token,
)
from .upload_safety import process_upload

def resolve_database_url() -> str:
    configured = os.getenv("DATABASE_URL", "").strip()
    if configured:
        if configured.startswith("postgres://"):
            # Render/Heroku-style URL alias for SQLAlchemy compatibility.
            return configured.replace("postgres://", "postgresql://", 1)
        return configured

    if os.getenv("APP_ENV", "development").lower() == "production":
        raise RuntimeError("DATABASE_URL must be set in production")
    return "sqlite:///./sachlens.db"


DATABASE_URL = resolve_database_url()
DEVICE_HEADER_NAME = "X-Device-Fingerprint"

settings = load_security_settings()
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("sachlens.backend")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

_is_production = os.getenv("APP_ENV", "development").lower() == "production"
app = FastAPI(
    title="SachLens API",
    version="0.2.0",
    debug=(not _is_production and os.getenv("APP_DEBUG", "false").lower() in {"1", "true", "yes", "on"}),
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
)

prediction_service = PredictionService()
gemini_explainer = GeminiExplainer()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class PredictRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class PredictResponse(BaseModel):
    percentage: int
    verdict: str
    explanation: list[str]
    corrected_info: str | None = None
    sources: list[dict[str, str]] | None = None
    grounded: bool = False



class OtpRequestBody(BaseModel):
    email: EmailStr


class OtpVerifyBody(BaseModel):
    email: EmailStr
    otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class AuthTokensResponse(BaseModel):
    access_token: str
    email: str | None = None


class GoogleAuthRequest(BaseModel):
    credential: str = Field(min_length=1)


class UploadResponse(BaseModel):
    file_id: str
    kind: str


class PredictMediaResponse(PredictResponse):
    extracted_text: str | None = None
    transcript: str | None = None
    source_domain: str | None = None


class PredictLinkRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class LogoutResponse(BaseModel):
    ok: bool


@app.on_event("startup")
def on_startup() -> None:
    init_db(engine)
    prediction_service.load()
    gemini_key_present = bool((os.getenv("GEMINI_API_KEY") or "").strip())
    logger.info("GEMINI_API_KEY configured: %s", gemini_key_present)
    start_cleanup_worker()


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if should_redirect_to_https(request, settings):
        return RedirectResponse(build_https_redirect_url(request), status_code=308)

    if request.url.path.startswith("/api/"):
        too_large = await enforce_body_size_limit(request, settings.max_request_bytes)
        if too_large is not None:
            apply_security_headers(too_large, settings)
            return too_large

    if is_public_api_path(request.url.path):
        device_fingerprint = get_device_fingerprint(request)
        client_ip = get_client_ip(request)
        if not rate_limiter.allow(f"ip:{request.url.path}:{client_ip}", settings.public_rate_limit_per_minute, 60):
            response = JSONResponse(status_code=429, content={"detail": "Too many requests"})
            apply_security_headers(response, settings)
            return response
        if not rate_limiter.allow(f"device:{request.url.path}:{device_fingerprint}", settings.public_device_limit_per_minute, 60):
            response = JSONResponse(status_code=429, content={"detail": "Too many requests"})
            apply_security_headers(response, settings)
            return response

    try:
        response = await call_next(request)
    except HTTPException as exc:
        response = JSONResponse(status_code=exc.status_code, content=format_http_error(exc))
    except Exception:
        logger.exception("Unhandled error")
        response = JSONResponse(status_code=500, content={"detail": "Internal server error"})

    apply_security_headers(response, settings)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




@app.post("/api/predict", response_model=PredictResponse)
def predict(request: Request, payload: PredictRequest, db: Session = Depends(get_db)) -> PredictResponse:
    return predict_from_text(request, payload.text, db)


@app.post("/api/predict-image", response_model=PredictMediaResponse)
async def predict_image(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)) -> PredictMediaResponse:
    # Require authentication before doing expensive upload processing
    if not get_authenticated_email(request):
        raise HTTPException(status_code=401, detail={"requires_login": True})
    stored_file = await store_analysis_upload(file, {"png", "jpeg", "gif", "webp"})
    try:
        try:
            extracted_text = extract_text_from_image(stored_file)
        except MediaProcessingError as error:
            # If OCR finds no readable text, return a clear, non-error response
            msg = str(error)
            if "No readable text" in msg or "No readable text was found" in msg:
                return PredictMediaResponse(
                    percentage=0,
                    verdict="NO_TEXT_FOUND",
                    explanation=["No readable text found in this image."],
                    corrected_info=None,
                    extracted_text=None,
                )
            # Other media errors are treated as bad requests
            raise HTTPException(status_code=400, detail=msg) from error

        # Reuse the exact same text prediction pipeline (it will re-check auth and record history)
        prediction = predict_from_text(request, extracted_text, db)
        return PredictMediaResponse(**prediction.model_dump(), extracted_text=extracted_text)
    finally:
        # Ensure uploaded file is removed after processing
        try:
            if stored_file.exists():
                stored_file.unlink()
        except Exception:
            logger.exception("Failed to delete uploaded image after analysis")


@app.post("/api/predict-voice", response_model=PredictMediaResponse)
async def predict_voice(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)) -> PredictMediaResponse:
    stored_file = await store_analysis_upload(file, {"wav", "mp3", "webm", "mp4"})
    try:
        transcript = transcribe_audio_file(stored_file)
        prediction = predict_from_text(request, transcript, db)
        return PredictMediaResponse(**prediction.model_dump(), transcript=transcript)
    except MediaProcessingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/predict-link", response_model=PredictMediaResponse)
def predict_link(request: Request, payload: PredictLinkRequest, db: Session = Depends(get_db)) -> PredictMediaResponse:
    try:
        extracted = extract_text_from_url(payload.url)
        prediction = predict_from_text(request, extracted.text, db)
        return PredictMediaResponse(
            **prediction.model_dump(),
            extracted_text=extracted.text,
            source_domain=extracted.source_domain,
        )
    except MediaProcessingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/auth/otp-request")
def otp_request(
    request: Request,
    payload: OtpRequestBody,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    email = normalize_email(payload.email)
    try:
        otp = issue_otp(
            email,
            db,
            remote_ip=get_client_ip(request),
            email_limit=settings.otp_email_limit_per_10_minutes,
            ip_limit=settings.otp_ip_limit_per_10_minutes,
        )
    except ValueError as error:
        raise HTTPException(status_code=429, detail="Too many OTP requests") from error

    background_tasks.add_task(deliver_otp_email_async, email, otp)

    return {"ok": True}


@app.post("/api/auth/otp-verify", response_model=AuthTokensResponse)
def otp_verify(request: Request, payload: OtpVerifyBody, response: Response, db: Session = Depends(get_db)) -> AuthTokensResponse:
    email = normalize_email(payload.email)
    if not verify_otp(email, payload.otp, db, max_attempts=settings.otp_max_attempts):
        raise HTTPException(status_code=400, detail="Invalid OTP")

    access_token = create_access_token(email)
    refresh_token = rotate_refresh_token(email, db)
    set_refresh_cookie(response, refresh_token)
    return AuthTokensResponse(access_token=access_token, email=email)


@app.post("/api/auth/google", response_model=AuthTokensResponse)
def google_auth(request: Request, payload: GoogleAuthRequest, response: Response, db: Session = Depends(get_db)) -> AuthTokensResponse:
    google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    if not google_client_id:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")

    token_payload = verify_google_credential(payload.credential, google_client_id)
    email = normalize_email(str(token_payload["email"]))
    user = get_or_create_user(db, email)
    logger.info("Google sign-in verified for %s (user_id=%s)", email, user.id)

    access_token = create_access_token(email)
    refresh_token = rotate_refresh_token(email, db)
    set_refresh_cookie(response, refresh_token)
    return AuthTokensResponse(access_token=access_token, email=email)


@app.post("/api/auth/refresh", response_model=AuthTokensResponse)
def refresh_tokens(request: Request, response: Response, db: Session = Depends(get_db)) -> AuthTokensResponse:
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh token")

    email = verify_refresh_token(refresh_token, db)
    if not email:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    access_token = create_access_token(email)
    rotated_refresh_token = rotate_refresh_token(email, db, old_token=refresh_token)
    set_refresh_cookie(response, rotated_refresh_token)
    return AuthTokensResponse(access_token=access_token)


@app.post("/api/auth/logout", response_model=LogoutResponse)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> LogoutResponse:
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        email = verify_refresh_token(refresh_token, db)
        if email:
            revoke_refresh_token(refresh_token, db, email=email)
    response.delete_cookie("refresh_token", path="/")
    return LogoutResponse(ok=True)


@app.post("/api/upload/media", response_model=UploadResponse)
async def upload_media(
    request: Request,
    file: UploadFile = File(...),
) -> UploadResponse:
    content_type = (file.content_type or "").lower()
    if content_type.startswith("image/"):
        allowed_kinds = ["png", "jpeg", "gif", "webp"]
    elif content_type.startswith("audio/"):
        allowed_kinds = ["wav", "mp3", "webm", "mp4"]
    else:
        raise HTTPException(status_code=415, detail="Unsupported media type")

    payload = await file.read()
    try:
        destination, result = process_upload(UPLOAD_DIR, file.filename or "upload.bin", payload, allowed_kinds)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        logger.exception("Upload processing failed")
        raise HTTPException(status_code=503, detail="Upload processing unavailable") from error

    return UploadResponse(file_id=destination.name, kind=result.kind)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.exception_handler(HTTPException)
def http_exception_handler(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content=format_http_error(exc))


@app.exception_handler(Exception)
def unhandled_exception_handler(_: Request, exc: Exception):
    logger.exception("Unhandled error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def has_valid_access_token(request: Request) -> bool:
    return get_authenticated_email(request) is not None


def get_authenticated_email(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    if not token or not prediction_service.verify_access_token(token):
        return None
    return get_access_token_email(token)


def predict_from_text(request: Request, text: str, db: Session) -> PredictResponse:
    authenticated_email = get_authenticated_email(request)
    if not authenticated_email:
        raise HTTPException(status_code=401, detail={"requires_login": True})

    user = get_or_create_user(db, authenticated_email)

    prediction = prediction_service.predict(text)
    try:
        explained = gemini_explainer.explain(text, prediction)
    except GeminiExplanationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    db.add(
        SearchHistory(
            user_id=user.id,
            input_text=text,
            prediction_label=prediction["label"],
            confidence=prediction["confidence"],
        )
    )
    db.commit()
    return PredictResponse(
        percentage=explained.percentage,
        verdict=clean_response_text(explained.verdict),
        explanation=[clean_response_text(item) for item in explained.explanation],
        corrected_info=clean_response_text(explained.corrected_info) if explained.corrected_info else None,
        sources=explained.sources if explained.sources else None,
        grounded=explained.grounded,
    )


def verify_google_credential(credential: str, expected_audience: str) -> dict[str, object]:
    request_url = f"https://oauth2.googleapis.com/tokeninfo?id_token={urllib.parse.quote(credential)}"
    try:
        with urllib.request.urlopen(request_url, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="ignore") if error.fp else ""
        logger.warning("Google token verification failed: %s %s", error.code, error_body)
        raise HTTPException(status_code=401, detail="Google sign-in failed") from error
    except Exception as error:
        logger.exception("Google token verification request failed")
        raise HTTPException(status_code=503, detail="Google sign-in verification unavailable") from error

    logger.info("Google tokeninfo response: %s", payload)

    audience = str(payload.get("aud", ""))
    issuer = str(payload.get("iss", ""))
    email_verified = str(payload.get("email_verified", "false")).lower() == "true"
    if audience != expected_audience:
        raise HTTPException(status_code=401, detail="Google sign-in audience mismatch")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        raise HTTPException(status_code=401, detail="Google sign-in issuer mismatch")
    if not email_verified:
        raise HTTPException(status_code=401, detail="Google account email is not verified")
    if not payload.get("email"):
        raise HTTPException(status_code=401, detail="Google account email is missing")

    return payload


def deliver_otp_email_async(email: str, otp: str) -> None:
    try:
        send_otp_email(email, otp)
    except EmailDeliveryError as error:
        logger.warning("OTP email send failed for %s", email, exc_info=error)
        cleanup_db = SessionLocal()
        try:
            cleanup_db.execute(delete(OTPChallenge).where(OTPChallenge.email == email))
            cleanup_db.commit()
        finally:
            cleanup_db.close()


async def store_analysis_upload(file: UploadFile, allowed_kinds: set[str]) -> Path:
    payload = await file.read()
    try:
        destination, _ = process_upload(UPLOAD_DIR, file.filename or "upload.bin", payload, allowed_kinds)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        logger.exception("Upload processing failed")
        raise HTTPException(status_code=503, detail="Upload processing unavailable") from error
    return destination




def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=60 * 60 * 24 * 30,
        path="/",
    )


def format_http_error(exc: HTTPException) -> dict[str, object]:
    if isinstance(exc.detail, dict):
        return exc.detail
    if exc.status_code >= 500:
        return {"detail": "Internal server error"}
    return {"detail": str(exc.detail)}


def clean_response_text(value: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()
