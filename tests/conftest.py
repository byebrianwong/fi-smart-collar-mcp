import pytest

from fi_mcp import settings


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    """Point every test at an empty temp settings file, never the real one.

    Tests that need settings write to the returned path.
    """
    file = tmp_path / "settings.toml"
    monkeypatch.setenv(settings.ENV_VAR, str(file))
    return file
