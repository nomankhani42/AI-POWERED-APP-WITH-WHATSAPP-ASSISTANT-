"""RedisSession round-trip tests using a fake in-memory Redis (T029)."""

from fakeredis import FakeAsyncRedis

from app.agent.session import RedisSession


def _session() -> RedisSession:
    return RedisSession("conv-test", redis=FakeAsyncRedis(), ttl=60)


async def test_add_and_get_items() -> None:
    s = _session()
    assert await s.get_items() == []

    await s.add_items([{"role": "user", "content": "hi"}])
    await s.add_items([{"role": "assistant", "content": "hello"}])

    items = await s.get_items()
    assert items == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


async def test_get_items_limit() -> None:
    s = _session()
    await s.add_items([{"n": 1}, {"n": 2}, {"n": 3}])
    assert await s.get_items(limit=2) == [{"n": 2}, {"n": 3}]


async def test_pop_item() -> None:
    s = _session()
    await s.add_items([{"n": 1}, {"n": 2}])
    assert await s.pop_item() == {"n": 2}
    assert await s.get_items() == [{"n": 1}]


async def test_pop_empty_returns_none() -> None:
    s = _session()
    assert await s.pop_item() is None


async def test_clear_session() -> None:
    s = _session()
    await s.add_items([{"n": 1}])
    await s.clear_session()
    assert await s.get_items() == []
