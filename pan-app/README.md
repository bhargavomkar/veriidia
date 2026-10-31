# Veriidia PAN registration

Veriidia's PAN application is a plain HTML/CSS/JavaScript frontend with a FastAPI backend and a visible Playwright Chromium session. The browser session is long-running and interactive, so it must run on a persistent container host. Vercel serves only the frontend.

The workflow targets only the official Protean initial Form 93 registration page. CAPTCHA and provider consent stay user controlled. Automated submission remains disabled unless Protean's service-specific permission is verified. The application does not automate later PAN stages.

## Local run

Use Docker Compose v2 from this directory:

~~~powershell
docker compose up --build
~~~

Open `http://127.0.0.1:8000`. The local gateway routes the app API and the authenticated noVNC viewer to the backend container.

Without Docker, install `backend/requirements.txt`, set the pooled Neon `DATABASE_URL` in a local ignored `.env` file, install Chromium with `python -m playwright install chromium`, and run:

~~~powershell
uvicorn backend.app:app --host 127.0.0.1 --port 8000
~~~

## Vercel frontend

Create a separate Vercel project for this repository and use the repository root as its **Root Directory**. Use the Other framework preset. The root `vercel.json` explicitly runs `npm --prefix pan-app run build` and publishes `pan-app/dist`, preventing Vercel from trying to run the Playwright backend as a Vercel Function. If you set the Vercel Root Directory to `pan-app` instead, its `vercel.json` runs `npm run build` and publishes `dist`.

Set this Vercel environment variable for Production and Preview as appropriate:

| Name | Value | Secret? |
| --- | --- | --- |
| `VERIIDIA_API_BASE_URL` | `https://veriidia-pan-backend.onrender.com` | No. This is a public API origin embedded in the static build. |

Set the variable to the actual Render HTTPS origin. If it is absent, the build uses the hostname planned by the Render Blueprint (`https://veriidia-pan-backend.onrender.com`); override it if Render assigns a different hostname. The build emits a restrictive content security policy for the configured API and viewer origins.

## Persistent backend on Render

The backend uses Docker, Xvfb, Chromium, noVNC, websockify, and Nginx in one service. Deploy the Blueprint at `pan-app/render.yaml` from the repository root. Choose a paid always-on web service with enough memory for Chromium. Render's health check is `/health`; `/api/health` remains available as a compatibility route. HTTP and WebSocket viewer traffic share port `10000`.

Configure these Render environment variables:

| Name | Value |
| --- | --- |
| `APP_ENV` | `production` (set by the Blueprint) |
| `VERIIDIA_CORS_ORIGINS` | Exact HTTPS Vercel deployment origins, comma separated; no wildcard and no trailing slash. The Blueprint includes `https://veriidiaprototype.vercel.app`; update it if the production domain differs. |
| `DATABASE_URL` | Neon pooled PostgreSQL connection string with `sslmode=require` (secret; set in Render only). |
| `PROTEAN_AUTOMATED_SUBMISSION_PERMITTED` | `false` (set by the Blueprint; leave false until provider-specific permission is verified) |
| `REGISTRATION_SESSION_IDLE_TIMEOUT_SECONDS` | Optional; defaults to 900 seconds. |

The backend refuses to start in production when the trusted origin list is empty, wildcarded, or contains a non-HTTPS origin. `/health` performs a live `SELECT 1` connectivity check and reports only a database status, never connection details. Configure the chosen Vercel hostname in Render and the Render hostname in Vercel, then deploy both services.

## Sessions, documents, database, and security

- The backend uses a random bearer token for each browser session, checks the configured Vercel origin on state-changing requests, and expires an idle session after 15 minutes. Only one active Playwright session is supported per backend instance. Do not scale this service horizontally without moving session routing/state to a design that preserves browser affinity.
- The noVNC page and WebSocket require a session-scoped viewer ticket that becomes invalid when that browser session closes. Nginx access logging is disabled for ticket-bearing viewer and WebSocket requests. The app uses HTTPS for its public origins; Render terminates TLS.
- Neon PostgreSQL stores operational session metadata only: a hash of the random session token, lifecycle status, timestamps, field verification count, and provider HTTP status. It does not store applicant details, CAPTCHA values, browser contents, or document bytes. Records older than 30 days are removed at backend startup. The live Chromium session remains in backend memory and is closed on idle timeout or service restart.
- The current Form 93 first page has no document upload control. The optional file picker stores names/references only in the current browser tab; it does not upload, inspect, or retain document bytes. There is no object-storage integration because this flow does not upload documents.
- The backend retains form values only in the request/task memory while typing them into Protean, and does not log values. It does not persist session or applicant data to disk.
- Origin checks and random session tokens protect browser flows but do not provide user identity or prevent a non-browser client from making a direct request. Add real user authentication, per-user authorization, abuse controls, and privacy/retention review before offering this to the public. Treat this deployment as a restricted pilot, not a general public launch.
- The only permitted top-level provider documents are Protean's initial registration and its first response. CAPTCHA is never bypassed. If the provider blocks automation, stop and continue directly on Protean.

## Tests and build

From `pan-app`:

~~~powershell
npm run build
python -m compileall -q backend
python -m unittest backend.test_app -v
~~~

The live Protean field-fill/CAPTCHA handoff has been exercised locally. Automated submission, CAPTCHA acceptance, public Vercel-to-backend connectivity, and a deployed browser session have not been tested by that local run.

## Deployment checklist

1. Choose the Vercel frontend hostname and create the persistent Render backend from the Blueprint.
2. Set the exact Vercel origin in Render's `VERIIDIA_CORS_ORIGINS` and deploy the backend.
3. Set the backend HTTPS origin as Vercel's `VERIIDIA_API_BASE_URL` and deploy the frontend.
4. Open `/api/health` on the backend and `/registration` on Vercel. Confirm Vercel's browser console has no CORS or mixed-content errors.
5. Run one controlled test through field verification to CAPTCHA handoff, then close the session. Do not solve a CAPTCHA or submit test data unless authorized and appropriate with Protean.
6. Keep the marketing website `https://veriidia.vercel.app/` in its existing Vercel project. Deploy this application as a separate Vercel project.
