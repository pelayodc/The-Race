import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import disnake
from disnake.ext import commands
import pytest
import requests

from conftest import validate_test_database_url
from soloq_challenge import new_edition, register, RuleError


def test_verified_owner_replaces_only_unverified_claim():
    edition = new_edition(0)
    register(edition, "attacker", {"puuid": "account", "approved": False}, ["Ahri"], 1)
    register(edition, "owner", {"puuid": "account", "approved": True}, ["Ahri"], 2)
    assert set(edition["players"]) == {"owner"}
    assert edition["audit"][-2]["action"] == "unverified_claim_replaced"
    with pytest.raises(RuleError, match="account_used"):
        register(edition, "other", {"puuid": "account", "approved": True}, ["Ahri"], 3)


@pytest.mark.parametrize("name", ["therace", "production", "postgres", ""])
def test_postgres_fixture_refuses_non_test_database(name):
    with pytest.raises(ValueError, match="Refusing destructive"):
        validate_test_database_url(f"postgresql+psycopg://user:password@localhost/{name}")


@pytest.mark.parametrize("name", ["therace_test", "test_therace"])
def test_postgres_fixture_accepts_explicit_test_database(name):
    validate_test_database_url(f"postgresql+psycopg://user:password@localhost/{name}")


def test_administration_is_restricted_to_configured_guild(monkeypatch):
    import state
    monkeypatch.setenv("DISCORD_GUILD_ID", "10")
    member = SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True))
    assert state.can_configure_channels(SimpleNamespace(author=member, guild=SimpleNamespace(id=10)))
    assert not state.can_configure_channels(SimpleNamespace(author=member, guild=SimpleNamespace(id=20)))
    assert not state.can_configure_channels(SimpleNamespace(author=member, guild=None))
    monkeypatch.delenv("DISCORD_GUILD_ID")
    monkeypatch.setattr(state, "discordChannel", 123)
    from bot_runtime import bot
    monkeypatch.setattr(bot, "get_channel", lambda _: SimpleNamespace(guild=SimpleNamespace(id=10)))
    assert state.guild_is_allowed(SimpleNamespace(id=10))
    assert not state.guild_is_allowed(SimpleNamespace(id=20))
    monkeypatch.setattr(bot, "get_channel", lambda _: None)
    assert not state.guild_is_allowed(SimpleNamespace(id=10))


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["add", "remove", "admin_matchmaking", "patch"])
async def test_protected_commands_stop_before_business_logic(name, monkeypatch):
    import bot_commands
    guard = AsyncMock(return_value=False)
    monkeypatch.setattr(bot_commands, "require_admin_interaction", guard)
    bot = commands.InteractionBot()
    bot_commands.register_commands(bot)
    command = bot.get_slash_command(name)
    inter = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()))
    args = {"name": "Player", "tagline": "EUW"} if name in ("add", "remove") else {}
    await command.callback(inter, **args)
    guard.assert_awaited_once_with(inter)
    inter.response.defer.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_panel_opens_challenge_admin_section(monkeypatch):
    import admin_panel
    import challenge_ui
    monkeypatch.setattr(admin_panel, "require_admin_interaction", AsyncMock(return_value=True))
    opened = AsyncMock()
    monkeypatch.setattr(challenge_ui, "open_challenge", opened)
    view = admin_panel.AdminView({"botLanguage": "es"})
    button = next(c for c in view.children if c.custom_id == "admin:soloq_challenge")
    assert view.is_persistent()
    inter = object()
    await button.callback(inter)
    opened.assert_awaited_once_with(inter, section="admin")
    monkeypatch.setattr(admin_panel, "require_admin_interaction", AsyncMock(return_value=False))
    opened.reset_mock()
    await button.callback(inter)
    opened.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscription_does_not_overwrite_updates_while_dm_is_pending(monkeypatch, no_database):
    import solo_queue
    latest = {"summoners": {"Existing": {}}, "soloQueueSubscriptions": {}}
    stale = copy.deepcopy(latest)
    async def dm(*args):
        latest["summoners"]["Newly added"] = {}
        return True
    monkeypatch.setattr(solo_queue, "send_dm", dm)
    monkeypatch.setattr(solo_queue, "load_json_data", lambda: copy.deepcopy(latest))
    writes = []
    monkeypatch.setattr(solo_queue, "writeToJsonFile", lambda path, data: writes.append(copy.deepcopy(data)))
    success, _ = await solo_queue.toggle_subscription(stale, "99", {"playerId": "10", "summonerFullName": "Player#EUW"})
    assert success
    assert "Newly added" in writes[-1]["summoners"]
    assert writes[-1]["soloQueueSubscriptions"] == {"99": ["10"]}


