import pytest

from screening.application.channel_context import asks_for_call, channel_note


@pytest.mark.parametrize("text", ["Llámame", "¿Puedes llamarme?", "llámame por favor", "call me"])
def test_call_request(text):
    assert asks_for_call(text)


@pytest.mark.parametrize(
    "text",
    [
        "no me llames",
        "No quiero que me llames",
        'el mensaje dice "llámame"',
        "llama al 112",
        "Hola",
    ],
)
def test_no_false_call_request(text):
    assert not asks_for_call(text)


def test_sms_is_not_chat_and_actions_not_promised():
    note = channel_note("telephone", sms_link=True, after_call_sms=True)
    assert '"sms_replies": false' in note
    assert '"ending_voice_does_not_complete_application": true' in note
    assert '"telephone": false' in note
    assert "no afirmes que ya se ha entregado" in note
