import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { neon } from '@neondatabase/serverless';
import { chromium } from 'playwright-core';

const PROTEAN_URL = 'https://onlineservices.proteantech.in/paam/endUserRegisterContact.html';
const PROTEAN_HOST = 'onlineservices.proteantech.in';
const PROTEAN_RESPONSE_PATH = '/paam/registerEndUser.html';
const SESSION_TIMEOUT_SECONDS = 900;
const browserbaseApi = 'https://api.browserbase.com/v1';
let schemaReady;

export class ApiError extends Error {
  constructor(status, detail, code) {
    super(detail);
    this.status = status;
    this.code = code;
  }
}

function requireConfig() {
  if (!process.env.DATABASE_URL) {
    throw new ApiError(503, 'Veriidia storage is not configured. No registration was started.', 'DATABASE_NOT_CONFIGURED');
  }
  if (!process.env.BROWSERBASE_API_KEY) {
    throw new ApiError(503, 'Browserbase is not connected to this deployment yet. No registration was started or submitted.', 'BROWSERBASE_NOT_CONFIGURED');
  }
}

async function getSql() {
  requireConfig();
  const sql = neon(process.env.DATABASE_URL);
  if (!schemaReady) {
    schemaReady = (async () => {
      await sql`CREATE TABLE IF NOT EXISTS veriidia_browser_sessions (
        id UUID PRIMARY KEY,
        token_sha256 CHAR(64) NOT NULL UNIQUE,
        client_sha256 CHAR(64) NOT NULL,
        browserbase_session_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL,
        fields JSONB NOT NULL DEFAULT '[]'::jsonb,
        fields_total SMALLINT NOT NULL DEFAULT 0,
        current_field TEXT,
        stage TEXT NOT NULL,
        error TEXT,
        captcha_detected BOOLEAN NOT NULL DEFAULT FALSE,
        actual_response JSONB,
        submission_started BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL,
        closed_at TIMESTAMPTZ
      )`;
      await sql`CREATE INDEX IF NOT EXISTS veriidia_browser_sessions_client_active_idx
        ON veriidia_browser_sessions (client_sha256, expires_at)
        WHERE closed_at IS NULL`;
      await sql`DELETE FROM veriidia_browser_sessions WHERE created_at < now() - interval '30 days'`;
    })().catch((error) => {
      schemaReady = undefined;
      throw error;
    });
  }
  await schemaReady;
  return sql;
}

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const bearer = (req) => {
  const header = req.headers?.authorization || '';
  return header.startsWith('Bearer ') ? header.slice(7).trim() : '';
};

async function getSession(sql, req) {
  const token = bearer(req);
  if (!token || token.length > 200) throw new ApiError(401, 'A registration session token is required.');
  const rows = await sql`SELECT * FROM veriidia_browser_sessions
    WHERE token_sha256 = ${sha256(token)} AND closed_at IS NULL AND expires_at > now()
    LIMIT 1`;
  if (!rows.length) throw new ApiError(401, 'This registration session has expired. Start again.');
  return { token, row: rows[0] };
}

async function persist(sql, row, patch = {}) {
  const next = { ...row, ...patch };
  await sql`UPDATE veriidia_browser_sessions SET
    status = ${next.status},
    fields = ${JSON.stringify(next.fields || [])}::jsonb,
    fields_total = ${next.fields_total || 0},
    current_field = ${next.current_field || null},
    stage = ${next.stage || ''},
    error = ${next.error || null},
    captcha_detected = ${Boolean(next.captcha_detected)},
    actual_response = ${next.actual_response ? JSON.stringify(next.actual_response) : null}::jsonb,
    submission_started = ${Boolean(next.submission_started)},
    updated_at = now(),
    closed_at = ${next.closed_at || null}
    WHERE id = ${next.id}`;
  Object.assign(row, next);
}

