import base64
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


KEYS_URL = "https://www.gstatic.com/admob/reward/verifier-keys.json"
_key_cache: tuple[float, dict[str, str]] = (0, {})


class AdMobVerificationError(RuntimeError):
    pass


def _keys() -> dict[str, str]:
    global _key_cache
    expires_at, cached = _key_cache
    if cached and expires_at > time.time():
        return cached
    try:
        request = Request(KEYS_URL, headers={"Accept": "application/json"})
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AdMobVerificationError("AdMob doğrulama anahtarları alınamadı.") from exc
    parsed = {str(item["keyId"]): item["pem"] for item in payload.get("keys", []) if item.get("keyId") is not None and item.get("pem")}
    if not parsed:
        raise AdMobVerificationError("AdMob doğrulama anahtarı bulunamadı.")
    _key_cache = (time.time() + 86_400, parsed)
    return parsed


def verify_callback_query(raw_query: bytes, *, signature: str, key_id: str) -> None:
    marker = b"&signature="
    signed_content, separator, tail = raw_query.partition(marker)
    if not separator or not tail.startswith(signature.encode()):
        raise AdMobVerificationError("AdMob callback sırası geçersiz.")
    pem = _keys().get(key_id)
    if pem is None:
        raise AdMobVerificationError("AdMob callback anahtarı tanınmıyor.")
    try:
        encoded = signature.replace("-", "+").replace("_", "/")
        decoded_signature = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
        public_key = serialization.load_pem_public_key(pem.encode())
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            raise AdMobVerificationError("AdMob anahtar türü geçersiz.")
        public_key.verify(decoded_signature, signed_content, ec.ECDSA(hashes.SHA256()))
    except (ValueError, InvalidSignature) as exc:
        raise AdMobVerificationError("AdMob callback imzası geçersiz.") from exc
