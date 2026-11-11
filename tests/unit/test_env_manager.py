from src.infrastructure.config.env_manager import EnvManager


def test_get_value_prefers_env_file_when_key_present(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("WEBHOOK_URL=https://hooks.example.com/new\n", encoding="utf-8")
    monkeypatch.setenv("WEBHOOK_URL", "https://hooks.example.com/old")

    manager = EnvManager(str(env_file))

    assert manager.get_value("WEBHOOK_URL") == "https://hooks.example.com/new"


def test_get_value_falls_back_to_runtime_when_key_missing_from_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("WEBHOOK_URL", "https://hooks.example.com/runtime")

    manager = EnvManager(str(env_file))

    assert manager.get_value("WEBHOOK_URL") == "https://hooks.example.com/runtime"


def test_set_value_writes_atomically_and_leaves_no_tmp_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\n", encoding="utf-8")
    manager = EnvManager(str(env_file))

    assert manager.set_value("B", "2") is True

    assert env_file.read_text(encoding="utf-8") == "A=1\nB=2\n"
    assert not (tmp_path / ".env.tmp").exists()
