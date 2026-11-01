import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_database_session
from app.database.base import Base
from app.database.models import Analysis, AnalysisMetric, Product, ProductFeedback, RefreshToken, User
from app.services.auth import hash_refresh_token
from app.main import app
from tests.test_database_repository import analysis_response


class AuthRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_secret = os.environ.get("AUTH_SECRET_KEY")
        os.environ["AUTH_SECRET_KEY"] = "test-secret-key-that-is-longer-than-thirty-two-characters"

    @classmethod
    def tearDownClass(cls):
        if cls.previous_secret is None:
            os.environ.pop("AUTH_SECRET_KEY", None)
        else:
            os.environ["AUTH_SECRET_KEY"] = cls.previous_secret

    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)

        def override_session():
            with Session(self.engine, expire_on_commit=False) as session:
                yield session

        app.dependency_overrides[get_database_session] = override_session
        self.client = TestClient(app)
        self.email_verification_patcher = patch("app.api.auth.send_email_verification")
        self.send_verification = self.email_verification_patcher.start()

    def tearDown(self):
        self.email_verification_patcher.stop()
        app.dependency_overrides.clear()
        self.engine.dispose()

    def register(self, email="user@example.com", password="strong-pass-123"):
        return self.client.post(
            "/v1/auth/register",
            json={"email": email, "password": password},
        )

    def test_register_hashes_password_and_returns_access_token(self):
        response = self.register(email="User@Example.com")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["token_type"], "bearer")
        self.assertEqual(body["expires_in"], 1800)
        self.assertTrue(body["refresh_token"])
        self.assertEqual(body["refresh_expires_in"], 30 * 24 * 60 * 60)
        self.assertEqual(body["user"]["email"], "user@example.com")

        with Session(self.engine) as session:
            user = session.scalar(select(User))
            self.assertNotEqual(user.password_hash, "strong-pass-123")
            self.assertTrue(user.password_hash.startswith("$argon2"))

            stored_refresh = session.scalar(select(RefreshToken))
            self.assertEqual(
                stored_refresh.token_hash,
                hash_refresh_token(body["refresh_token"]),
            )
            self.assertNotEqual(stored_refresh.token_hash, body["refresh_token"])

        usage = self.client.get(
            "/v1/usage",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        self.assertEqual(usage.status_code, 200)
        self.assertEqual(usage.json()["plan"], "free")
        self.assertEqual(usage.json()["remaining"], 5)

    def test_admin_endpoints_reject_normal_user(self):
        registered = self.register().json()
        response = self.client.get(
            "/v1/admin/metrics/summary",
            headers={"Authorization": f"Bearer {registered['access_token']}"},
        )
        self.assertEqual(response.status_code, 403)

    def test_configured_admin_can_read_metric_summary(self):
        with patch.dict(os.environ, {"ADMIN_EMAILS": "admin@example.com"}):
            registered = self.register(email="admin@example.com").json()
            with Session(self.engine) as session:
                session.add(AnalysisMetric(
                    request_key="admin-summary-test",
                    flow_type="vision",
                    status="complete",
                    total_ms=2500,
                    stage_durations_ms={},
                    formula_cache_used=True,
                    ingredient_cache_used=False,
                    model_usage={},
                ))
                session.commit()
            headers = {"Authorization": f"Bearer {registered['access_token']}"}
            me = self.client.get("/v1/users/me", headers=headers)
            summary = self.client.get("/v1/admin/metrics/summary", headers=headers)

        self.assertTrue(me.json()["is_admin"])
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["total_analyses"], 1)
        self.assertEqual(summary.json()["success_rate"], 100.0)
        self.assertEqual(summary.json()["average_duration_ms"], 2500)

    def test_admin_can_mark_feedback_reviewed(self):
        with patch.dict(os.environ, {"ADMIN_EMAILS": "admin@example.com"}):
            registered = self.register(email="admin@example.com").json()
            user_id = registered["user"]["id"]
            with Session(self.engine) as session:
                product = Product(
                    brand="Test", product_name="Cream", variant=None,
                    category="Moisturizer", market="TR", barcode=None,
                    normalized_key="test|cream||TR",
                )
                session.add(product)
                session.flush()
                analysis = Analysis(
                    user_id=user_id, product_id=product.id,
                    skin_profile_snapshot={}, routine_snapshot=[], status="complete",
                )
                session.add(analysis)
                session.flush()
                feedback = ProductFeedback(
                    user_id=user_id, analysis_id=analysis.id, product_id=product.id,
                    reason="wrong_product", status="pending",
                )
                session.add(feedback)
                session.commit()
                feedback_id = feedback.id
            response = self.client.patch(
                f"/v1/admin/feedback/{feedback_id}",
                headers={"Authorization": f"Bearer {registered['access_token']}"},
                json={"status": "reviewed"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "reviewed")
        with Session(self.engine) as session:
            stored = session.get(ProductFeedback, feedback_id)
            self.assertIsNotNone(stored.reviewed_at)

    def test_refresh_token_rotates_and_logout_revokes_session(self):
        registered = self.register().json()
        first_refresh = registered["refresh_token"]

        refreshed = self.client.post(
            "/v1/auth/refresh",
            json={"refresh_token": first_refresh},
        )
        self.assertEqual(refreshed.status_code, 200)
        second_refresh = refreshed.json()["refresh_token"]
        self.assertNotEqual(first_refresh, second_refresh)

        replay = self.client.post(
            "/v1/auth/refresh",
            json={"refresh_token": first_refresh},
        )
        self.assertEqual(replay.status_code, 401)

        logged_out = self.client.post(
            "/v1/auth/logout",
            json={"refresh_token": second_refresh},
        )
        self.assertEqual(logged_out.status_code, 204)
        after_logout = self.client.post(
            "/v1/auth/refresh",
            json={"refresh_token": second_refresh},
        )
        self.assertEqual(after_logout.status_code, 401)

    def test_email_verification_is_single_use_and_updates_user(self):
        registered = self.register().json()
        verification_link = self.send_verification.call_args.args[1]
        verification_token = parse_qs(urlparse(verification_link).query)["token"][0]

        verified = self.client.post(
            "/v1/auth/verify-email",
            json={"token": verification_token},
        )
        self.assertEqual(verified.status_code, 204)
        me = self.client.get(
            "/v1/users/me",
            headers={"Authorization": f"Bearer {registered['access_token']}"},
        )
        self.assertTrue(me.json()["email_verified"])
        replay = self.client.post(
            "/v1/auth/verify-email",
            json={"token": verification_token},
        )
        self.assertEqual(replay.status_code, 400)

    def test_unverified_user_can_request_another_verification_email(self):
        registered = self.register().json()
        response = self.client.post(
            "/v1/users/me/email-verification",
            headers={"Authorization": f"Bearer {registered['access_token']}"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.send_verification.call_count, 2)

    def test_password_reset_is_single_use_and_revokes_sessions(self):
        registered = self.register().json()
        with patch("app.api.auth.send_password_reset_email") as send_email:
            unknown = self.client.post(
                "/v1/auth/forgot-password",
                json={"email": "unknown@example.com"},
            )
            requested = self.client.post(
                "/v1/auth/forgot-password",
                json={"email": "user@example.com"},
            )
        self.assertEqual(unknown.status_code, 202)
        self.assertEqual(requested.status_code, 202)
        send_email.assert_called_once()
        reset_link = send_email.call_args.args[1]
        reset_token = parse_qs(urlparse(reset_link).query)["token"][0]

        reset = self.client.post(
            "/v1/auth/reset-password",
            json={"token": reset_token, "new_password": "reset-strong-pass-789"},
        )
        self.assertEqual(reset.status_code, 204)
        replay = self.client.post(
            "/v1/auth/reset-password",
            json={"token": reset_token, "new_password": "another-strong-pass-999"},
        )
        self.assertEqual(replay.status_code, 400)
        old_session = self.client.post(
            "/v1/auth/refresh",
            json={"refresh_token": registered["refresh_token"]},
        )
        self.assertEqual(old_session.status_code, 401)
        new_login = self.client.post(
            "/v1/auth/token",
            data={"username": "user@example.com", "password": "reset-strong-pass-789"},
        )
        self.assertEqual(new_login.status_code, 200)

    def test_duplicate_email_is_case_insensitive(self):
        self.assertEqual(self.register(email="User@Example.com").status_code, 201)
        duplicate = self.register(email="user@example.com")
        self.assertEqual(duplicate.status_code, 409)

    def test_login_rejects_wrong_password(self):
        self.register()
        response = self.client.post(
            "/v1/auth/token",
            data={"username": "user@example.com", "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_user_can_change_password_after_confirming_current_password(self):
        token = self.register().json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        wrong = self.client.put(
            "/v1/users/me/password",
            headers=headers,
            json={"current_password": "wrong-password", "new_password": "new-strong-pass-456"},
        )
        self.assertEqual(wrong.status_code, 400)

        changed = self.client.put(
            "/v1/users/me/password",
            headers=headers,
            json={"current_password": "strong-pass-123", "new_password": "new-strong-pass-456"},
        )
        self.assertEqual(changed.status_code, 204)

        old_login = self.client.post(
            "/v1/auth/token",
            data={"username": "user@example.com", "password": "strong-pass-123"},
        )
        new_login = self.client.post(
            "/v1/auth/token",
            data={"username": "user@example.com", "password": "new-strong-pass-456"},
        )
        self.assertEqual(old_login.status_code, 401)
        self.assertEqual(new_login.status_code, 200)

    def test_account_deletion_removes_personal_analysis_but_preserves_catalog(self):
        registered = self.register().json()
        user_id = registered["user"]["id"]
        with Session(self.engine) as session:
            product = Product(
                brand="Shared Brand", product_name="Shared Product", variant=None,
                category="Moisturizer", market="TR", barcode=None,
                normalized_key="shared-brand|shared-product|tr",
            )
            session.add(product)
            session.flush()
            analysis = Analysis(
                user_id=user_id, product_id=product.id,
                skin_profile_snapshot={"skin_type": "Kuru"}, routine_snapshot=[], status="complete",
            )
            session.add(analysis)
            session.commit()
            product_id, analysis_id = product.id, analysis.id

        headers = {"Authorization": f"Bearer {registered['access_token']}"}
        wrong = self.client.request(
            "DELETE", "/v1/users/me", headers=headers,
            json={"password": "wrong-password", "confirmation": "SİL"},
        )
        self.assertEqual(wrong.status_code, 400)
        deleted = self.client.request(
            "DELETE", "/v1/users/me", headers=headers,
            json={"password": "strong-pass-123", "confirmation": "SİL"},
        )
        self.assertEqual(deleted.status_code, 204)
        with Session(self.engine) as session:
            self.assertIsNone(session.get(User, user_id))
            self.assertIsNone(session.get(Analysis, analysis_id))
            self.assertIsNotNone(session.get(Product, product_id))

    def test_user_can_report_analysis_without_mutating_catalog(self):
        registered = self.register().json()
        user_id = registered["user"]["id"]
        with Session(self.engine) as session:
            product = Product(
                brand="Shared Brand", product_name="Shared Product", variant=None,
                category="Moisturizer", market="TR", barcode="8690123456789",
                normalized_key="shared-brand|shared-product||TR",
            )
            session.add(product)
            session.flush()
            analysis = Analysis(
                user_id=user_id, product_id=product.id,
                skin_profile_snapshot={}, routine_snapshot=[], status="complete",
            )
            session.add(analysis)
            session.commit()
            analysis_id, product_id = analysis.id, product.id

        response = self.client.post(
            f"/v1/users/me/analyses/{analysis_id}/feedback",
            headers={"Authorization": f"Bearer {registered['access_token']}"},
            json={"reason": "wrong_barcode", "details": "Barkod başka varyanta ait."},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "pending")
        with Session(self.engine) as session:
            feedback = session.scalar(select(ProductFeedback))
            self.assertEqual(feedback.reason, "wrong_barcode")
            self.assertEqual(session.get(Product, product_id).barcode, "8690123456789")

    def test_token_authenticates_user_and_profile_can_be_saved(self):
        token = self.register().json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        me = self.client.get("/v1/users/me", headers=headers)
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["email"], "user@example.com")

        updated = self.client.put(
            "/v1/users/me/profile",
            headers=headers,
            json={
                "skin_type": "Kuru",
                "sensitivity": "Hassas",
                "appearance": ["Kızarıklık"],
                "goals": ["hydration", "uneven_tone"],
                "avoid_ingredients": ["Parfum"],
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["avoid_ingredients"], ["Parfum"])
        self.assertEqual(updated.json()["goals"], ["hydration", "uneven_tone"])

        loaded = self.client.get("/v1/users/me/profile", headers=headers)
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["skin_type"], "Kuru")

        history = self.client.get("/v1/users/me/analyses", headers=headers)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["items"], [])
        self.assertEqual(history.json()["total"], 0)
        self.assertEqual(history.json()["history_limit"], 10)

        with patch(
            "app.api.analyze.run_and_persist_full_analysis",
            return_value=analysis_response(["Aqua", "Glycerin"]),
        ) as analyze:
            analyzed = self.client.post(
                "/v1/analyze-product",
                headers=headers,
                data={"routine": "[]", "market": "TR"},
                files={"product_image": ("front.jpg", b"image", "image/jpeg")},
            )
        self.assertEqual(analyzed.status_code, 200)
        self.assertEqual(analyze.call_args.kwargs["skin_profile"].skin_type, "Kuru")
        self.assertEqual(analyze.call_args.kwargs["user_id"], me.json()["id"])

    def test_protected_endpoint_rejects_missing_or_invalid_token(self):
        missing = self.client.get("/v1/users/me")
        invalid = self.client.get(
            "/v1/users/me", headers={"Authorization": "Bearer invalid-token"}
        )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(invalid.status_code, 401)

    def test_user_can_add_update_and_remove_manual_routine_product(self):
        registered = self.register().json()
        headers = {"Authorization": f"Bearer {registered['access_token']}"}

        created = self.client.post(
            "/v1/users/me/routine/manual",
            headers=headers,
            json={
                "product_name": "  Gentle Cleanser  ",
                "product_brand": "  Example Brand  ",
                "product_category": "Cleanser",
                "routine_time": "morning",
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["product_name"], "Gentle Cleanser")
        self.assertEqual(created.json()["product_brand"], "Example Brand")
        self.assertEqual(created.json()["ingredients"], [])

        routine_id = created.json()["id"]
        listed = self.client.get("/v1/users/me/routine", headers=headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

        updated = self.client.put(
            f"/v1/users/me/routine/{routine_id}",
            headers=headers,
            json={"routine_time": "both"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["routine_time"], "both")

        removed = self.client.delete(
            f"/v1/users/me/routine/{routine_id}", headers=headers
        )
        self.assertEqual(removed.status_code, 204)
        self.assertEqual(
            self.client.get("/v1/users/me/routine", headers=headers).json(), []
        )

    def test_manual_routine_product_requires_non_blank_name(self):
        registered = self.register().json()
        response = self.client.post(
            "/v1/users/me/routine/manual",
            headers={"Authorization": f"Bearer {registered['access_token']}"},
            json={"product_name": "   ", "routine_time": "evening"},
        )
        self.assertEqual(response.status_code, 422)

    def test_catalog_endpoint_returns_saved_products(self):
        with Session(self.engine) as session:
            product = Product(
                brand="Catalog Brand", product_name="Catalog Cream", variant="Sensitive",
                category="Moisturizer", market="TR", barcode="8690123456789",
                normalized_key="catalog-brand|catalog-cream|sensitive|TR",
            )
            session.add(product)
            session.commit()
            product_id = product.id
        response = self.client.get("/v1/products?q=catalog&category=Moisturizer")
        detail = self.client.get(f"/v1/products/{product_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.json()["items"][0]["brand"], "Catalog Brand")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["product_name"], "Catalog Cream")
        self.assertEqual(detail.json()["formulas"], [])


if __name__ == "__main__":
    unittest.main()
