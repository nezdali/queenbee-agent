import json

from core import user_roles


def test_set_roles_normalizes_and_persists(tmp_path, monkeypatch):
    roles_file = tmp_path / "roles.json"
    monkeypatch.setenv("USER_ROLES_FILE", str(roles_file))

    import config
    monkeypatch.setattr(config, "save_secret_to_keyvault", lambda *args, **kwargs: False)

    record = user_roles.set_roles(
        123,
        [" Finance ", "ADMIN"],
        username="@Alice",
    )

    assert record == {
        "roles": ["public", "finance", "admin"],
        "username": "alice",
    }
    assert user_roles.get_roles(123) == ["public", "finance", "admin"]

    raw = json.loads(roles_file.read_text(encoding="utf-8"))
    assert raw["123"]["username"] == "alice"


def test_remove_user_returns_true_once(tmp_path, monkeypatch):
    roles_file = tmp_path / "roles.json"
    monkeypatch.setenv("USER_ROLES_FILE", str(roles_file))

    import config
    monkeypatch.setattr(config, "save_secret_to_keyvault", lambda *args, **kwargs: False)

    user_roles.set_roles(456, ["public"], username="bob")

    assert user_roles.remove_user(456) is True
    assert user_roles.remove_user(456) is False
    assert user_roles.get_roles(456) == ["public"]
