import json

import pytest

from core import tool_factory


def _meta(name="demo", version=1):
    return tool_factory.ToolMeta(
        name=name,
        description="Demo",
        requires_google_auth=False,
        trigger_keywords=["demo"],
        created_at="2026-01-01T00:00:00+00:00",
        version=version,
    )


def test_save_tool_writes_code_and_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_factory, "TOOLS_DIR", tmp_path)

    tool_factory.save_tool(
        _meta(),
        "async def run(context: dict) -> str:\n    return 'ok'\n",
    )

    assert (tmp_path / "demo.py").read_text(encoding="utf-8").endswith("return 'ok'\n")
    data = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    assert data["name"] == "demo"
    assert not (tmp_path / ".demo.py.tmp").exists()
    assert not (tmp_path / ".demo.json.tmp").exists()


def test_save_tool_rolls_back_existing_pair_on_replace_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_factory, "TOOLS_DIR", tmp_path)

    old_code = "async def run(context: dict) -> str:\n    return 'old'\n"
    tool_factory.save_tool(_meta(version=1), old_code)

    real_replace = tool_factory.os.replace
    calls = {"count": 0}

    def _replace(src, dst):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated manifest replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(tool_factory.os, "replace", _replace)

    with pytest.raises(OSError, match="simulated"):
        tool_factory.save_tool(
            _meta(version=2),
            "async def run(context: dict) -> str:\n    return 'new'\n",
        )

    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == old_code
    data = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert not (tmp_path / ".demo.py.tmp").exists()
    assert not (tmp_path / ".demo.json.tmp").exists()
