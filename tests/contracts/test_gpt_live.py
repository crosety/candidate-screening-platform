import asyncio
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock

from adapters.channels.gpt_live import (
    BACKEND_PENDING,
    INSTRUCTIONS,
    OPENING_INSTRUCTIONS,
    VOICE_DESCRIPTION,
    GptLiveBridge,
)


def bridge() -> GptLiveBridge:
    result = GptLiveBridge(AsyncMock(), AsyncMock(return_value="Respuesta aprobada"), AsyncMock())
    result.connection = SimpleNamespace(
        session=SimpleNamespace(
            commentary=SimpleNamespace(append=AsyncMock()),
            input_audio=SimpleNamespace(append=AsyncMock()),
        )
    )
    return result


async def user(b: GptLiveBridge, text: str) -> None:
    await b.handle({"type": "session.input_transcript.delta", "delta": text})
    b.last_transcript = monotonic() - 1


async def delegated(b: GptLiveBridge, identity: str = "delegation_one") -> None:
    await b.handle(
        {"type": "session.delegation.created", "delegation": {"id": identity, "target": "client"}}
    )


def test_spanish_voice_and_interviewer_opening_preserve_history() -> None:
    b = bridge()
    b.voice_id = "voice_test_es"
    history = [
        {"role": "user", "content": [{"type": "input_text", "text": "Soy Elena"}]},
        {"role": "assistant", "content": [{"type": "output_text", "text": "¿Tienes carnet?"}]},
    ]
    config = b.session_config(history)
    assert config["audio"]["output"]["voice"] == {"id": "voice_test_es"}
    assert VOICE_DESCRIPTION in config["instructions"]
    seeded = list(config["input"])
    assert seeded[:-1] == history and len(history) == 2
    assert seeded[-1] == {
        "role": "developer",
        "content": [{"type": "input_text", "text": b.opening}],
    }
    assert "EXACTAMENTE esta pregunta autorizada" in b.opening
    assert '"¿Tienes carnet?"' in b.opening
    assert "Habla primero" in OPENING_INSTRUCTIONS
    assert "última pregunta pendiente" in OPENING_INSTRUCTIONS
    assert "No vuelvas a pedir datos ya respondidos" in OPENING_INSTRUCTIONS


async def test_opening_names_only_the_last_approved_question() -> None:
    b = bridge()
    b.session_config(
        [
            {"role": "assistant", "content": [{"type": "output_text", "text": "¿Tu nombre?"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "Ana. ¿Me pides dinero?"}]},
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Gracias. ¿Dónde vives?"}],
            },
        ]
    )
    await b.handle({"type": "session.started"})
    sent = b.connection.session.commentary.append.await_args.kwargs["content"]
    assert '"¿Dónde vives?"' in sent
    assert "¿Tu nombre?" not in sent and "dinero" not in sent
    b.session_config(
        [{"role": "user", "content": [{"type": "input_text", "text": "¿Pide dinero?"}]}]
    )
    assert "dinero" not in b.opening


async def test_opening_sent_once_after_ready_without_backend_action() -> None:
    b = bridge()
    await b.handle({"type": "session.started"})
    await b.handle({"type": "session.started"})
    assert b.ready.is_set()
    b.connection.session.commentary.append.assert_awaited_once()
    sent = b.connection.session.commentary.append.await_args.kwargs
    assert sent["content"] == OPENING_INSTRUCTIONS
    assert sent["delegation_id"] is None and sent["event_id"]
    b.delegate.assert_not_awaited()


async def test_opening_does_not_interrupt_candidate_or_closed_call() -> None:
    for stopped in [False, True]:
        b = bridge()
        if stopped:
            b.stop()
        else:
            await user(b, "Mi respuesta es...")
        await b.handle({"type": "session.started"})
        b.connection.session.commentary.append.assert_not_awaited()


