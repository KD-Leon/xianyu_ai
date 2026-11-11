from __future__ import annotations

from fastapi.testclient import TestClient

import src.app as app_module


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(app_module.app_settings, "web_auth_enabled", True)
    return TestClient(app_module.app)


def test_api_requires_login(monkeypatch):
    client = _client(monkeypatch)

    assert client.get("/api/does-not-exist").status_code == 401
    # 健康检查必须保持公开
    assert client.get("/health").status_code == 200


def test_login_sets_cookie_and_logout_clears_it(monkeypatch):
    client = _client(monkeypatch)
    credentials = {
        "username": app_module.app_settings.web_username,
        "password": app_module.app_settings.web_password,
    }

    wrong = {**credentials, "password": "not-the-right-password"}
    assert client.post("/auth/status", json=wrong).status_code == 401

    assert client.post("/auth/status", json=credentials).status_code == 200
    # 通过认证后不再是 401（该路径不存在，所以是 404）
    assert client.get("/api/does-not-exist").status_code == 404

    assert client.post("/auth/logout").status_code == 200
    assert client.get("/api/does-not-exist").status_code == 401


def test_login_supports_non_ascii_credentials(monkeypatch):
    monkeypatch.setattr(app_module.app_settings, "web_auth_enabled", True)
    monkeypatch.setattr(app_module.app_settings, "web_username", "管理员")
    monkeypatch.setattr(app_module.app_settings, "web_password", "中文密码123")
    client = TestClient(app_module.app)

    response = client.post(
        "/auth/status",
        json={"username": "管理员", "password": "中文密码123"},
    )

    assert response.status_code == 200
    assert client.get("/api/does-not-exist").status_code == 404
