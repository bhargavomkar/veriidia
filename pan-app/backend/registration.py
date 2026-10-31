"""Visible, one-step Protean Form 93 registration assistance.

Only the official first registration page and its required static/CAPTCHA
resources are reachable. The provider consent and CAPTCHA remain user actions.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

PROTEAN_URL = "https://onlineservices.proteantech.in/paam/endUserRegisterContact.html"
PROTEAN_RESPONSE_PATH = "/paam/registerEndUser.html"
PROTEAN_HOST = "onlineservices.proteantech.in"
AUTOMATED_SUBMISSION_PERMITTED = os.getenv("PROTEAN_AUTOMATED_SUBMISSION_PERMITTED", "false").casefold() in {"1", "true", "yes"}
router = APIRouter(prefix="/api/registration", tags=["registration"])
logger = logging.getLogger("veriidia.registration")


class ApplicantDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    first_name: str = Field(min_length=1, max_length=25)
    middle_name: str = Field(default="", max_length=25)
    last_name: str = Field(min_length=1, max_length=25)
    date_of_birth: str = Field(pattern=r"^\d{2}/\d{2}/\d{4}$")
    email: str = Field(min_length=3, max_length=40)
    mobile: str = Field(pattern=r"^[1-9]\d{9}$")

    @field_validator("date_of_birth")
    @classmethod
    def valid_dob(cls, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%d/%m/%Y").date()
        except ValueError as exc:
            raise ValueError("Enter a real date in DD/MM/YYYY format.") from exc
        today = date.today()
        if parsed > today or parsed < date(today.year - 125, today.month, min(today.day, 28)):
            raise ValueError("Date of birth is outside the accepted range.")
        return value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value


@dataclass
class RegistrationSession:
    token: str
    playwright: Playwright
    browser: Browser
    context: BrowserContext
    page: Page
    status: str
    results: list[dict]
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    fill_task: asyncio.Task | None = None
    current_field: str | None = None
    fields_total: int = 0
    error: str | None = None
    actual_response: dict | None = None
    submission_started: bool = False
    captcha_detected: bool = False
    provider_response_status: int | None = None
    closed: bool = False
    last_activity: float = 0.0
    stage: str = "opening provider page"
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    close_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


active: RegistrationSession | None = None
lock = asyncio.Lock()


async def _close(state: RegistrationSession) -> None:
    global active
    async with state.operation_lock:
        async with state.close_lock:
            if state.closed:
                return
            state.closed = True
            if state.fill_task and not state.fill_task.done():
                state.fill_task.cancel()
                try:
                    await state.fill_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.warning("Field-entry task stopped during browser cleanup")
            try:
                await state.context.close()
            except Exception:
                logger.warning("Browser context cleanup failed")
            try:
                if state.browser.is_connected():
                    await state.browser.close()
            except Exception:
                logger.warning("Browser cleanup failed")
            try:
                await state.playwright.stop()
            except Exception:
                logger.warning("Playwright cleanup failed")
            finally:
                logger.info("Browser closed")
                if active is state:
                    active = None


async def shutdown() -> None:
    state = active
    if state and not state.closed:
        await _close(state)


async def _get(authorization: str | None) -> RegistrationSession:
    state = active
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="A registration session token is required.")
    if not state or state.closed or not secrets.compare_digest(state.token, authorization[7:].strip()):
        raise HTTPException(status_code=401, detail="This registration session has expired.")
    state.last_activity = time.monotonic()
    return state


def _allowed(url: str, resource_type: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme in ("data", "blob", "about"):
        return True
    if parsed.scheme != "https":
        return False
    host, path = parsed.hostname or "", parsed.path
    if host == PROTEAN_HOST:
        if resource_type == "document":
            return url.split("#", 1)[0].split("?", 1)[0] in (PROTEAN_URL, "https://" + PROTEAN_HOST + PROTEAN_RESPONSE_PATH)
        return True
    if host in ("www.google.com", "www.recaptcha.net"):
        return path.startswith("/recaptcha/")
    if host == "www.gstatic.com":
        return path.startswith("/recaptcha/")
    if host == "d1axzsitviir9s.cloudfront.net":
        return path.startswith("/proteantech/")
    return False


async def _route_guard(route) -> None:
    request = route.request
    if _allowed(request.url, request.resource_type):
        await route.continue_()
    else:
        await route.abort()


async def ensure_browser_open(page: Page, stage: str) -> None:
    if page.is_closed():
        raise RuntimeError(f"The visible browser page closed during {stage}.")
    browser = page.context.browser
    if not browser or not browser.is_connected():
        raise RuntimeError(f"The visible browser context closed during {stage}.")
    parsed = urlparse(page.url)
    if parsed.hostname and (parsed.hostname != PROTEAN_HOST or parsed.path != "/paam/endUserRegisterContact.html"):
        raise RuntimeError(f"Protean navigated away from the permitted initial registration page during {stage}.")


async def select_dropdown(
    page: Page,
    selector: str,
    option_label: str,
    *,
    expected_value: str | None = None,
    wait_for_selector: str | None = None,
) -> dict:
    """Select a live provider option through its visible widget and verify native state."""
    await ensure_browser_open(page, f"selecting {option_label}")
    control = page.locator(selector)
    await control.wait_for(state="attached", timeout=15000)
    option_rows = await control.locator("option").evaluate_all(
        "els => els.map((e,index) => ({index,value:e.value,label:e.textContent.trim()}))"
    )
    matches = [row for row in option_rows if row["label"].casefold() == option_label.casefold()]
    if expected_value is not None:
        matches = [row for row in matches if row["value"] == expected_value]
    if len(matches) != 1:
        raise RuntimeError(f"Protean did not provide one unambiguous {option_label} option.")
    option = matches[0]
    logger.info("Dropdown options inspected: control=%s count=%d", selector, len(option_rows))

    control_id = await control.get_attribute("id")
    select2_id = f"select2-{control_id}-container" if control_id else ""
    visible_widget = page.locator(f'.select2-selection[aria-labelledby="{select2_id}"]') if select2_id else None
    widget_is_visible = bool(visible_widget and await visible_widget.count() == 1 and await visible_widget.is_visible())
    select2_control = "select2-hidden-accessible" in (await control.get_attribute("class") or "")

    if widget_is_visible and select2_control:
        logger.info("Visible custom dropdown detected: control=%s", selector)
        await visible_widget.click()
        choice = page.get_by_role("option", name=option_label, exact=True)
        await choice.wait_for(state="visible", timeout=10000)
        await choice.click()
    elif await control.is_visible() and not await control.is_disabled():
        logger.info("Visible native dropdown detected: control=%s", selector)
        await control.select_option(index=option["index"])
    else:
        raise RuntimeError(f"Protean’s {option_label} dropdown is hidden and has no visible interactive widget.")

    await page.wait_for_function(
        "({selector, value}) => document.querySelector(selector)?.value === value",
        arg={"selector": selector, "value": option["value"]},
        timeout=10000,
    )
    # Refresh the locator after provider change handlers have had a chance to replace DOM nodes.
    if widget_is_visible:
        await page.locator(f"#{select2_id}").wait_for(state="attached", timeout=10000)
        visible_text = (await page.locator(f"#{select2_id}").inner_text()).strip()
        if visible_text.casefold() != option_label.casefold():
            raise RuntimeError(f"Protean did not display the selected {option_label} option.")
    if wait_for_selector:
        await page.locator(wait_for_selector).wait_for(state="visible", timeout=15000)
    await ensure_browser_open(page, f"verifying {option_label}")
    logger.info("Dropdown selection verified: control=%s option=%s", selector, option_label)
    return option


async def _wait_until_resumed(state: RegistrationSession) -> None:
    await state.pause_event.wait()
    if state.closed:
        raise asyncio.CancelledError()


async def _type_visible(page: Page, selector: str, value: str, label: str, state: RegistrationSession) -> None:
    state.current_field = label
    state.stage = f"filling {label.lower()}"
    await _wait_until_resumed(state)
    await ensure_browser_open(page, state.stage)
    control = page.locator(selector)
    await control.wait_for(state="visible", timeout=15000)
    if await control.is_disabled():
        raise RuntimeError(f"Protean’s {label.lower()} control is disabled.")
    await control.click()
    await control.press("Control+A")
    await control.press("Backspace")
    for character in value:
        await _wait_until_resumed(state)
        await ensure_browser_open(page, state.stage)
        await control.press_sequentially(character)
    actual = await control.input_value()
    if actual != value:
        raise RuntimeError(f"Protean’s {label.lower()} field did not pass read-back verification.")
    valid = await control.evaluate("e => e.checkValidity()")
    if not valid:
        message = await control.evaluate("e => e.validationMessage")
        raise RuntimeError(f"Protean marked {label.lower()} invalid: {message or 'check the field'}.")
    logger.info("Provider field typed and verified: field=%s", label)
    state.results.append({"field": label, "status": "verified"})


async def _fill_actual_form(page: Page, details: ApplicantDetails, state: RegistrationSession) -> list[dict]:
    state.stage = "checking the loaded registration page"
    await ensure_browser_open(page, "checking the loaded registration page")
    if urlparse(page.url).hostname != PROTEAN_HOST or urlparse(page.url).path != "/paam/endUserRegisterContact.html":
        raise RuntimeError("The official registration page did not load at the expected address.")

    logger.info("Protean registration page loaded")
    state.status = "selecting_application_type"
    state.stage = "selecting the Form 93 application type"
    type_options = await page.locator("#type option").evaluate_all(
        "els => els.map((e,index) => ({index,value:e.value,label:e.textContent.trim()}))"
    )
    logger.info("Dropdown detected: control=#type options=%d", len(type_options))
    form93_matches = [o for o in type_options if "form no. 93" in o["label"].casefold() and "indian citizen" in o["label"].casefold()]
    if len(form93_matches) != 1:
        raise RuntimeError("Protean’s New PAN Form 93 (Indian Citizen) option is unavailable or ambiguous.")
    await _wait_until_resumed(state)
    await select_dropdown(page, "#type", form93_matches[0]["label"], expected_value=form93_matches[0]["value"], wait_for_selector="#f_name_end")
    await _wait_until_resumed(state)
    results = [{"field": "Application type", "status": "verified"}]
    state.results = results

    # Protean currently hides categoryContainer for Form 93. Inspect its real options,
    # but do not interact with an unavailable control or fire events on hidden markup.
    state.status = "checking_applicant_category"
    state.stage = "checking the applicant category control"
    category = page.locator("#cat_applicant1")
    if await category.count() == 1:
        cat_options = await category.locator("option").evaluate_all(
            "els => els.map((e,index) => ({index,value:e.value,label:e.textContent.trim()}))"
        )
        individual = next((o for o in cat_options if o["value"] == "P" and o["label"].casefold() == "individual"), None)
        logger.info("Dropdown detected: control=#cat_applicant1 options=%d", len(cat_options))
        logger.info("Applicant category options inspected")
        if not individual:
            raise RuntimeError("Protean’s live category options do not include INDIVIDUAL.")
        if await page.locator("#categoryContainer").is_visible():
            state.stage = "selecting the visible INDIVIDUAL category"
            await _wait_until_resumed(state)
            await select_dropdown(page, "#cat_applicant1", individual["label"], expected_value=individual["value"], wait_for_selector="#f_name_end")
            await _wait_until_resumed(state)
            results.append({"field": "Applicant category", "status": "verified"})
        else:
            if "indian citizen" not in form93_matches[0]["label"].casefold():
                raise RuntimeError("Protean hid applicant category outside the supported Form 93 Indian citizen route.")
            logger.info("Applicant category hidden by Protean for the selected Form 93 route; left untouched")
            results.append({"field": "Applicant category", "status": "not required by provider for Form 93"})
        state.results = results
    else:
        raise RuntimeError("Protean’s applicant category control is missing from the live form.")

    state.status = "filling_provider_fields"
    mapping = [
        ("#f_name_end", details.first_name, "First name"),
        ("#m_name_end", details.middle_name, "Middle name"),
        ("#l_name_end", details.last_name, "Last name"),
        ("#date_of_birth_reg", details.date_of_birth, "Date of birth"),
        ("#email_id2", details.email, "Email address"),
        ("#rvContactNo", details.mobile, "Mobile number"),
    ]
    state.fields_total = len(mapping) + len(results)
    for selector, value, label in mapping:
        await _type_visible(page, selector, value, label, state)

    citizen = page.locator("#citizenCr1")
    if await citizen.count() == 1 and await citizen.is_visible():
        await _wait_until_resumed(state)
        await citizen.check()
        if not await page.locator("#citizenCr1").is_checked():
            raise RuntimeError("Protean’s Indian-citizen option could not be verified.")
        state.results.append({"field": "Indian citizenship", "status": "verified"})

    captcha_present = await page.locator('textarea[name="g-recaptcha-response"]').count() > 0 or await page.locator("iframe[src*='recaptcha']").count() > 0
    state.captcha_detected = captcha_present
    await _wait_until_resumed(state)
    logger.info("CAPTCHA detected: %s", captcha_present)
    state.current_field = None
    state.status = "awaiting_user_captcha_and_consent"
    state.stage = "waiting for user CAPTCHA and consent"
    return results


async def _run_fill(state: RegistrationSession, details: ApplicantDetails) -> None:
    try:
        state.results = []
        await _fill_actual_form(state.page, details, state)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        state.error = f"{type(exc).__name__}: {exc}"[:240]
        state.status = "fill_error"
        state.current_field = None
        state.stage = "stopped during field entry"
        logger.exception("Provider field entry stopped safely: exception=%s", type(exc).__name__)
    finally:
        for key in details.model_fields:
            try:
                setattr(details, key, "")
            except Exception:
                pass


@router.post("/start")
async def start_registration():
    global active
    async with lock:
        if active and not active.closed:
            raise HTTPException(status_code=409, detail="A visible browser session is already active. End it before starting another.")
        playwright = browser = context = page = None
        state = None
        stage = "starting Playwright"
        try:
            import os
            playwright = await async_playwright().start()
            headless = os.getenv("APP_ENV", "development") == "test"
            browser = await playwright.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--window-size=1365,900"],
            )
            logger.info("Browser launched")
            stage = "creating browser context"
            context = await browser.new_context(viewport={"width": 1365, "height": 900}, accept_downloads=False)
            logger.info("Browser context created")
            stage = "creating provider page"
            page = await context.new_page()
            logger.info("Provider page created")
            state = RegistrationSession(
                token=secrets.token_urlsafe(32), playwright=playwright, browser=browser,
                context=context, page=page, status="opening_provider_page", results=[],
                last_activity=time.monotonic(),
            )
            state.pause_event.set()
            active = state

            def note_page_closed() -> None:
                if not state.closed:
                    state.status = "outcome_uncertain_no_retry" if state.submission_started else "browser_closed"
                    logger.error("Provider page closed unexpectedly at stage=%s submission_started=%s", state.stage, state.submission_started)

            def note_browser_disconnected() -> None:
                if not state.closed:
                    state.status = "outcome_uncertain_no_retry" if state.submission_started else "browser_closed"
                    logger.error("Browser disconnected at stage=%s submission_started=%s", state.stage, state.submission_started)

            page.on("close", note_page_closed)
            page.on("crash", lambda: logger.error("Provider page crashed"))
            browser.on("disconnected", note_browser_disconnected)

            async def note_provider_response(response) -> None:
                try:
                    parsed_response = urlparse(response.url)
                    if (
                        response.request.is_navigation_request()
                        and response.request.frame == page.main_frame
                        and parsed_response.hostname == PROTEAN_HOST
                        and parsed_response.path == PROTEAN_RESPONSE_PATH
                    ):
                        state.provider_response_status = response.status
                        body = await response.text()
                        state.actual_response = {"httpStatus": response.status, "url": response.url, "text": body[:12000]}
                        state.status = "provider_response_received"
                        logger.info("Actual initial provider response received: http_status=%d", response.status)
                except Exception:
                    logger.error("Could not capture the initial provider response")

            page.on("response", note_provider_response)
            await page.route("**/*", _route_guard)
            stage = "loading Protean registration page"
            state.stage = stage
            await page.goto(PROTEAN_URL, wait_until="domcontentloaded", timeout=45000)
            stage = "waiting for user details"
            state.stage = stage
            state.status = "ready_to_fill"
            in_app_viewer = os.getenv("APP_ENV", "development") == "local"
            logger.info("Real Protean registration page ready for sequential entry")
            return {
                "status": state.status,
                "fields": [],
                "captchaDetected": False,
                "automatedSubmissionPermitted": AUTOMATED_SUBMISSION_PERMITTED,
                "providerUrl": PROTEAN_URL,
                "viewer": "/viewer/vnc.html?autoconnect=1&resize=scale&view_only=false" if in_app_viewer else None,
                "visibleBrowserWindow": not in_app_viewer and not headless,
                "token": state.token,
                "notice": "Protean is open in the visible browser. Start the controlled field entry, then review the page yourself.",
            }
        except Exception as exc:
            failure_stage = state.stage if state else stage
            page_was_closed = bool(page and page.is_closed())
            context_was_closed = bool(page and page.is_closed())
            browser_was_disconnected = bool(browser and not browser.is_connected())
            logger.error(
                "Registration failed at stage=%s exception=%s page_closed=%s context_closed=%s browser_connected=%s",
                failure_stage, type(exc).__name__, page_was_closed, context_was_closed, not browser_was_disconnected,
            )
            if state:
                await _close(state)
            else:
                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass
                if browser and browser.is_connected():
                    try:
                        await browser.close()
                    except Exception:
                        pass
                if playwright:
                    try:
                        await playwright.stop()
                    except Exception:
                        pass
            if page_was_closed or context_was_closed or browser_was_disconnected:
                detail = f"The visible Protean browser closed during {failure_stage}. No registration was submitted; start again."
            elif isinstance(exc, RuntimeError):
                detail = f"Could not continue while {failure_stage}: {str(exc)[:180]} No registration was submitted."
            elif "Timeout" in type(exc).__name__:
                detail = f"Protean did not finish {failure_stage} in time. No registration was submitted."
            else:
                detail = f"The provider page could not continue while {failure_stage}. No registration was submitted."
            raise HTTPException(status_code=502, detail=detail) from None


@router.post("/fill")
async def fill_registration(details: ApplicantDetails, authorization: str | None = Header(default=None)):
    state = await _get(authorization)
    if state.status != "ready_to_fill" or state.fill_task and not state.fill_task.done():
        raise HTTPException(status_code=409, detail="This session is not ready to begin field entry.")
    state.status = "filling_provider_fields"
    state.error = None
    state.pause_event.set()
    state.fill_task = asyncio.create_task(_run_fill(state, details))
    return {"status": "filling_provider_fields", "fieldsTotal": 8}


@router.post("/pause")
async def pause_registration(authorization: str | None = Header(default=None)):
    state = await _get(authorization)
    if state.status not in {"filling_provider_fields", "selecting_application_type", "checking_applicant_category"}:
        raise HTTPException(status_code=409, detail="There is no active field entry to pause.")
    state.pause_event.clear()
    state.status = "paused"
    return {"status": state.status, "currentField": state.current_field}


@router.post("/resume")
async def resume_registration(authorization: str | None = Header(default=None)):
    state = await _get(authorization)
    if state.status != "paused":
        raise HTTPException(status_code=409, detail="The field entry is not paused.")
    state.status = "filling_provider_fields"
    state.pause_event.set()
    return {"status": state.status}


@router.get("/status")
async def registration_status(authorization: str | None = Header(default=None)):
    state = await _get(authorization)
    provider_url = state.page.url if not state.page.is_closed() else PROTEAN_URL
    parsed = urlparse(provider_url)
    if (
        state.actual_response is None
        and parsed.hostname == PROTEAN_HOST
        and parsed.path == PROTEAN_RESPONSE_PATH
        and state.provider_response_status is not None
        and not state.page.is_closed()
    ):
        try:
            text = (await state.page.locator("body").inner_text(timeout=5000)).strip()
            state.actual_response = {"httpStatus": state.provider_response_status, "url": provider_url, "text": text[:12000]}
            state.status = "provider_response_received"
            logger.info("Actual provider response received after manual provider submission: http_status=%d", state.provider_response_status)
        except Exception as exc:
            logger.error("Could not read initial provider response: exception=%s", type(exc).__name__)
    error = None
    if state.status == "browser_closed":
        error = f"The Protean browser closed during {state.stage}. No registration was submitted. You can restart."
    elif state.status == "outcome_uncertain_no_retry" and state.submission_started and not state.actual_response:
        error = "The browser closed after the submit attempt. The result is uncertain; check Protean before starting again."
    return {"status": state.status, "fields": state.results, "currentField": state.current_field,
            "fieldsDone": len([item for item in state.results if item.get("status") in {"verified", "not required by provider for Form 93"}]),
            "fieldsTotal": state.fields_total, "error": state.error,
            "captchaDetected": state.captcha_detected,
            "automatedSubmissionPermitted": AUTOMATED_SUBMISSION_PERMITTED, "providerUrl": provider_url,
            "actualResponse": state.actual_response, "error": error}


@router.post("/captcha-complete")
async def captcha_complete(authorization: str | None = Header(default=None)):
    state = await _get(authorization)
    async with state.operation_lock:
        if state.status != "awaiting_user_captcha_and_consent":
            raise HTTPException(status_code=409, detail="This session is not waiting for CAPTCHA and consent.")
        try:
            await ensure_browser_open(state.page, "checking CAPTCHA completion")
            captcha_present = await state.page.locator('textarea[name="g-recaptcha-response"]').evaluate("e => Boolean(e.value && e.value.trim().length > 0)")
            consent = state.page.locator("#consent")
            consent_checked = await consent.count() == 1 and await consent.is_checked()
        except Exception as exc:
            if state.page.is_closed() or not state.browser.is_connected():
                state.status = "browser_closed"
                logger.error("Browser closed while checking CAPTCHA completion")
                raise HTTPException(status_code=410, detail="The Protean browser closed. No submission was attempted; restart the registration.") from None
            logger.error("Could not check CAPTCHA completion; exception=%s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="Could not inspect CAPTCHA and consent in the Protean browser.") from None
        logger.info("User marked CAPTCHA completion; provider token detected=%s", captcha_present)
        if not captcha_present:
            raise HTTPException(status_code=422, detail="Protean has not exposed a completed CAPTCHA response. Solve its CAPTCHA in the visible browser first.")
        if not consent_checked:
            raise HTTPException(status_code=422, detail="Review and check Protean’s own consent checkbox in the visible browser first.")
        if AUTOMATED_SUBMISSION_PERMITTED:
            state.status = "ready_for_explicit_submission_approval"
            state.stage = "waiting for explicit submission authorization"
            logger.info("User CAPTCHA/consent checks passed; waiting for separate submission authorization")
            message = "CAPTCHA response detected locally and provider consent is checked. Protean must still validate CAPTCHA when you authorize submission."
        else:
            state.status = "manual_submission_required"
            state.stage = "waiting for user-controlled Protean submission"
            logger.info("Provider CAPTCHA is present; automated submission disabled pending verified provider permission")
            message = "CAPTCHA response detected locally. Protean must still validate it. Provider-specific permission for automated submission is unverified, so use Protean’s own Submit control if you choose."
        return {"status": state.status, "automatedSubmissionPermitted": AUTOMATED_SUBMISSION_PERMITTED, "message": message}


class SubmitApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authorized: bool


async def _submit_initial_registration(state: RegistrationSession, payload: SubmitApproval):
    if not AUTOMATED_SUBMISSION_PERMITTED:
        raise HTTPException(status_code=403, detail="Automated submission is disabled until Protean’s service-specific permission is verified. Use the visible Protean Submit control if you choose.")
    if not payload.authorized:
        raise HTTPException(status_code=400, detail="Explicit submission authorization is required.")
    if state.status != "ready_for_explicit_submission_approval" or state.submission_started:
        raise HTTPException(status_code=409, detail="The session is not ready to submit or already attempted submission.")
    if urlparse(state.page.url).hostname != PROTEAN_HOST or urlparse(state.page.url).path != "/paam/endUserRegisterContact.html":
        raise HTTPException(status_code=409, detail="The initial Protean registration page is no longer active.")
    if not await state.page.locator("#consent").is_checked():
        raise HTTPException(status_code=422, detail="Protean consent is no longer checked. Review it on the provider page.")
    captcha_present = await state.page.locator('textarea[name="g-recaptcha-response"]').evaluate("e => Boolean(e.value && e.value.trim().length > 0)")
    if not captcha_present:
        raise HTTPException(status_code=422, detail="Protean CAPTCHA completion is no longer present.")
    state.submission_started = True
    state.status = "submission_in_progress"
    state.stage = "submitting initial Protean registration"
    logger.info("Submission attempted after explicit user authorization")
    submit_button = state.page.locator("#submitForm")
    if await submit_button.count() != 1:
        state.status = "provider_blocked"
        raise HTTPException(status_code=409, detail="Protean’s initial registration submit control is unavailable.")
    response = None
    try:
        async with state.page.expect_response(
            lambda r: r.request.is_navigation_request() and r.request.frame == state.page.main_frame,
            timeout=60000,
        ) as response_info:
            await submit_button.click()
        response = await response_info.value
        parsed_response = urlparse(response.url)
        if parsed_response.hostname != PROTEAN_HOST or parsed_response.path != PROTEAN_RESPONSE_PATH:
            state.status = "provider_blocked"
            state.actual_response = None
            logger.warning("Provider response did not match the permitted initial registration response")
        else:
            try:
                text = await response.text()
            except Exception:
                text = ""
            state.status = "provider_response_received"
            state.actual_response = {"httpStatus": response.status, "url": response.url, "text": text[:12000]}
            logger.info("Actual provider response received: http_status=%d", response.status)
    except Exception:
        state.status = "outcome_uncertain_no_retry"
        logger.error("Submission response could not be confirmed; automatic retry disabled")
        state.actual_response = None
    return {"status": state.status, "actualResponse": state.actual_response}


@router.post("/submit")
async def submit_initial_registration(payload: SubmitApproval, authorization: str | None = Header(default=None)):
    state = await _get(authorization)
    async with state.operation_lock:
        return await _submit_initial_registration(state, payload)


@router.delete("/session")
async def end_registration(authorization: str | None = Header(default=None)):
    state = await _get(authorization)
    await _close(state)
    return {"status": "closed"}


@router.post("/session/close")
async def close_registration_beacon(request: Request):
    """Best effort close when the tab exits; never logs or stores applicant fields."""
    global active
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    token = payload.get("token", "") if isinstance(payload, dict) else ""
    state = active
    if not state or state.closed or not isinstance(token, str) or not secrets.compare_digest(state.token, token):
        return {"status": "already closed"}
    await _close(state)
    return {"status": "closed"}
