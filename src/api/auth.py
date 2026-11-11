"""Web 认证辅助。

登录成功后写入 HttpOnly Cookie，后续 /api/* 与 /ws 请求凭该 Cookie 校验。
令牌由用户名/密码确定性地派生，服务重启后仍然有效，无需会话存储。
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from src.infrastructure.config.settings import settings

AUTH_COOKIE_NAME = "goofish_auth"


def build_auth_token(username: str, password: str) -> str:
    """由账号密码派生认证令牌（不使用明文密码作为 Cookie）。"""
    return hmac.new(
        str(password or "").encode("utf-8"),
        f"ai-goofish:{username or ''}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def expected_auth_token() -> str:
    return build_auth_token(settings.web_username, settings.web_password)


def is_authenticated_token(token: Optional[str]) -> bool:
    if not token:
        return False
    return hmac.compare_digest(token, expected_auth_token())
