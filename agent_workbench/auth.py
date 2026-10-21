"""Password hashing and opaque sessions; credentials never enter model context."""

import hashlib
import hmac
import secrets
import time
from collections import defaultdict
from threading import Lock


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + ":" + digest.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        salt, expected = encoded.split(":")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginLimiter:
    """Per-process login throttling; use a shared limiter for multiple replicas."""

    def __init__(self):
        self._attempts = defaultdict(list)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        with self._lock:
            now = time.time()
            self._attempts[key] = [stamp for stamp in self._attempts[key] if stamp > now - 60]
            if len(self._attempts[key]) >= 10:
                return False
            self._attempts[key].append(now)
            # Prevent unbounded growth from unique addresses.
            if len(self._attempts) > 2000:
                self._attempts = defaultdict(list, {key: self._attempts[key]})
            return True
