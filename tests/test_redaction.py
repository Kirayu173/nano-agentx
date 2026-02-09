from pathlib import Path

from nanobot.utils.redaction import SensitiveOutputRedactor


def test_redacts_absolute_paths_windows_and_unix() -> None:
    workspace = (Path.cwd() / "workspace").resolve()
    redactor = SensitiveOutputRedactor(
        workspace=workspace,
        config_path=Path.home() / ".nanobot" / "config.json",
    )

    text = (
        f"workspace={workspace}\n"
        "win=C:\\Users\\Alice\\AppData\\Roaming\\.nanobot\\config.json\n"
        "unix=/home/alice/.nanobot/config.json"
    )

    redacted = redactor.redact(text)
    assert str(workspace) not in redacted
    assert "C:\\Users\\Alice\\AppData\\Roaming\\.nanobot\\config.json" not in redacted
    assert "/home/alice/.nanobot/config.json" not in redacted
    assert SensitiveOutputRedactor.PATH_PLACEHOLDER in redacted


def test_redacts_private_endpoints() -> None:
    redactor = SensitiveOutputRedactor()
    text = (
        "http://127.0.0.1:8000/v1 and ws://localhost:3001/socket "
        "and 192.168.1.23:9000"
    )
    redacted = redactor.redact(text)
    assert "127.0.0.1" not in redacted
    assert "localhost:3001" not in redacted
    assert "192.168.1.23:9000" not in redacted
    assert redacted.count(SensitiveOutputRedactor.ENDPOINT_PLACEHOLDER) >= 3


def test_redacts_secrets_and_tokens() -> None:
    redactor = SensitiveOutputRedactor(extra_secrets=["my-very-secret-value"])
    text = (
        '{"api_key":"sk-live-secret-value-123456","token":"abcdef123456"} '
        "Authorization: Bearer abcdefghijklmnopqr "
        "Slack xoxb-1234567890-secret-token and my-very-secret-value"
    )
    redacted = redactor.redact(text)
    assert "sk-live-secret-value-123456" not in redacted
    assert "abcdef123456" not in redacted
    assert "Bearer abcdefghijklmnopqr" not in redacted
    assert "xoxb-1234567890-secret-token" not in redacted
    assert "my-very-secret-value" not in redacted
    assert SensitiveOutputRedactor.SECRET_PLACEHOLDER in redacted


def test_regular_text_unchanged_when_no_match() -> None:
    redactor = SensitiveOutputRedactor()
    text = "The build is healthy. No credentials or local paths here."
    assert redactor.redact(text) == text


def test_redaction_can_be_disabled() -> None:
    redactor = SensitiveOutputRedactor(enabled=False)
    text = "Chat ID: 12345 and token: sk-abcdef123456"
    assert redactor.redact(text) == text