async def test_audio_is_direct_and_full_duplex_while_backend_waits() -> None:
    b = bridge()
    b.ready.set()
    ready = asyncio.Event()

    async def backend(text: str, identity: str) -> str:
        await ready.wait()
        return "Salida validada"

    b.delegate = backend
    await user(b, "Mi certificado es DEMO-1")
    await delegated(b)
    await asyncio.sleep(0)
    assert b.connection.session.commentary.append.await_args.kwargs["content"] == BACKEND_PENDING
    assert not ready.is_set()
    await b.audio("AAAA")
    await b.handle({"type": "session.output_audio.delta", "delta": "AQACAA=="})
    b.connection.session.input_audio.append.assert_awaited_once_with(audio="AAAA")
    b.emit.assert_any_await({"type": "audio", "delta": "AQACAA=="})
    ready.set()
    await asyncio.gather(*b.tasks)
    assert b.connection.session.commentary.append.await_args.kwargs["content"] == "Salida validada"
    assert b.connection.session.commentary.append.await_count == 2


def test_prompt_separates_immediate_acknowledgment_from_backend_validation() -> None:
    assert "Backchannel policy:" in INSTRUCTIONS
    assert "Interruption policy:" in INSTRUCTIONS
    assert "Delegation policy:" in INSTRUCTIONS
    assert "Generate <|SpawnThinking|>" in INSTRUCTIONS
    assert "sin esperar al agente ni al guardián" in INSTRUCTIONS
    assert "EN PARALELO" in INSTRUCTIONS
    assert "NO validación" in INSTRUCTIONS


async def test_fragments_are_joined_and_duplicate_delegation_does_not_repeat() -> None:
    b = bridge()
    await user(b, "Hola, soy ")
    await user(b, "Ana Prueba")
    await delegated(b)
    await delegated(b)
    await asyncio.gather(*b.tasks)
    b.delegate.assert_awaited_once_with("Hola, soy Ana Prueba", "delegation_one")
    assert b.connection.session.commentary.append.await_count == 2


async def test_interruption_suppresses_stale_reply_without_cancelling_backend() -> None:
    b = bridge()
    ready = asyncio.Event()
    finished = []

    async def backend(text: str, identity: str) -> str:
        await ready.wait()
        finished.append(text)
        return "Respuesta a petición anterior"

    b.delegate = backend
    await user(b, "Primero")
    await delegated(b)
    await asyncio.sleep(0)
    await user(b, "Mejor otra cosa")
    ready.set()
    await asyncio.gather(*b.tasks)
    assert finished == ["Primero"]
    b.connection.session.commentary.append.assert_awaited_once()
    assert b.connection.session.commentary.append.await_args.kwargs["content"] == BACKEND_PENDING
    assert b.pending == "Mejor otra cosa"


async def test_usage_is_cumulative_not_sum_and_finalization_is_explicit() -> None:
    b = bridge()
    for seconds in [2, 4, 3]:
        await b.handle({"type": "session.usage.updated", "usage": {"seconds": seconds}})
    assert b.usage_seconds == 4 and not b.finalized
    await b.handle({"type": "session.closed", "usage": {"seconds": 5}, "reason": "close_requested"})
    assert b.usage_seconds == 5 and b.finalized and b.stopping.is_set()


async def test_closed_call_never_delivers_pending_commentary() -> None:
    b = bridge()
    b.stop()
    await user(b, "Hola")
    await delegated(b)
    await asyncio.gather(*b.tasks)
    b.delegate.assert_not_awaited()
    b.connection.session.commentary.append.assert_not_awaited()


async def test_backend_exception_closes_call_without_exposing_content() -> None:
    b = bridge()
    b.delegate.side_effect = ValueError("private candidate data or key")
    await user(b, "Hola")
    await delegated(b)
    await asyncio.gather(*b.tasks)
    assert b.stopping.is_set() and b.close_reason == "backend_error"
    b.connection.session.commentary.append.assert_awaited_once()
    assert b.connection.session.commentary.append.await_args.kwargs["content"] == BACKEND_PENDING
    assert "private" not in str(b.emit.mock_calls)
