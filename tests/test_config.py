from app.config import Settings


def test_admin_ids_are_parsed(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("ADMIN_IDS", "123, 456")
    monkeypatch.setenv("SAM_API_BASE", "")
    monkeypatch.setenv("SAM_API_KEY", "")

    settings = Settings()

    assert settings.admin_ids == {123, 456}


def test_empty_admin_ids(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("ADMIN_IDS", "")
    monkeypatch.setenv("SAM_API_BASE", "")
    monkeypatch.setenv("SAM_API_KEY", "")

    settings = Settings()

    assert settings.admin_ids == set()
