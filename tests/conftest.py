import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

os.environ.setdefault("DISCORD_TOKEN", "test-discord-token")
os.environ.setdefault("RIOT_API_KEY", "test-riot-api-key")
os.environ.setdefault("DISCORD_CHANNEL", "123456789")
os.environ.setdefault("REQUESTS", "100")
os.environ.setdefault("DAILY", "21")


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="{}", headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


def blocked_network_get(url, *args, **kwargs):
    if "ddragon.leagueoflegends.com/api/versions.json" in url:
        return FakeResponse(payload=["15.9.1"])
    raise RuntimeError(f"Unexpected network call during tests: {url}")


import requests  # noqa: E402

requests.get = blocked_network_get


def reset_storage_module():
    import storage

    engine = getattr(storage, "_engine", None)
    if engine is not None:
        engine.dispose()
    storage._engine = None
    storage._engine_url = None
    storage._metadata = None
    storage._tables = None
    storage._initialized = False
    return storage


@pytest.fixture
def no_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AUTO_MIGRATE_JSON", "false")
    yield reset_storage_module()
    reset_storage_module()


@pytest.fixture
def postgres_storage(monkeypatch):
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL storage tests.")

    monkeypatch.setenv("AUTO_MIGRATE_JSON", "false")
    storage = reset_storage_module()
    storage.initialize_storage()
    engine, metadata, _tables = storage._ensure_engine()
    metadata.drop_all(engine)
    metadata.create_all(engine)
    yield storage
    metadata.drop_all(engine)
    reset_storage_module()


@pytest.fixture
def make_summoner():
    def factory(
        full_name,
        *,
        player_id=None,
        primary=True,
        position=1,
        tier="GOLD",
        rank="I",
        lp=50,
        score=450,
        puuid=None,
    ):
        name, tagline = full_name.split("#", 1)
        return SimpleNamespace(
            fullName=full_name,
            name=name,
            tagline=tagline,
            puuid=puuid or f"puuid-{name}",
            platform="EUW1",
            id=f"id-{name}",
            leaderboardPosition=position,
            tier=tier,
            rank=rank,
            leaguePoints=lp,
            score=score,
            deltaScore=0,
            deltaDailyScore=0,
            deltaGamesPlayed=0,
            deltaDailyGamesPlayed=0,
            deltaLeaderboardPosition=0,
            deltaDailyLeaderboardPosition=0,
            discordUserId=player_id,
            discordPrimary=primary,
        )

    return factory
