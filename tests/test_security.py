"""
tests/test_security.py
───────────────────────
Pure unit tests for app/core/security.py — no I/O required.
Tests run without any fixtures or database connections.
"""

import hashlib
import hmac as _hmac

import pytest
from cryptography.fernet import InvalidToken

from app.core.security import (
    compute_device_hmac,
    decrypt_device_secret,
    encrypt_device_secret,
    hash_password,
    verify_device_hmac,
    verify_password,
)


# ── Password hashing ──────────────────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        hashed = hash_password("MySuperSecret!")
        assert hashed != "MySuperSecret!"

    def test_verify_correct_password(self):
        hashed = hash_password("CorrectHorseBattery")
        assert verify_password("CorrectHorseBattery", hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("CorrectHorseBattery")
        assert verify_password("WrongPassword", hashed) is False

    def test_two_hashes_of_same_password_differ(self):
        """bcrypt uses a random salt — same input, different output."""
        h1 = hash_password("SameInput")
        h2 = hash_password("SameInput")
        assert h1 != h2

    def test_both_hashes_verify_correctly(self):
        h1 = hash_password("SameInput")
        h2 = hash_password("SameInput")
        assert verify_password("SameInput", h1) is True
        assert verify_password("SameInput", h2) is True


# ── Fernet device secret ──────────────────────────────────────────────────────

class TestFernetDeviceSecret:
    def test_encrypt_decrypt_roundtrip(self):
        secret = "factory-provisioned-secret-abc123"
        token = encrypt_device_secret(secret)
        assert decrypt_device_secret(token) == secret

    def test_encrypted_token_is_different_from_plaintext(self):
        secret = "my-device-secret"
        token = encrypt_device_secret(secret)
        assert token != secret

    def test_two_encryptions_differ(self):
        """Fernet uses random IVs — same plaintext, different ciphertext."""
        s = "same-secret"
        assert encrypt_device_secret(s) != encrypt_device_secret(s)

    def test_both_ciphertexts_decrypt_to_same_plaintext(self):
        s = "same-secret"
        t1, t2 = encrypt_device_secret(s), encrypt_device_secret(s)
        assert decrypt_device_secret(t1) == s
        assert decrypt_device_secret(t2) == s

    def test_tampered_token_raises(self):
        token = encrypt_device_secret("valid-secret")
        # Flip a byte in the middle of the token
        tampered = token[:20] + ("X" if token[20] != "X" else "Y") + token[21:]
        with pytest.raises(InvalidToken):
            decrypt_device_secret(tampered)

    def test_random_string_raises(self):
        with pytest.raises(Exception):
            decrypt_device_secret("this-is-not-a-fernet-token")


# ── HMAC computation and verification ────────────────────────────────────────

class TestDeviceHMAC:
    SECRET = "shared-device-secret-xyz"
    NONCE  = "abc123def456abc123def456abc123def456abc123def456abc123def456abcd"

    def _expected_hmac(self) -> str:
        return _hmac.new(
            self.SECRET.encode(), self.NONCE.encode(), hashlib.sha256
        ).hexdigest()

    def test_compute_returns_64_char_hex(self):
        result = compute_device_hmac(self.SECRET, self.NONCE)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_compute_matches_stdlib_hmac(self):
        result = compute_device_hmac(self.SECRET, self.NONCE)
        assert result == self._expected_hmac()

    def test_compute_different_nonce_differs(self):
        r1 = compute_device_hmac(self.SECRET, "nonce_a")
        r2 = compute_device_hmac(self.SECRET, "nonce_b")
        assert r1 != r2

    def test_compute_different_secret_differs(self):
        r1 = compute_device_hmac("secret_a", self.NONCE)
        r2 = compute_device_hmac("secret_b", self.NONCE)
        assert r1 != r2

    def test_verify_correct_hmac(self):
        enc = encrypt_device_secret(self.SECRET)
        hmac_hex = self._expected_hmac()
        assert verify_device_hmac(enc, self.NONCE, hmac_hex) is True

    def test_verify_wrong_hmac_returns_false(self):
        enc = encrypt_device_secret(self.SECRET)
        assert verify_device_hmac(enc, self.NONCE, "deadbeef" * 8) is False

    def test_verify_case_insensitive(self):
        """Device firmware may return uppercase hex."""
        enc = encrypt_device_secret(self.SECRET)
        hmac_upper = self._expected_hmac().upper()
        assert verify_device_hmac(enc, self.NONCE, hmac_upper) is True

    def test_verify_wrong_nonce_returns_false(self):
        enc = encrypt_device_secret(self.SECRET)
        hmac_for_real_nonce = self._expected_hmac()
        assert verify_device_hmac(enc, "wrong-nonce", hmac_for_real_nonce) is False

    def test_verify_tampered_encrypted_secret_returns_false(self):
        """A corrupted device_secret_enc must not crash — return False."""
        assert verify_device_hmac("not-valid-fernet", self.NONCE, "abc") is False

    def test_verify_empty_hmac_returns_false(self):
        enc = encrypt_device_secret(self.SECRET)
        assert verify_device_hmac(enc, self.NONCE, "") is False
