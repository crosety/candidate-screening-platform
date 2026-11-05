import asyncio
from uuid import uuid4

import pytest
from django.db import connection
from psycopg.conninfo import make_conninfo

from adapters.persistence.turn_lock import PostgresTurnLock

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def lock() -> PostgresTurnLock:
    db = connection.settings_dict
    return PostgresTurnLock(
        make_conninfo(
            dbname=db["NAME"],
            user=db["USER"],
            password=db["PASSWORD"],
            host=db["HOST"],
            port=db["PORT"],
        )
    )


@pytest.mark.asyncio
async def test_independent_connections_serialize_the_same_session(lock: PostgresTurnLock) -> None:
    tenant, session = uuid4(), uuid4()
    entered = asyncio.Event()

    async def other_process() -> None:
        async with lock.hold(tenant, session):
            entered.set()

    async with lock.hold(tenant, session):
        pending = asyncio.create_task(other_process())
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(entered.wait(), 0.05)
        async with lock.hold(tenant, uuid4()):
            assert not entered.is_set()
    await asyncio.wait_for(pending, 2)
    assert entered.is_set()