async function browserbaseRequest(path, method = 'GET', body) {
  const response = await fetch(`${browserbaseApi}${path}`, {
    method,
    headers: {
      'X-BB-API-Key': process.env.BROWSERBASE_API_KEY,
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
    signal: AbortSignal.timeout(20000),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const providerMessage = String(result.message || result.error || '').toLocaleLowerCase();
    const detail = providerMessage.includes('keepalive') || providerMessage.includes('keep alive')
      ? 'This registration flow requires a Browserbase plan with keep-alive sessions so the browser remains available between requests.'
      : response.status === 401 || response.status === 403
      ? 'Browserbase rejected its server-side API key. Check the Vercel BROWSERBASE_API_KEY setting.'
      : response.status === 402 || response.status === 429
        ? 'Browserbase cannot start this session because the account plan or browser capacity needs attention.'
        : 'Browserbase could not complete the browser request. Retry after checking the service status.';
    throw new ApiError(response.status === 401 || response.status === 403 ? 503 : 502, detail, 'BROWSERBASE_REQUEST_FAILED');
  }
  return result;
}

async function remoteSession(browserbaseId) {
  const session = await browserbaseRequest(`/sessions/${encodeURIComponent(browserbaseId)}`);
  if (!session.connectUrl || !['PENDING', 'RUNNING'].includes(session.status)) {
    throw new ApiError(410, 'The Protean browser session ended. Start a new registration.', 'BROWSER_SESSION_ENDED');
  }
  return session;
}

async function withBrowser(browserbaseId, action) {
  const session = await remoteSession(browserbaseId);
  const browser = await chromium.connectOverCDP(session.connectUrl, { timeout: 30000 });
  try {
    const context = browser.contexts()[0];
    if (!context) throw new ApiError(502, 'Browserbase did not return an active browser context.');
    const pages = context.pages();
    const page = pages[0] || await context.newPage();
    return await action({ browser, context, page, session });
  } finally {
    // Browserbase keepAlive preserves the remote session when this CDP client disconnects.
    await browser.close().catch(() => {});
  }
}

function assertInitialProteanPage(page, stage) {
  const url = new URL(page.url());
  if (url.hostname !== PROTEAN_HOST || url.pathname !== '/paam/endUserRegisterContact.html') {
    throw new ApiError(409, `Protean left the supported initial registration page during ${stage}. No further automated action was taken.`);
  }
}

function applicantDetails(input) {
  const keys = ['first_name', 'middle_name', 'last_name', 'date_of_birth', 'email', 'mobile'];
  if (!input || typeof input !== 'object' || Array.isArray(input) || Object.keys(input).some((key) => !keys.includes(key))) {
    throw new ApiError(422, 'The registration details do not match the expected fields.');
  }
  const details = Object.fromEntries(keys.map((key) => [key, String(input[key] ?? '').trim()]));
  if (!details.first_name || details.first_name.length > 25 || !details.last_name || details.last_name.length > 25 || details.middle_name.length > 25) {
    throw new ApiError(422, 'Check the applicant name fields; first and last name are required and each name must be 25 characters or fewer.');
  }
  if (!/^\d{2}\/\d{2}\/\d{4}$/.test(details.date_of_birth)) throw new ApiError(422, 'Date of birth must use DD/MM/YYYY.');
  const [day, month, year] = details.date_of_birth.split('/').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  const now = new Date();
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day || date > now || year < now.getUTCFullYear() - 125) {
    throw new ApiError(422, 'Enter a real date of birth that is not in the future and is within the accepted range.');
  }
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(details.email) || details.email.length > 40) throw new ApiError(422, 'Enter a valid email address of 40 characters or fewer.');
  if (!/^[1-9]\d{9}$/.test(details.mobile)) throw new ApiError(422, 'Enter a valid 10-digit Indian mobile number.');
  return details;
}

