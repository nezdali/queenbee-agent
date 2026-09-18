import importlib
import sys


def _reload_config(monkeypatch, **env):
    keys = {
        "OPENAI_DEFAULT_MODEL",
        "LLM_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "AZURE_KEYVAULT_URL",
    }
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AZURE_KEYVAULT_URL", "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    sys.modules.pop("config", None)
    import config
    return importlib.reload(config)


def test_documented_model_env_name_takes_precedence(monkeypatch):
    config = _reload_config(
        monkeypatch,
        OPENAI_DEFAULT_MODEL="new-model",
        LLM_MODEL="legacy-model",
    )

    assert config.OPENAI_DEFAULT_MODEL == "new-model"
    assert config.LLM_MODEL == "new-model"


def test_legacy_model_env_name_still_works(monkeypatch):
    config = _reload_config(
        monkeypatch,
        LLM_MODEL="legacy-model",
    )

    assert config.OPENAI_DEFAULT_MODEL == "legacy-model"
    assert config.LLM_MODEL == "legacy-model"


def test_documented_base_url_takes_precedence(monkeypatch):
    config = _reload_config(
        monkeypatch,
        OPENAI_BASE_URL="https://new.example/v1",
        OPENAI_API_BASE="https://legacy.example/v1",
    )

    assert config.OPENAI_BASE_URL == "https://new.example/v1"
    assert config.OPENAI_API_BASE == "https://new.example/v1"


def test_legacy_base_url_still_works(monkeypatch):
    config = _reload_config(
        monkeypatch,
        OPENAI_API_BASE="https://legacy.example/v1",
    )

    assert config.OPENAI_BASE_URL == "https://legacy.example/v1"
    assert config.OPENAI_API_BASE == "https://legacy.example/v1"
