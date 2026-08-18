from __future__ import annotations

from snipe_rugg.core.events import SubscriptionKind
from snipe_rugg.core.normalize import normalize
from tests.helpers import raw_message_from_fixture


def test_normalize_logs_notification():
    msg = raw_message_from_fixture("logs_notification.json")
    event = normalize(msg)
    assert event.kind is SubscriptionKind.LOGS
    assert event.slot == 207547741
    assert event.signature == (
        "5h6xBEauJ3PK6SWCZ1PGjBvj8vDdWG3KpwATGy1ARAXFSDwt8GFXM7W5Ncn16wmqokgpiKRLuS83KUxyZyv2sUYv"
    )
    assert event.err is None
    assert event.logs and len(event.logs) == 2
    assert event.latency.chain_slot == 207547741
    assert event.latency.provider_received_at == msg.received_at


def test_normalize_account_notification():
    msg = raw_message_from_fixture("account_notification.json")
    event = normalize(msg)
    assert event.kind is SubscriptionKind.ACCOUNT
    assert event.slot == 207547750
    assert event.signature is None
    assert event.account_data == {
        "lamports": 33594,
        "data": ["", "base64"],
        "owner": "11111111111111111111111111111111",
        "executable": False,
        "rentEpoch": 635,
        "space": 0,
    }


def test_normalize_program_notification():
    msg = raw_message_from_fixture("program_notification.json")
    event = normalize(msg)
    assert event.kind is SubscriptionKind.PROGRAM
    assert event.accounts == ["H4vnBqifaSACnKa7acsxstsY1iV1bvJNxsCY7enrd1hh"]
    assert event.account_data is not None
    assert event.account_data["owner"] == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def test_normalize_slot_notification():
    msg = raw_message_from_fixture("slot_notification.json")
    event = normalize(msg)
    assert event.kind is SubscriptionKind.SLOT
    assert event.slot == 207547740
    assert event.signature is None


def test_normalize_signature_notification():
    msg = raw_message_from_fixture("signature_notification.json")
    event = normalize(msg)
    assert event.kind is SubscriptionKind.SIGNATURE
    assert event.slot == 207547780
    assert event.err is None


def test_normalize_signature_notification_with_error():
    msg = raw_message_from_fixture("signature_notification.json")
    msg = msg.model_copy(update={"payload": {**msg.payload, "value": {"err": {"InstructionError": [0, "Custom"]}}}})
    event = normalize(msg)
    assert event.err == {"InstructionError": [0, "Custom"]}
