import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.models import User
from app.services.usage_limits import UsageLimitExceededError, finish_usage_event, reserve_vision_analysis, usage_status


class UsageLimitTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_guest_gets_one_charged_analysis_and_failure_is_refunded(self):
        event = reserve_vision_analysis(
            self.session, user=None, guest_key="guest-a", request_key="analysis-1"
        )
        finish_usage_event(self.session, event.id, charged=False, status="failed")
        self.assertEqual(usage_status(self.session, user=None, guest_key="guest-a")["remaining"], 1)

        second = reserve_vision_analysis(
            self.session, user=None, guest_key="guest-a", request_key="analysis-2"
        )
        finish_usage_event(self.session, second.id, charged=True, status="completed")
        self.assertEqual(usage_status(self.session, user=None, guest_key="guest-a")["remaining"], 0)
        with self.assertRaises(UsageLimitExceededError):
            reserve_vision_analysis(
                self.session, user=None, guest_key="guest-a", request_key="analysis-3"
            )

    def test_free_and_premium_plans_have_different_limits(self):
        free = User(email="free@example.com", normalized_email="free@example.com", password_hash="x", plan="free")
        premium = User(email="premium@example.com", normalized_email="premium@example.com", password_hash="x", plan="premium")
        self.session.add_all([free, premium])
        self.session.commit()

        free_usage = usage_status(self.session, user=free, guest_key=None)
        premium_usage = usage_status(self.session, user=premium, guest_key=None)
        self.assertEqual(free_usage["analysis_limit"], 5)
        self.assertEqual(free_usage["history_limit"], 10)
        self.assertTrue(free_usage["ads_enabled"])
        self.assertEqual(premium_usage["analysis_limit"], 100)
        self.assertIsNone(premium_usage["history_limit"])
        self.assertFalse(premium_usage["ads_enabled"])

    def test_request_key_is_idempotent(self):
        first = reserve_vision_analysis(
            self.session, user=None, guest_key="guest-b", request_key="analysis-same"
        )
        second = reserve_vision_analysis(
            self.session, user=None, guest_key="guest-b", request_key="analysis-same"
        )

        self.assertEqual(first.id, second.id)


if __name__ == "__main__":
    unittest.main()
