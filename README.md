# SachLens

AI-assisted verification tool for claims from text, images (OCR), links, and voice notes with a login-first workflow.

**Live Demo:** [link]  
**Screenshots:** add after deployment

## Features

- OTP + Google sign-in
- Text claim analysis
- Image analysis via OCR + prediction
- Link content extraction + prediction
- Voice transcription + prediction
- Security controls: CSRF, CORS allowlist, rate limits, upload validation, metadata stripping

## Tech Stack

- **Frontend:** React + Vite
- **Backend:** FastAPI + SQLAlchemy
- **Database:** PostgreSQL (Neon/Supabase compatible), SQLite for local fallback
- **ML/OCR/Media:** scikit-learn, pytesseract, faster-whisper

## Architecture (high-level)

```text
React (Vite)
  -> HTTPS API calls
FastAPI backend
  -> Auth, CSRF, CORS, rate-limit, upload safety
  -> Prediction pipeline (shared for text/image/link/voice)
  -> SQLAlchemy ORM
PostgreSQL (Neon/Supabase)
```

## Environment Variables

Copy `.env.example` to `.env` and set real values.

All sensitive values must come from environment variables only:
- `JWT_SECRET`
- `DATA_ENCRYPTION_KEY`
- `DATABASE_URL`
- `SMTP_*`
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
- `GEMINI_API_KEY` (if used)
- `FACT_CHECK_API_KEY` (if used)

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

### Frontend (Vercel/Netlify)
- Set env vars:
  - `VITE_API_URL=https://<your-backend-domain>`
  - `VITE_GOOGLE_CLIENT_ID=<your-google-client-id>`
  - CAPTCHA vars if used

### Backend (Render/Railway)
- Set env vars from `.env.example`
- Set `APP_ENV=production`
- Set `DATABASE_URL` to hosted Postgres URL
- Set `FRONTEND_ORIGINS` to deployed frontend URL(s)
- Keep `APP_DEBUG=false`

### Database (Neon/Supabase)
- Provision Postgres
- Copy connection string into `DATABASE_URL`
- Ensure SSL mode (`sslmode=require`) when needed

## Google OAuth Manual Production Step (Required)

After frontend deployment, you must manually update your Google Cloud OAuth client:

- Add production frontend URL to **Authorized JavaScript origins**
- Add production callback/redirect URL(s) to **Authorized redirect URIs**

This is a manual Google Cloud Console step and is not automated by this repo.

## Operational Note (Free Tiers)

Free-tier backend hosts (Render/Railway) can cold-sleep after inactivity.  
The first request after idle can be slower. This is expected behavior, not a bug.

## Security / Git Hygiene

- `.env` is git-ignored.
- `node_modules/` and `__pycache__/` are git-ignored.
- Model binaries are git-ignored (`*.joblib`, `*.pkl`, `*.pt`, `*.onnx`).
- If you later need to store model files over ~50MB, use Git LFS or a separate model download/build step in CI/deploy.

