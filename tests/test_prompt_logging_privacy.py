from handlers import utils


def test_prompt_logging_disabled_by_default(tmp_path, monkeypatch):
    log_file = tmp_path / "prompt_log.jsonl"
    monkeypatch.setattr(utils, "_PROMPT_LOG_FILE", log_file)
    monkeypatch.setattr(utils, "PROMPT_LOG_ENABLED", False)

    utils._log_prompt(123, "alice", "Alice", "secret token abc123")

    assert not log_file.exists()


def test_prompt_logging_writes_when_explicitly_enabled(tmp_path, monkeypatch):
    log_file = tmp_path / "prompt_log.jsonl"
    monkeypatch.setattr(utils, "_PROMPT_LOG_FILE", log_file)
    monkeypatch.setattr(utils, "PROMPT_LOG_ENABLED", True)

    utils._log_prompt(123, "alice", "Alice", "hello")

    content = log_file.read_text(encoding="utf-8")
    assert '"user_id": 123' in content
    assert '"message": "hello"' in content
