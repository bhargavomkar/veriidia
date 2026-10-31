# Veriidia PAN registration

This app has a static HTML/CSS/JavaScript frontend, Vercel serverless API functions, Neon PostgreSQL, and Browserbase-hosted Playwright sessions for the live Protean flow. The API reconnects to the same remote browser session for each step; Vercel does not keep browser state in function memory.

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

Use `pan-app` as the Vercel project root, the Other framework preset, `npm run build`, and `dist` as the output directory. Set `DATABASE_URL` and `BROWSERBASE_API_KEY` in Vercel as server-side secrets. `/api/health` checks Neon and reports whether Browserbase is configured without returning credentials.

Create a Browserbase account, choose a plan that supports keep-alive sessions, and add its API key to Vercel as `BROWSERBASE_API_KEY`. The functions create a 15-minute session in the Singapore region, explicitly disable Browserbase logs and recordings, and leave CAPTCHA solving and automated submission disabled. The user controls the embedded live view and submits directly on Protean if they choose. Keep-alive is needed because each browser step runs in a separate Vercel function.

No Browserbase API key is stored in the repository or sent to the browser. Applicant values are held only in the fill request while they are typed; the database stores a hashed session token, Browserbase session ID, field verification statuses, and provider response metadata. The Browserbase session itself is configured not to retain logs or video recordings.

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
