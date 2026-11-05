from datetime import UTC, datetime

from channels.generic.websocket import AsyncJsonWebsocketConsumer


class HealthConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self) -> None:
        await self.accept()
        await self.send_json({"type": "hello", "server_time": datetime.now(UTC).isoformat()})
