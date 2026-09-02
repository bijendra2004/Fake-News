# SachLens — AI-Powered Fact-Checking & Claim Verification Platform

SachLens is an AI-powered fact-checking and claim verification platform designed to evaluate news and viral claims across text, images (OCR/vision), links, and voice notes. 

Rather than returning a simple binary fake/real label, SachLens utilizes live web search grounding (Tavily), fact-check lookups, and LLM reasoning with self-verification to produce a nuanced percentage confidence score, plain-language bulleted explanations, and verifiable cited sources.

**Live Demo:** [https://fake-news-bznu.vercel.app](https://fake-news-bznu.vercel.app)  
**Backend API:** [https://fake-news-cvzg.onrender.com](https://fake-news-cvzg.onrender.com)

## Features

- **Multi-Format Claim Verification:** Verify claims submitted via raw text, screenshots/images (multimodal vision + OCR), web/social links, and voice notes (Whisper transcription).
- **Source-Grounded Analysis:** Real-time web search grounding via Tavily API and Google Fact Check Tools integration.
- **Explainable Verdicts:** Generates percentage confidence scores, plain-language explanations, corrected facts, and direct web citations.
- **Secure Authentication:** Passwordless Email OTP with multi-provider failover (Brevo, Resend, SMTP) and Google One-Tap OAuth.
- **Token Security:** Short-lived JWT access tokens with rotating `HttpOnly` refresh tokens.
- **Enterprise-Grade Security:** AES-256-GCM database encryption at rest, sliding-window rate limiting, CSRF protection, and strict CSP/HSTS headers.

## Tech Stack

- **Frontend:** React + Vite, Tailwind CSS
- **Backend:** FastAPI + SQLAlchemy
- **Database:** PostgreSQL (Neon/Supabase/Render compatible), SQLite fallback
- **ML / AI / Media:** Groq (`qwen/qwen3.6-27b`), Google Gemini Vision (`gemini-3.5-flash`), scikit-learn, faster-whisper, pytesseract, Tavily Search API

## Architecture (high-level)

```text
React (Vite SPA on Vercel)
  -> HTTPS API calls (pre-warmed)
FastAPI backend (Render Web Service)
  -> Auth, CSRF, CORS, rate-limit, upload safety
  -> Prediction pipeline (shared for text/image/link/voice)
  -> Live web grounding (Tavily) + LLM reasoning (Groq / Gemini)
  -> SQLAlchemy ORM with AES-256-GCM field encryption
PostgreSQL Database
```

## Environment Variables

Copy `.env.example` to `.env` and set real values.

All sensitive values must come from environment variables only:
- `JWT_SECRET`
- `DATA_ENCRYPTION_KEY`
- `DATABASE_URL`
- `GROQ_API_KEY`
- `GEMINI_API_KEY`
- `TAVILY_API_KEY`
- `BREVO_API_KEY` / `RESEND_API_KEY` / `SMTP_*`
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`

Frontend API URL is environment-driven:
- `VITE_API_URL` (preferred)

Backend CORS allowlist is environment-driven:
- `FRONTEND_ORIGINS` (comma-separated)

## Local Development

### 1) Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

Still from repo root:

```bash
cp .env.example .env
```

Update `.env` with your local values, then:

```bash
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

### 2) Frontend

```bash
npm install
npm run dev
```

Set `VITE_API_URL` in `.env` so frontend points to backend.

## Database Setup / Migration

For a fresh hosted database (Neon/Supabase/any Postgres):

1. Set `DATABASE_URL` (include `?sslmode=require` when provider requires SSL).
2. Run:

```bash
./.venv/bin/python scripts/migrate_db.py
```

This creates all required tables via SQLAlchemy models.

## Deployment Notes

### Frontend (Vercel)
- Set env vars:
  - `VITE_API_URL=https://<your-backend-domain>`
  - `VITE_GOOGLE_CLIENT_ID=<your-google-client-id>`

### Backend (Render)
- Set env vars from `.env.example`
- Set `APP_ENV=production`
- Set `DATABASE_URL` to hosted Postgres URL
- Set `FRONTEND_ORIGINS` to deployed frontend URL(s)
- Keep `APP_DEBUG=false`

## Security / Git Hygiene

- `.env` is git-ignored.
- `node_modules/` and `__pycache__/` are git-ignored.
- Model binaries are git-ignored (`*.joblib`, `*.pkl`, `*.pt`, `*.onnx`).
