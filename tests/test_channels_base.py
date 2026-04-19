from mopedzoomd.channels.base import ApprovalButton, InboundMessage, OutboundMessage


def test_inbound_message_shape():
    m = InboundMessage(
        channel="telegram",
        user_ref="chat:1",
        text="hi",
        reply_to_ref=None,
        raw={},
    )
    assert m.channel == "telegram"


def test_outbound_approval_options():
    o = OutboundMessage(
        task_id=5,
        body="approve?",
        buttons=[ApprovalButton("approve", "Approve")],
        channel_ref=None,
    )
    assert o.buttons[0].callback == "approve"
