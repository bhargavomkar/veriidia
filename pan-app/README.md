# Veriidia PAN registration

This app has a static HTML/CSS/JavaScript frontend, Vercel serverless API functions, and a FastAPI/Playwright browser service for the live Protean flow. The Vercel API currently provides a Neon database health check and an explicit unavailable response for registration until a persistent browser worker is configured.

## Local run

Use Docker Compose v2 from this directory:

~~~powershell
docker compose up --build
~~~

Open `http://127.0.0.1:8000`. For a direct Python run, install `backend/requirements.txt`, set the pooled Neon `DATABASE_URL` in an ignored `.env`, install Chromium with `python -m playwright install chromium`, and run:

~~~powershell
uvicorn backend.app:app --host 127.0.0.1 --port 8000
~~~

The local FastAPI service can launch a visible Chromium session, show it through noVNC, pause for the user-controlled CAPTCHA, and stop before later PAN application stages. Automated submission remains disabled.

## Vercel deployment

Use `pan-app` as the Vercel project root, the Other framework preset, `npm run build`, and `dist` as the output directory. Set `DATABASE_URL` in Vercel as a server-side secret. `/api/health` checks that Neon is reachable and returns no connection details.

The browser workflow is not available on this Vercel-only deployment. It needs one persistent Playwright/Chromium worker that keeps the live Protean page and session available between API requests and serves the interactive browser viewer. Vercel Functions have bounded lifetimes and may route separate requests to different instances, so the current in-memory session and viewer cannot safely be moved into stateless functions. Until a persistent browser worker is configured, `/api/registration/start` returns an explicit error and does not open Protean or claim a registration started.

If this deployment is configured from the repository root instead, the root `vercel.json` builds and publishes `pan-app/dist`. Ensure the API functions in `pan-app/api` are included in the configured project root; prefer setting the project root to `pan-app`.

## Provider scope and data

- Only the official Protean initial Form 93 registration page is in scope. CAPTCHA and provider consent remain user controlled. Veriidia does not automate later PAN stages.
- The backend records PII-free session metadata in Neon. Applicant values, CAPTCHA data, page contents, and uploaded file bytes are not stored.
- The Form 93 page has no document upload control. The optional local file picker keeps names/references only in the current browser tab.
- Do not claim a successful registration unless Protean returns its actual response. If automation is blocked, stop and continue manually on Protean.

## Build and checks

From this directory:

~~~powershell
npm run build
python -m compileall -q backend
python -m unittest backend.test_app -v
~~~

The live Protean field-fill/CAPTCHA handoff has been exercised locally. The Vercel-only registration endpoint intentionally does not start the provider workflow until a persistent browser worker is configured.
