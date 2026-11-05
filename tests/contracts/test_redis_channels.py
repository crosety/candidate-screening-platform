import asyncio

from channels_redis.core import RedisChannelLayer

from web.settings import CHANNEL_LAYERS


async def test_idle_channel_stays_connected() -> None:
    # The autouse fixture uses an in-memory layer. Check the real runtime config
    # and a Redis idle interval beyond the library's five-second blocking pop.
    layer = RedisChannelLayer(**CHANNEL_LAYERS["default"]["CONFIG"])
    channel = await layer.new_channel()
    waiting = asyncio.create_task(layer.receive(channel))
    try:
        await asyncio.sleep(5.2)
        await layer.send(channel, {"type": "test.event", "value": "still-connected"})
        assert await asyncio.wait_for(waiting, timeout=2) == {
            "type": "test.event",
            "value": "still-connected",
        }
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await layer.close_pools()
