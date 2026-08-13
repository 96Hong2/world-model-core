"""고정 계정 간단 로그인 (REVISED §2 · §6 A6).

승인된 소수 내부 사용자 데모 전제다. per-user RBAC 도, 사용자 저장소도 만들지 않는다
(UAA 연동은 V2). 계정이 하는 일은 하나뿐이다 — 어떤 sensitivity 까지 볼 수 있는가.

비밀번호는 데모 고정값이라 저장소에 그대로 적는다. 실제 비밀 정보가 아니고, 감춰 두면
데모 때 어디서 꺼내야 하는지 아무도 모르게 된다. 대신 비교는 상수 시간 함수로 한다.
"""

from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass

from retrieval.access import AccessPolicy

#: 로그인 없이 들어온 요청이 쓰는 계정. 데모 화면과 eval 하네스가 이 경로로 붙는다.
DEFAULT_ACCOUNT_ENV = "BWM_DEFAULT_ACCOUNT"


@dataclass(frozen=True)
class Account:
    username: str
    display_name: str
    policy: AccessPolicy
    demo_password: str


ACCOUNTS: dict[str, Account] = {
    "demo": Account(
        username="demo",
        display_name="데모 (전체 열람)",
        policy=AccessPolicy(
            allowed_sensitivity=frozenset({"public", "internal", "restricted"})
        ),
        demo_password="bwm-demo",
    ),
    "viewer": Account(
        username="viewer",
        display_name="제한 열람 (사내 공개 자료만)",
        policy=AccessPolicy(allowed_sensitivity=frozenset({"public", "internal"})),
        demo_password="bwm-viewer",
    ),
}


def default_account() -> Account:
    name = os.getenv(DEFAULT_ACCOUNT_ENV, "demo")
    return ACCOUNTS.get(name, ACCOUNTS["demo"])


def authenticate(username: str, password: str) -> Account | None:
    # 바이트로 비교한다. compare_digest 는 비 ASCII 문자열을 그대로 받으면 예외를 던진다.
    given = (password or "").encode("utf-8")
    account = ACCOUNTS.get(username or "")
    if account is None:
        # 존재하지 않는 계정도 같은 비용을 쓰게 해서 사용자명 유무가 새지 않게 한다.
        hmac.compare_digest(given, b"")
        return None
    if hmac.compare_digest(given, account.demo_password.encode("utf-8")):
        return account
    return None


class TokenStore:
    """메모리 토큰 발급기. 프로세스가 죽으면 함께 사라진다(데모 전제)."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}

    def issue(self, account: Account) -> str:
        token = secrets.token_urlsafe(24)
        self._tokens[token] = account.username
        return token

    def resolve(self, token: str | None) -> Account | None:
        if not token:
            return None
        username = self._tokens.get(token)
        return ACCOUNTS.get(username) if username else None

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)
