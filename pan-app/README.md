# Veriidia PAN registration

This local application assists with the **initial Protean New PAN Form 93 (Indian Citizen)** registration page. It does not imitate Protean and does not automate later PAN application stages.

## Run locally

Use Docker Compose v2 from this directory:

~~~powershell
docker compose up --build
~~~

Open `http://127.0.0.1:8000`. In Docker, a visible Playwright Chromium session is shown through the local noVNC viewer. Without Docker, run the FastAPI app directly and Chromium opens in a separate desktop window:

~~~powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
python -m playwright install chromium
uvicorn backend.app:app --host 127.0.0.1 --port 8000
~~~

The app opens only the exact Protean registration page and its first registration response. Its request allowlist includes Protean page resources and CAPTCHA resources. It blocks other navigation destinations and later top-level PAN stages.

## Registration workflow

1. Enter the initial registration details in Veriidia and review them.
2. Choose **Start Registration**. Playwright opens the real Protean page, selects Form 93 through its visible control, and fills fields that Protean currently exposes. Veriidia checks the values by reading them back.
3. Solve CAPTCHA and review/check consent yourself in the visible Protean browser. CAPTCHA is never solved or bypassed by Veriidia.
4. Provider-specific terms for automated submission could not be verified. Automated submission is therefore disabled by default. Use Protean's own Submit control if you choose; Veriidia can display the actual initial response when the provider returns it in the first response page.

The optional supporting-document picker keeps selected file references in the current page only. It does not upload files, inspect contents, run OCR, or transfer documents. The initial Protean registration page has no document upload field; provide documents directly to Protean only if a later step requests them.

Automated submit can only be enabled after Protean's service-specific permission has been verified. The Docker Compose variable `PROTEAN_AUTOMATED_SUBMISSION_PERMITTED` defaults to `false`. Do not set it to `true` unless that permission has been established. A separate in-app confirmation remains required when enabled.

## Data and session handling

Applicant values are sent to the local backend only when Start Registration is chosen, so Playwright can enter them on Protean. The backend does not persist form values or log them. Selected files stay in page memory and are not read. Use **Finish and clear this session** to clear the page and close the browser. Closing the app also tears down the Playwright session.

This is a local, single-user prototype, not a production service. It has no database, account system, or persistent profile store. Do not expose the Docker Compose gateway to a public network. Production use would require authenticated access, TLS, per-user browser isolation, retention and privacy review, and verified provider permission.

## Tests

Run backend and route tests with:

~~~powershell
pip install -r backend\requirements-test.txt
python -m unittest backend.test_app -v
~~~

The live registration flow has been exercised against Protean through field verification and CAPTCHA handoff, without solving CAPTCHA or submitting a registration. Automated submission and CAPTCHA acceptance were not tested. Provider pages and terms can change; use Protean directly if it blocks automation or displays a restriction.

## Official pages

- [Protean PAN registration](https://onlineservices.proteantech.in/paam/endUserRegisterContact.html)
- [Protean Form 93 instructions](https://tinpan.proteantech.in/services/pan/instructions-form_93-form_94.html)
- [Protean privacy policy](https://tinpan.proteantech.in/privacy-policy)
