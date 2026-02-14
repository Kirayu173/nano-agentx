from pathlib import Path

from nanobot.agent.context import ContextBuilder


def test_build_user_content_uses_suffix_fallback_when_mime_guess_missing(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    image_path = workspace / "sample.webp"
    image_path.write_bytes(b"RIFFxxxxWEBPVP8 payload")

    # Simulate environments where webp MIME is not registered.
    monkeypatch.setattr("nanobot.agent.context.mimetypes.guess_type", lambda _p: (None, None))

    ctx = ContextBuilder(workspace)
    content = ctx._build_user_content("请识别图片内容", [str(image_path)])

    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/webp;base64,")
    assert content[-1] == {"type": "text", "text": "请识别图片内容"}