@pytest.mark.parametrize("status", [False, True])
def test_riot_timeout_is_handled_without_exposing_url(status, monkeypatch):
    from utils import dataUtils
    def get(url, **kwargs):
        assert kwargs["timeout"] == 15
        raise requests.Timeout("secret URL must not be logged")
    monkeypatch.setattr(dataUtils.requests, "get", get)
    monkeypatch.setattr(dataUtils, "riotBackoffRemaining", lambda: 0)
    recorded = []
    monkeypatch.setattr(dataUtils, "record_riot_error", lambda *args: recorded.append(args))
    result = (dataUtils.riot_get_status if status else dataUtils.riot_get)("https://example.test", "test")
    assert result[0] is False
    assert "secret" not in str(recorded)


@pytest.mark.asyncio
async def test_matchmaking_rejects_overlapping_channel_creation(monkeypatch):
    import asyncio
    import matchmaking
    monkeypatch.setattr(matchmaking, "_match_start_lock", asyncio.Lock())
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow(*args):
        entered.set()
        await release.wait()
        return True, "done", {}
    monkeypatch.setattr(matchmaking, "_finish_matchmaking_teams", slow)
    first = asyncio.create_task(matchmaking.finish_matchmaking_teams(None, {}, [], [], "random"))
    await entered.wait()
    try:
        second = await matchmaking.finish_matchmaking_teams(None, {}, [], [], "random")
        assert second[0] is False
    finally:
        release.set()
        await first


@pytest.mark.asyncio
async def test_challenge_scheduler_rotates_guild_priority_and_resets_budget(monkeypatch):
    import challenge_service as service
    visited = []
    async def tick(guild):
        visited.append(guild.id)
        if guild.id == 1:
            raise RuntimeError("one guild unavailable")
    monkeypatch.setattr(service, "_next_guild_index", 0)
    monkeypatch.setattr(service, "_tick_guild", tick)
    bot = SimpleNamespace(guilds=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
    await service.challenge_tick(bot)
    await service.challenge_tick(bot)
    assert visited == [1, 2, 2, 1]
    assert service._budget is None


@pytest.mark.asyncio
async def test_history_recovers_deliveries_in_one_pass_per_batch(monkeypatch):
    import challenge_service as service
    import challenge_ui as ui
    records = [{"id": "SIM1"}, {"id": "SIM2"}]
    edition = {"id": "edition", "language": "en", "channels": {"history": 50},
               "players": {"1": {"records": records}}}
    document = {"editions": [edition]}
    recovered = SimpleNamespace(id=100, author=SimpleNamespace(id=9),
        embeds=[SimpleNamespace(footer=SimpleNamespace(text="1:SIM1"))], edit=AsyncMock())
    scans = []
    async def history(**kwargs):
        scans.append(kwargs)
        yield recovered
    channel = SimpleNamespace(history=history,
        send=AsyncMock(return_value=SimpleNamespace(id=101)))
    guild = SimpleNamespace(id=10, me=SimpleNamespace(id=9), get_channel=lambda _: channel)
    monkeypatch.setattr(ui, "match_text", lambda e, g, u, r: r["id"])
    monkeypatch.setattr(service, "edit_challenges", lambda guild_id, change: change(document))
    await service.publish_history(guild, edition)
    await service.publish_history(guild, edition)
    assert len(scans) == 1
    channel.send.assert_awaited_once()
    recovered.edit.assert_awaited_once()
    assert [r["message_id"] for r in records] == [100, 101]


@pytest.mark.asyncio
async def test_team_channels_preserve_private_source_permissions(monkeypatch):
    import matchmaking as mm
    from state import ensure_matchmaking_state
    private_role = object()
    overwrites = {private_role: disnake.PermissionOverwrite(view_channel=False, connect=False)}
    category = SimpleNamespace(overwrites=overwrites)
    source = SimpleNamespace(category=category, overwrites=overwrites)
    created = []
    async def create(name, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(id=len(created) + 100)
    guild = SimpleNamespace(get_channel=lambda _: source, create_voice_channel=create)
    monkeypatch.setattr(mm, "ensure_role_assignments", lambda d, a, b: (a, b))
    monkeypatch.setattr(mm, "effective_matchmaking_separate_channels", lambda _: True)
    monkeypatch.setattr(mm, "writeToJsonFile", lambda *args: None)
    monkeypatch.setattr(mm, "get_guild_member", AsyncMock(return_value=None))
    result = await mm.finish_matchmaking_teams(guild, ensure_matchmaking_state({}),
        [{"userId": "1", "voiceChannelId": 1}], [{"userId": "2"}], "random")
    assert result[0]
    assert len(created) == 2
    assert all(c["overwrites"][private_role].view_channel is False for c in created)
