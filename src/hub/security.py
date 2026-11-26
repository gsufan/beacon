"""Passwords and signed tokens.

The Hub signs sessions and licenses with an Ed25519 key. Beacon Local only holds the public key, so it can verify
both offline and cannot issue them.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

_SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)


class InvalidTokenError(ValueError):
    pass


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


def new_code(nbytes: int = 9) -> str:
    """Short random code for invitations."""
    return secrets.token_urlsafe(nbytes)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def load_or_create_key(path: Path) -> Ed25519PrivateKey:
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def public_key_text(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _b64(raw)


def sign_token(key: Ed25519PrivateKey, claims: dict) -> str:
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_b64(key.sign(body.encode()))}"


def verify_token(public_key: str, token: str, typ: str, now: Optional[float] = None) -> dict:
    """Claims of a valid, unexpired token of type `typ`; InvalidTokenError otherwise."""
    try:
        body, sig = token.split(".")
        Ed25519PublicKey.from_public_bytes(_unb64(public_key)).verify(_unb64(sig), body.encode())
        claims = json.loads(_unb64(body))
    except (ValueError, InvalidSignature, TypeError) as e:
        raise InvalidTokenError("Token inválido.") from e
    if claims.get("typ") != typ:
        raise InvalidTokenError("Tipo de token incorrecto.")
    if claims.get("exp", 0) < (now if now is not None else time.time()):
        raise InvalidTokenError("Token vencido.")
    return claims
