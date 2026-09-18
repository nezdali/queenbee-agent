import json

from services import strava_service


def _write_token(path, access_token):
    path.write_text(
        json.dumps({
            "access_token": access_token,
            "refresh_token": "refresh",
            "expires_at": 9999999999,
        }),
        encoding="utf-8",
    )


def test_user_without_token_does_not_inherit_legacy_shared_token(tmp_path, monkeypatch):
    legacy = tmp_path / "strava_token.json"
    _write_token(legacy, "legacy-secret")

    monkeypatch.setattr(strava_service, "STRAVA_TOKEN_FILE", str(legacy))
    monkeypatch.setattr(strava_service, "STRAVA_TOKEN_DIR", str(tmp_path))
    monkeypatch.setattr(strava_service, "STRAVA_LEGACY_USER_ID", 0)

    assert strava_service._load_token(123) is None


def test_only_explicit_legacy_owner_can_use_shared_token(tmp_path, monkeypatch):
    legacy = tmp_path / "strava_token.json"
    _write_token(legacy, "legacy-secret")

    monkeypatch.setattr(strava_service, "STRAVA_TOKEN_FILE", str(legacy))
    monkeypatch.setattr(strava_service, "STRAVA_TOKEN_DIR", str(tmp_path))
    monkeypatch.setattr(strava_service, "STRAVA_LEGACY_USER_ID", 123)

    owner = strava_service._load_token(123)
    other = strava_service._load_token(456)

    assert owner["access_token"] == "legacy-secret"
    assert other is None


def test_per_user_token_takes_precedence_over_legacy_token(tmp_path, monkeypatch):
    legacy = tmp_path / "strava_token.json"
    per_user = tmp_path / "strava_token_123.json"
    _write_token(legacy, "legacy-secret")
    _write_token(per_user, "user-secret")

    monkeypatch.setattr(strava_service, "STRAVA_TOKEN_FILE", str(legacy))
    monkeypatch.setattr(strava_service, "STRAVA_TOKEN_DIR", str(tmp_path))
    monkeypatch.setattr(strava_service, "STRAVA_LEGACY_USER_ID", 123)

    token = strava_service._load_token(123)

    assert token["access_token"] == "user-secret"


def test_legacy_single_user_call_still_reads_legacy_file(tmp_path, monkeypatch):
    legacy = tmp_path / "strava_token.json"
    _write_token(legacy, "legacy-secret")

    monkeypatch.setattr(strava_service, "STRAVA_TOKEN_FILE", str(legacy))
    monkeypatch.setattr(strava_service, "STRAVA_TOKEN_DIR", str(tmp_path))
    monkeypatch.setattr(strava_service, "STRAVA_LEGACY_USER_ID", 0)

    token = strava_service._load_token(None)

    assert token["access_token"] == "legacy-secret"
