import pytest

from fi_mcp import queries
from fi_mcp.client import FiClient, FiLoginError, FiNotAuthorized

from .fake_fi import FakeFi


def _client(fake: FakeFi) -> FiClient:
    return FiClient("dog@example.com", "pw", transport=fake.transport())


async def test_logs_in_once_and_caches():
    fake = FakeFi()
    c = _client(fake)
    await c.query(queries.HOUSEHOLD)
    await c.query(queries.HOUSEHOLD)
    assert fake.logins == 1
    assert fake.queries == ["FiHousehold"]


async def test_relogs_in_when_session_expires():
    fake = FakeFi(expire_session_once=True)
    data = await _client(fake).query(queries.HOUSEHOLD)
    assert data["currentUser"]["id"] == "u1"
    assert fake.logins == 2


async def test_bad_password_raises_login_error():
    with pytest.raises(FiLoginError, match="email or password is wrong"):
        await _client(FakeFi(bad_password=True)).query(queries.HOUSEHOLD)


async def test_refused_query_raises_not_authorized_after_one_retry():
    fake = FakeFi(refuse_trends=True)
    with pytest.raises(FiNotAuthorized):
        await _client(fake).query(queries.HEALTH_TRENDS, {"petId": "pet-1", "period": "DAY"})
    assert fake.logins == 2