async function waitUntilResumed(sql, row) {
  const deadline = Date.now() + 240000;
  while (Date.now() < deadline) {
    const rows = await sql`SELECT status, closed_at, expires_at > now() AS alive
      FROM veriidia_browser_sessions WHERE id = ${row.id} LIMIT 1`;
    const state = rows[0];
    if (!state || state.closed_at || !state.alive) throw new ApiError(410, 'This browser session ended while field entry was paused. No registration was submitted.');
    if (state.status !== 'paused') return;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new ApiError(408, 'Field entry remained paused too long. Resume and start field entry again; no registration was submitted.');
}

async function selectDropdown(page, selector, optionLabel, expectedValue, waitForSelector) {
  const control = page.locator(selector);
  await control.waitFor({ state: 'attached', timeout: 15000 });
  const options = await control.locator('option').evaluateAll((items) => items.map((element, index) => ({
    index, value: element.value, label: element.textContent.trim(),
  })));
  const matches = options.filter((option) => option.label.toLocaleLowerCase() === optionLabel.toLocaleLowerCase() && (expectedValue === undefined || option.value === expectedValue));
  if (matches.length !== 1) throw new ApiError(502, `Protean did not provide one unambiguous ${optionLabel} option.`);
  const option = matches[0];
  const id = await control.getAttribute('id');
  const widget = id ? page.locator(`.select2-selection[aria-labelledby="select2-${id}-container"]`) : null;
  const useCustomWidget = Boolean(widget && await widget.count() === 1 && await widget.isVisible() && (await control.getAttribute('class') || '').includes('select2-hidden-accessible'));
  if (useCustomWidget) {
    await widget.click();
    const choice = page.getByRole('option', { name: optionLabel, exact: true });
    await choice.waitFor({ state: 'visible', timeout: 10000 });
    await choice.click();
  } else if (await control.isVisible() && !await control.isDisabled()) {
    await control.selectOption({ index: option.index });
  } else {
    throw new ApiError(502, `Protean’s ${optionLabel} dropdown has no visible interactive control.`);
  }
  await page.waitForFunction(({ selector: query, value }) => document.querySelector(query)?.value === value, { selector, value: option.value }, { timeout: 10000 });
  if (useCustomWidget) {
    const selected = page.locator(`#select2-${id}-container`);
    await selected.waitFor({ state: 'attached', timeout: 10000 });
    if ((await selected.innerText()).trim().toLocaleLowerCase() !== optionLabel.toLocaleLowerCase()) throw new ApiError(502, `Protean did not display the selected ${optionLabel} option.`);
  }
  if (waitForSelector) await page.locator(waitForSelector).waitFor({ state: 'visible', timeout: 15000 });
  return option;
}

async function typeVisible(page, selector, value, label) {
  const control = page.locator(selector);
  await control.waitFor({ state: 'visible', timeout: 15000 });
  if (await control.isDisabled()) throw new ApiError(502, `Protean’s ${label.toLocaleLowerCase()} control is disabled.`);
  await control.click();
  await control.press('Control+A');
  await control.press('Backspace');
  await control.pressSequentially(value);
  if (await control.inputValue() !== value) throw new ApiError(502, `Protean’s ${label.toLocaleLowerCase()} field did not pass read-back verification.`);
  const valid = await control.evaluate((element) => element.checkValidity());
  if (!valid) {
    const message = await control.evaluate((element) => element.validationMessage);
    throw new ApiError(422, `Protean marked ${label.toLocaleLowerCase()} invalid: ${message || 'check the field'}.`);
  }
}

async function beginSession(req) {
  requireConfig();
  const sql = await getSql();
  const forwarded = String(req.headers?.['x-forwarded-for'] || '').split(',')[0].trim();
  const clientHash = sha256(`${process.env.BROWSERBASE_API_KEY}:${forwarded || 'unknown-client'}`);
  const active = await sql`SELECT id FROM veriidia_browser_sessions
    WHERE client_sha256 = ${clientHash} AND closed_at IS NULL AND expires_at > now()
    LIMIT 1`;
  if (active.length) throw new ApiError(409, 'A browser session is already active for this connection. Finish or close it before starting another.');

  const token = randomBytes(32).toString('base64url');
  const recordId = randomUUID();
  const browserSession = await browserbaseRequest('/sessions', 'POST', {
    timeout: SESSION_TIMEOUT_SECONDS,
    keepAlive: true,
    region: 'ap-southeast-1',
    browserSettings: {
      viewport: { width: 1365, height: 900 },
      recordSession: false,
      logSession: false,
      solveCaptchas: false,
      advancedStealth: false,
      allowedDomains: [
        PROTEAN_HOST,
        'www.google.com',
        'www.recaptcha.net',
        'www.gstatic.com',
        'd1axzsitviir9s.cloudfront.net',
      ],
    },
  });
  if (!browserSession.id || !browserSession.connectUrl) throw new ApiError(502, 'Browserbase did not return a usable browser session.');

  try {
    await withBrowser(browserSession.id, async ({ page }) => {
      await page.goto(PROTEAN_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
      assertInitialProteanPage(page, 'opening the official registration page');
    });
    const liveView = await browserbaseRequest(`/sessions/${encodeURIComponent(browserSession.id)}/debug?expiresIn=${SESSION_TIMEOUT_SECONDS}`);
    const expiresAt = browserSession.expiresAt || new Date(Date.now() + SESSION_TIMEOUT_SECONDS * 1000).toISOString();
    await sql`INSERT INTO veriidia_browser_sessions (
      id, token_sha256, client_sha256, browserbase_session_id, status, stage, expires_at
    ) VALUES (
      ${recordId}, ${sha256(token)}, ${clientHash}, ${browserSession.id}, 'ready_to_fill',
      'waiting for user details', ${expiresAt}
    )`;
    return {
      status: 'ready_to_fill',
      fields: [],
      captchaDetected: false,
      automatedSubmissionPermitted: false,
      providerUrl: PROTEAN_URL,
      viewer: liveView.debuggerFullscreenUrl,
      token,
      notice: 'The official Protean registration page is open in the live browser. Review it, then start the controlled field entry.',
    };
  } catch (error) {
    await browserbaseRequest(`/sessions/${encodeURIComponent(browserSession.id)}`, 'POST', { status: 'REQUEST_RELEASE' }).catch(() => {});
    throw error;
  }
}

async function fillRegistration(req) {
  const details = applicantDetails(req.body);
  const sql = await getSql();
  const { row } = await getSession(sql, req);
  if (row.status !== 'ready_to_fill') throw new ApiError(409, 'This session is not ready to begin field entry.');
  const state = { ...row, fields: [], current_field: null, error: null, status: 'selecting_application_type', stage: 'selecting the Form 93 application type' };
  await persist(sql, row, state);

  try {
    await withBrowser(row.browserbase_session_id, async ({ page }) => {
      assertInitialProteanPage(page, 'checking the loaded registration page');
      const typeOptions = await page.locator('#type option').evaluateAll((items) => items.map((element, index) => ({ index, value: element.value, label: element.textContent.trim() })));
      const forms = typeOptions.filter((option) => option.label.toLocaleLowerCase().includes('form no. 93') && option.label.toLocaleLowerCase().includes('indian citizen'));
      if (forms.length !== 1) throw new ApiError(502, 'Protean’s New PAN Form 93 (Indian Citizen) option is unavailable or ambiguous.');
      await waitUntilResumed(sql, row);
      await selectDropdown(page, '#type', forms[0].label, forms[0].value, '#f_name_end');
      state.fields.push({ field: 'Application type', status: 'verified' });
      state.status = 'checking_applicant_category';
      state.stage = 'checking the applicant category control';
      await persist(sql, row, state);

      const category = page.locator('#cat_applicant1');
      if (await category.count() !== 1) throw new ApiError(502, 'Protean’s applicant category control is missing from the live form.');
      const categories = await category.locator('option').evaluateAll((items) => items.map((element) => ({ value: element.value, label: element.textContent.trim() })));
      const individual = categories.filter((option) => option.value === 'P' && option.label.toLocaleLowerCase() === 'individual');
      if (individual.length !== 1) throw new ApiError(502, 'Protean’s live category options do not include INDIVIDUAL.');
      if (await page.locator('#categoryContainer').isVisible()) {
        state.stage = 'selecting the visible INDIVIDUAL category';
        await persist(sql, row, state);
        await waitUntilResumed(sql, row);
        await selectDropdown(page, '#cat_applicant1', individual[0].label, individual[0].value, '#f_name_end');
        state.fields.push({ field: 'Applicant category', status: 'verified' });
      } else if (forms[0].label.toLocaleLowerCase().includes('indian citizen')) {
        state.fields.push({ field: 'Applicant category', status: 'not required by provider for Form 93' });
      } else {
        throw new ApiError(502, 'Protean hid applicant category outside the supported Form 93 Indian citizen route.');
      }

      state.status = 'filling_provider_fields';
      const mapping = [
        ['#f_name_end', details.first_name, 'First name'],
        ['#m_name_end', details.middle_name, 'Middle name'],
        ['#l_name_end', details.last_name, 'Last name'],
        ['#date_of_birth_reg', details.date_of_birth, 'Date of birth'],
        ['#email_id2', details.email, 'Email address'],
        ['#rvContactNo', details.mobile, 'Mobile number'],
      ];
      const citizen = page.locator('#citizenCr1');
      const citizenVisible = await citizen.count() === 1 && await citizen.isVisible();
      state.fields_total = mapping.length + state.fields.length + Number(citizenVisible);
      for (const [selector, value, label] of mapping) {
        await waitUntilResumed(sql, row);
        assertInitialProteanPage(page, `filling ${label.toLocaleLowerCase()}`);
        state.current_field = label;
        state.stage = `filling ${label.toLocaleLowerCase()}`;
        await persist(sql, row, state);
        await typeVisible(page, selector, value, label);
        state.fields.push({ field: label, status: 'verified' });
        await persist(sql, row, state);
      }
      if (citizenVisible) {
        await waitUntilResumed(sql, row);
        await citizen.check();
        if (!await citizen.isChecked()) throw new ApiError(502, 'Protean’s Indian-citizen option could not be verified.');
        state.fields.push({ field: 'Indian citizenship', status: 'verified' });
      }
      state.captcha_detected = await page.locator('textarea[name="g-recaptcha-response"]').count() > 0 || await page.locator('iframe[src*="recaptcha"]').count() > 0;
      state.current_field = null;
      state.status = 'awaiting_user_captcha_and_consent';
      state.stage = 'waiting for user CAPTCHA and consent';
      await persist(sql, row, state);
    });
    return { status: state.status, fieldsTotal: state.fields_total };
  } catch (error) {
    const detail = error instanceof ApiError ? error.message : 'Protean field entry could not continue safely. Review the visible page and continue manually.';
    await persist(sql, row, { ...state, status: 'fill_error', current_field: null, error: detail, stage: 'stopped during field entry' });
    return { status: 'fill_error', error: detail };
  } finally {
    // Applicant data is held only in this request while it is typed into Protean.
    for (const key of Object.keys(details)) details[key] = '';
  }
}

async function status(req) {
  const sql = await getSql();
  const { row } = await getSession(sql, req);
  let providerUrl = PROTEAN_URL;
  let actualResponse = row.actual_response;
  if (!actualResponse && ['awaiting_user_captcha_and_consent', 'manual_submission_required', 'ready_for_explicit_submission_approval'].includes(row.status)) {
    try {
      const observed = await withBrowser(row.browserbase_session_id, async ({ page }) => {
        const url = page.url();
        const parsed = new URL(url);
        if (parsed.hostname === PROTEAN_HOST && parsed.pathname === PROTEAN_RESPONSE_PATH) {
          const text = (await page.locator('body').innerText({ timeout: 5000 })).trim().slice(0, 12000);
          const responseStatus = await page.evaluate(() => performance.getEntriesByType('navigation')[0]?.responseStatus || null).catch(() => null);
          return { providerUrl: url, actualResponse: { httpStatus: responseStatus, url, text } };
        }
        if (parsed.hostname !== PROTEAN_HOST) throw new ApiError(409, 'The visible browser left Protean. No further automated action was taken.');
        return { providerUrl: url, actualResponse: null };
      });
      providerUrl = observed.providerUrl;
      if (observed.actualResponse) {
        actualResponse = observed.actualResponse;
        await persist(sql, row, { ...row, status: 'provider_response_received', actual_response: actualResponse, stage: 'initial provider response received' });
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await persist(sql, row, { ...row, status: 'provider_blocked', error: error.message, stage: 'provider page changed' });
      }
    }
  }
  return {
    status: row.status,
    fields: row.fields || [],
    currentField: row.current_field,
    fieldsDone: (row.fields || []).filter((item) => ['verified', 'not required by provider for Form 93'].includes(item.status)).length,
    fieldsTotal: row.fields_total,
    error: row.error,
    captchaDetected: row.captcha_detected,
    automatedSubmissionPermitted: false,
    providerUrl,
    actualResponse,
  };
}

async function captchaComplete(req) {
  const sql = await getSql();
  const { row } = await getSession(sql, req);
  if (row.status !== 'awaiting_user_captcha_and_consent') throw new ApiError(409, 'This session is not waiting for CAPTCHA and consent.');
  const verification = await withBrowser(row.browserbase_session_id, async ({ page }) => {
    assertInitialProteanPage(page, 'checking CAPTCHA completion');
    const captcha = page.locator('textarea[name="g-recaptcha-response"]');
    const captchaPresent = await captcha.count() === 1 && Boolean((await captcha.inputValue()).trim());
    const consent = page.locator('#consent');
    const consentChecked = await consent.count() === 1 && await consent.isChecked();
    return { captchaPresent, consentChecked };
  }).catch((error) => {
    if (error instanceof ApiError) throw error;
    throw new ApiError(502, 'Could not inspect CAPTCHA and consent in the Protean browser.');
  });
  if (!verification.captchaPresent) throw new ApiError(422, 'Protean has not exposed a completed CAPTCHA response. Solve it in the live browser first.');
  if (!verification.consentChecked) throw new ApiError(422, 'Review and check Protean’s own consent checkbox in the live browser first.');
  await persist(sql, row, { ...row, status: 'manual_submission_required', stage: 'waiting for user-controlled Protean submission' });
  return {
    status: 'manual_submission_required',
    automatedSubmissionPermitted: false,
    message: 'Protean CAPTCHA response is present and consent is checked. Provider permission for automated submission is unverified; use Protean’s own Submit control if you choose.',
  };
}

async function pause(req) {
  const sql = await getSql();
  const { row } = await getSession(sql, req);
  if (!['filling_provider_fields', 'selecting_application_type', 'checking_applicant_category'].includes(row.status)) {
    throw new ApiError(409, 'There is no active field entry to pause.');
  }
  await persist(sql, row, { ...row, status: 'paused', stage: `paused during ${row.stage}` });
  return { status: 'paused', currentField: row.current_field };
}

async function resume(req) {
  const sql = await getSql();
  const { row } = await getSession(sql, req);
  if (row.status !== 'paused') throw new ApiError(409, 'The field entry is not paused.');
  await persist(sql, row, { ...row, status: 'filling_provider_fields', stage: 'resuming field entry' });
  return { status: 'filling_provider_fields' };
}

async function closeByToken(token) {
  if (!token || !process.env.DATABASE_URL || !process.env.BROWSERBASE_API_KEY) return { status: 'already closed' };
  const sql = await getSql();
  const rows = await sql`SELECT * FROM veriidia_browser_sessions
    WHERE token_sha256 = ${sha256(token)} AND closed_at IS NULL LIMIT 1`;
  if (!rows.length) return { status: 'already closed' };
  const row = rows[0];
  await persist(sql, row, { ...row, status: 'closed', stage: 'closed by user', closed_at: new Date().toISOString() });
  await browserbaseRequest(`/sessions/${encodeURIComponent(row.browserbase_session_id)}`, 'POST', { status: 'REQUEST_RELEASE' }).catch(() => {});
  return { status: 'closed' };
}

export async function closeSession(req) {
  const sql = await getSql();
  const { row, token } = await getSession(sql, req);
  await persist(sql, row, { ...row, status: 'closed', stage: 'closed by user', closed_at: new Date().toISOString() });
  await browserbaseRequest(`/sessions/${encodeURIComponent(row.browserbase_session_id)}`, 'POST', { status: 'REQUEST_RELEASE' }).catch(() => {});
  return { status: 'closed' };
}

export async function closeSessionBeacon(req) {
  let body = req.body;
  if (typeof body === 'string') {
    try { body = JSON.parse(body); } catch { body = {}; }
  }
  if (!body?.token) return { status: 'already closed' };
  return closeByToken(String(body.token));
}

export async function handleRegistration(action, req) {
  if (action === 'start') {
    if (req.method !== 'POST') throw new ApiError(405, 'Method not allowed.');
    return beginSession(req);
  }
  if (action === 'fill') {
    if (req.method !== 'POST') throw new ApiError(405, 'Method not allowed.');
    return fillRegistration(req);
  }
  if (action === 'status') {
    if (req.method !== 'GET') throw new ApiError(405, 'Method not allowed.');
    return status(req);
  }
  if (action === 'captcha-complete') {
    if (req.method !== 'POST') throw new ApiError(405, 'Method not allowed.');
    return captchaComplete(req);
  }
  if (action === 'pause') {
    if (req.method !== 'POST') throw new ApiError(405, 'Method not allowed.');
    return pause(req);
  }
  if (action === 'resume') {
    if (req.method !== 'POST') throw new ApiError(405, 'Method not allowed.');
    return resume(req);
  }
  if (action === 'submit') {
    throw new ApiError(403, 'Automated submission is disabled. Review the completed fields and use Protean’s own Submit control if you choose.');
  }
  if (action === 'session') {
    if (req.method !== 'DELETE') throw new ApiError(405, 'Method not allowed.');
    return closeSession(req);
  }
  throw new ApiError(404, 'Registration API route not found.');
}
