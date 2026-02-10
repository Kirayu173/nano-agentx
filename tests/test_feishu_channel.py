import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels import feishu as feishu_module
from nanobot.channels.feishu import FeishuChannel
from nanobot.config.schema import FeishuConfig


class _FakeResponse:
    def __init__(self, ok: bool = True, code: int = 0, msg: str = "ok", data=None) -> None:
        self._ok = ok
        self.code = code
        self.msg = msg
        self.data = data

    def success(self) -> bool:
        return self._ok

    def get_log_id(self) -> str:
        return "log_test"


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.image_requests = []
        self.file_requests = []
        self.message_requests = []
        self.image_responses: list[_FakeResponse] = []
        self.file_responses: list[_FakeResponse] = []
        self.message_responses: list[_FakeResponse] = []

        self.im = SimpleNamespace(
            v1=SimpleNamespace(
                image=SimpleNamespace(create=self._create_image),
                file=SimpleNamespace(create=self._create_file),
                message=SimpleNamespace(create=self._create_message),
            )
        )

    def _create_image(self, request):
        self.image_requests.append(request)
        if self.image_responses:
            return self.image_responses.pop(0)
        key = f"img_{len(self.image_requests)}"
        return _FakeResponse(data=SimpleNamespace(image_key=key))

    def _create_file(self, request):
        self.file_requests.append(request)
        if self.file_responses:
            return self.file_responses.pop(0)
        key = f"file_{len(self.file_requests)}"
        return _FakeResponse(data=SimpleNamespace(file_key=key))

    def _create_message(self, request):
        self.message_requests.append(request)
        if self.message_responses:
            return self.message_responses.pop(0)
        return _FakeResponse()


def _make_channel(client: _FakeFeishuClient) -> FeishuChannel:
    channel = FeishuChannel(
        config=FeishuConfig(enabled=True, app_id="cli_test", app_secret="secret_test"),
        bus=MessageBus(),
    )
    channel._client = client
    return channel


@pytest.mark.asyncio
async def test_send_text_only_uses_interactive_message() -> None:
    client = _FakeFeishuClient()
    channel = _make_channel(client)

    await channel.send(OutboundMessage(channel="feishu", chat_id="ou_test", content="hello"))

    assert len(client.image_requests) == 0
    assert len(client.file_requests) == 0
    assert len(client.message_requests) == 1
    assert client.message_requests[0].request_body.msg_type == "interactive"


@pytest.mark.asyncio
async def test_send_png_uploads_image_then_sends_text(tmp_path: Path) -> None:
    client = _FakeFeishuClient()
    channel = _make_channel(client)
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\npayload")

    await channel.send(
        OutboundMessage(
            channel="feishu",
            chat_id="ou_test",
            content="uploaded",
            media=[str(image_path)],
        )
    )

    assert len(client.image_requests) == 1
    assert len(client.file_requests) == 0
    assert [r.request_body.msg_type for r in client.message_requests] == ["image", "interactive"]
    image_content = json.loads(client.message_requests[0].request_body.content)
    assert image_content["image_key"] == "img_1"


@pytest.mark.asyncio
async def test_send_non_image_uploads_file_then_sends_text(tmp_path: Path) -> None:
    client = _FakeFeishuClient()
    channel = _make_channel(client)
    file_path = tmp_path / "notes.txt"
    file_path.write_text("hello")

    await channel.send(
        OutboundMessage(
            channel="feishu",
            chat_id="ou_test",
            content="done",
            media=[str(file_path)],
        )
    )

    assert len(client.image_requests) == 0
    assert len(client.file_requests) == 1
    assert client.file_requests[0].request_body.file_type == "stream"
    assert client.file_requests[0].request_body.file_name == "notes.txt"
    assert [r.request_body.msg_type for r in client.message_requests] == ["file", "interactive"]


@pytest.mark.asyncio
async def test_oversized_image_falls_back_to_file_upload(tmp_path: Path) -> None:
    client = _FakeFeishuClient()
    channel = _make_channel(client)
    channel._MAX_IMAGE_BYTES = 1

    image_path = tmp_path / "big.png"
    image_path.write_bytes(b"12345")

    await channel.send(
        OutboundMessage(
            channel="feishu",
            chat_id="ou_test",
            content="fallback",
            media=[str(image_path)],
        )
    )

    assert len(client.image_requests) == 0
    assert len(client.file_requests) == 1
    assert [r.request_body.msg_type for r in client.message_requests] == ["file", "interactive"]


@pytest.mark.asyncio
async def test_attachment_failure_does_not_block_following_messages(tmp_path: Path) -> None:
    client = _FakeFeishuClient()
    client.image_responses = [
        _FakeResponse(ok=False, code=234001, msg="Invalid request"),
        _FakeResponse(ok=True, data=SimpleNamespace(image_key="img_ok")),
    ]
    channel = _make_channel(client)

    first = tmp_path / "a.png"
    first.write_bytes(b"\x89PNG\r\n\x1a\n1")
    second = tmp_path / "b.png"
    second.write_bytes(b"\x89PNG\r\n\x1a\n2")

    await channel.send(
        OutboundMessage(
            channel="feishu",
            chat_id="ou_test",
            content="still send text",
            media=[str(first), str(second)],
        )
    )

    assert len(client.image_requests) == 2
    assert len(client.file_requests) == 0
    # First image failed so only one image message is sent; text still goes out.
    assert [r.request_body.msg_type for r in client.message_requests] == ["image", "interactive"]


@pytest.mark.skipif(not feishu_module.FEISHU_AVAILABLE, reason="lark-oapi not installed")
def test_feishu_sdk_available_for_upload_requests() -> None:
    # Guard test to ensure import branch with upload request classes is active.
    assert feishu_module.FEISHU_AVAILABLE is True
