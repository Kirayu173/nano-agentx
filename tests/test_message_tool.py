import pytest

from nanobot.agent.tools.message import MessageTool
from nanobot.bus.events import OutboundMessage


@pytest.mark.asyncio
async def test_message_tool_forwards_media_paths() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send, default_channel="feishu", default_chat_id="ou_test")
    result = await tool.execute(content="hello", media=["workspace/screenshots/a.png"])

    assert "Message sent to feishu:ou_test" == result
    assert len(sent) == 1
    assert sent[0].content == "hello"
    assert sent[0].media == ["workspace/screenshots/a.png"]


@pytest.mark.asyncio
async def test_message_tool_defaults_media_to_empty_list() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send, default_channel="feishu", default_chat_id="ou_test")
    await tool.execute(content="hello")

    assert len(sent) == 1
    assert sent[0].media == []
