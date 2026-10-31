import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import app as backend  # noqa: E402
from backend import registration as live_registration  # noqa: E402


class RegistrationTests(unittest.TestCase):
    def test_only_the_initial_protean_registration_and_first_response_are_document_targets(self):
        self.assertTrue(live_registration._allowed(live_registration.PROTEAN_URL, "document"))
        self.assertTrue(live_registration._allowed(
            "https://onlineservices.proteantech.in/paam/registerEndUser.html", "document"
        ))
        self.assertFalse(live_registration._allowed(
            "https://onlineservices.proteantech.in/paam/nextStage.html", "document"
        ))
        self.assertFalse(live_registration._allowed("https://example.com/", "document"))

    def test_captcha_resources_are_allowlisted_without_other_origins(self):
        self.assertTrue(live_registration._allowed("https://www.google.com/recaptcha/enterprise/anchor", "document"))
        self.assertTrue(live_registration._allowed("https://www.gstatic.com/recaptcha/releases/test/recaptcha__en.js", "script"))
        self.assertFalse(live_registration._allowed("https://www.google.com/search?q=pan", "document"))

    def test_live_applicant_input_validation(self):
        applicant = live_registration.ApplicantDetails(
            first_name="Aarav", middle_name="", last_name="Patel", date_of_birth="12/05/1994",
            email="applicant@example.test", mobile="9876543210",
        )
        self.assertEqual(applicant.mobile, "9876543210")
        with self.assertRaises(ValidationError):
            live_registration.ApplicantDetails(
                first_name="", last_name="Patel", date_of_birth="31/02/1994",
                email="invalid", mobile="0123",
            )

    def test_only_real_registration_ui_and_routes_are_served(self):
        with TestClient(backend.app) as client:
            health = client.get("/api/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["provider"], "protean")
            self.assertTrue(health.json()["liveProviderAutomation"])
            self.assertFalse(health.json()["automatedSubmissionPermitted"])
            self.assertIn("VERIIDIA", client.get("/").text)
            self.assertIn(live_registration.PROTEAN_URL, client.get("/registration").text)
            self.assertIn('id="supporting-files"', client.get("/registration").text)
            self.assertEqual(client.get("/sandbox").status_code, 404)
            self.assertEqual(client.post("/api/demo/session").status_code, 404)
            self.assertEqual(client.post("/api/demo/fill").status_code, 404)

    def test_registration_api_requires_a_bearer_session(self):
        with TestClient(backend.app) as client:
            self.assertEqual(client.get("/api/registration/status").status_code, 401)

    def test_no_static_success_or_acknowledgement_claims(self):
        root = Path(__file__).resolve().parents[1]
        page = (root / "registration.html").read_text(encoding="utf-8").casefold()
        self.assertNotIn("acknowledgement number", page)
        self.assertNotIn("registration successful", page)
        self.assertNotIn("demo mode", page)
        self.assertNotIn("mock protean", page)


if __name__ == "__main__":
    unittest.main()
