"""
tests/test_otp.py
──────────────────
Integration tests for SMS-OTP login.

Uses a small stateful in-memory Redis fake (the default mock_redis is stateless)
and patches the SMS sender to capture the generated code so we can complete the
verify step.
"""

import re

import pytest
from httpx import AsyncClient

from app.api.deps import get_redis
from app.main import app


class _FakeRedis:
    """Minimal stateful async Redis supporting the ops OTP uses."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, val):
        self.store[key] = str(val)
        return True

    async def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                n += 1
        return n

    async def incr(self, key):
        v = int(self.store.get(key, 0)) + 1
        self.store[key] = str(v)
        return v

    async def ping(self):
        return True


@pytest.fixture
def fake_redis():
    fake = _FakeRedis()
    app.dependency_overrides[get_redis] = lambda: fake
    yield fake
    # async_client fixture restores the original override on teardown via clear()


async def _register_with_phone(async_client: AsyncClient, phone: str) -> None:
    resp = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": f"otp{phone[-4:]}@rawbin.io",
            "password": "OtpUserPass123!",
            "display_name": "OTP User",
            "phone": phone,
        },
    )
    assert resp.status_code == 201, resp.text


async def test_otp_login_full_flow(async_client: AsyncClient, fake_redis, monkeypatch):
    phone = "+14155550101"
    await _register_with_phone(async_client, phone)

    # Capture the SMS body (contains the code) instead of really sending.
    captured = {}

    async def _capture(to, body):
        captured["to"] = to
        captured["body"] = body

    monkeypatch.setattr("app.api.v1.auth.send_sms", _capture)

    req = await async_client.post("/api/v1/auth/otp/request", json={"phone": phone})
    assert req.status_code == 202
    code = re.search(r"\b(\d{6})\b", captured["body"]).group(1)

    verify = await async_client.post(
        "/api/v1/auth/otp/verify", json={"phone": phone, "code": code}
    )
    assert verify.status_code == 200
    assert "access_token" in verify.json()
    assert "refresh_token" in verify.json()


async def test_otp_request_unknown_phone_still_202(async_client: AsyncClient, fake_redis):
    resp = await async_client.post(
        "/api/v1/auth/otp/request", json={"phone": "+14155559999"}
    )
    # Uniform 202 to avoid leaking which numbers are registered.
    assert resp.status_code == 202


async def test_otp_verify_wrong_code_401(async_client: AsyncClient, fake_redis, monkeypatch):
    phone = "+14155550202"
    await _register_with_phone(async_client, phone)
    monkeypatch.setattr("app.api.v1.auth.send_sms", lambda to, body: _noop())

    await async_client.post("/api/v1/auth/otp/request", json={"phone": phone})
    resp = await async_client.post(
        "/api/v1/auth/otp/verify", json={"phone": phone, "code": "000000"}
    )
    assert resp.status_code == 401


async def _noop():
    return None
