"""Resolve only verified transport availability for the current application."""

from asgiref.sync import sync_to_async

from screening.application.channel_context import channel_note
from screening.application.ports import SessionRecord
from screening.domain.errors import NotAllowed


async def context_for(session: SessionRecord, channel: str) -> str:
    from web.conversations.eleven_direct import enabled
    from web.conversations.sms_links import continuation_enabled, sms_configuration
    from web.conversations.telephone import call_capacity, configuration, voice_ready

    browser = await enabled(session)
    try:
        config = await sync_to_async(configuration)(session.tenant_id)
        phone = await sync_to_async(voice_ready)(
            session.tenant_id, session.id
        ) and await sync_to_async(call_capacity)(
            session.tenant_id, config, application_id=session.application_id
        )
    except NotAllowed:
        phone = False
    try:
        await sync_to_async(sms_configuration)(session.tenant_id)
        sms = True
    except NotAllowed:
        sms = False
    return channel_note(
        channel,
        browser_voice=browser,
        telephone=phone,
        sms_link=sms,
        after_call_sms=sms and await sync_to_async(continuation_enabled)(session),
    )
