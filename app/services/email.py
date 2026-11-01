import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class EmailDeliveryError(RuntimeError):
    pass


logger = logging.getLogger(__name__)


def _send_email(email: str, subject: str, html: str, dev_label: str, dev_link: str) -> None:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    environment = os.getenv("APP_ENV", "development").casefold()
    sender = os.getenv("RESEND_FROM_EMAIL", "").strip()
    if not sender and environment != "production":
        sender = "Cosmetic AI <onboarding@resend.dev>"
    if not api_key or not sender:
        if environment == "production":
            raise EmailDeliveryError("E-posta servisi yapılandırılmamış.")
        print(f"[DEV {dev_label}] {email}: {dev_link}")
        return

    payload = json.dumps({"from": sender, "to": [email], "subject": subject, "html": html}).encode("utf-8")
    request = Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "cosmetic-ai-backend/1.0"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            if response.status >= 300:
                raise EmailDeliveryError("E-posta gönderilemedi.")
    except HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        logger.error("Resend email request failed with status %s: %s", exc.code, response_body)
        raise EmailDeliveryError(f"Resend isteği başarısız (status={exc.code}).") from exc
    except URLError as exc:
        logger.error("Resend email request could not connect: %s", exc.reason)
        raise EmailDeliveryError("E-posta servisine bağlanılamadı.") from exc
    except Exception as exc:
        logger.exception("Unexpected email delivery error")
        raise EmailDeliveryError("E-posta gönderilemedi.") from exc


def send_password_reset_email(email: str, reset_link: str) -> None:
    _send_email(email, "Cosmetic AI şifre sıfırlama", f'<p>Şifreni yenilemek için aşağıdaki bağlantıyı aç:</p><p><a href="{reset_link}">Şifremi yenile</a></p><p>Bu bağlantı 15 dakika geçerlidir.</p>', "PASSWORD RESET", reset_link)


def send_email_verification(email: str, verification_link: str) -> None:
    _send_email(email, "Cosmetic AI e-posta doğrulama", f'<p>E-posta adresini doğrulamak için aşağıdaki bağlantıyı aç:</p><p><a href="{verification_link}">E-postamı doğrula</a></p><p>Bu bağlantı 24 saat geçerlidir.</p>', "EMAIL VERIFICATION", verification_link)
