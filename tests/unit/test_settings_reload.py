from __future__ import annotations

import importlib


def test_reload_settings_refreshes_imported_references(monkeypatch):
    """reload 必须原地刷新：已 import 的引用不能停留在旧对象上。"""
    # 包 __init__ 把 settings 这个名字导出成了实例，必须按模块路径取模块本身。
    settings_module = importlib.import_module("src.infrastructure.config.settings")

    class FakeAppSettings:
        def __init__(self):
            self.web_username = "fresh-user"
            self.server_port = 9000

    class FakeAISettings:
        def __init__(self):
            self.model_name = "fresh-model"

    class FakeNotificationSettings:
        def __init__(self):
            self.bark_url = "fresh-bark"

    class FakeScraperSettings:
        def __init__(self):
            self.run_headless = False

    monkeypatch.setattr(settings_module, "AppSettings", FakeAppSettings)
    monkeypatch.setattr(settings_module, "AISettings", FakeAISettings)
    monkeypatch.setattr(settings_module, "NotificationSettings", FakeNotificationSettings)
    monkeypatch.setattr(settings_module, "ScraperSettings", FakeScraperSettings)
    monkeypatch.setattr("dotenv.load_dotenv", lambda **_: None)

    imported = {
        name: getattr(settings_module, name)
        for name in (
            "settings",
            "ai_settings",
            "notification_settings",
            "scraper_settings",
        )
    }
    snapshots = {name: dict(obj.__dict__) for name, obj in imported.items()}

    try:
        settings_module.reload_settings()

        # 引用身份不变，但值已刷新
        assert settings_module.settings is imported["settings"]
        assert imported["settings"].web_username == "fresh-user"
        assert imported["ai_settings"].model_name == "fresh-model"
        assert imported["scraper_settings"].run_headless is False
    finally:
        for name, snapshot in snapshots.items():
            obj = imported[name]
            obj.__dict__.clear()
            obj.__dict__.update(snapshot)
