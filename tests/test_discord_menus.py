import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import disnake
import pytest

import discord_menus as menus
from menu_session import MenuSession, SessionView, page_slice
from state import ensure_admin_state


class Response:
    def __init__(self):
        self.done = False
        self.send_message = AsyncMock()
        self.send_modal = AsyncMock()

    def is_done(self):
        return self.done

    async def defer(self, **kwargs):
        self.done = True


@pytest.fixture
def interaction():
    member = SimpleNamespace(id=99, display_name="Live name", guild_permissions=SimpleNamespace(manage_guild=True))
    message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(
        author=member, guild=SimpleNamespace(id=10, get_member=lambda user_id: member, me=member),
        response=Response(), followup=SimpleNamespace(send=AsyncMock(return_value=message)),
        message=message,
    )


@pytest.fixture
def menu_data(monkeypatch, no_database):
    data = ensure_admin_state({"botLanguage": "es", "summoners": {
        f"Player{i}#EUW": {
            "discordUserId": str(i), "discordPrimary": True, "discordDisplayName": f"Cached{i}",
            "tier": "GOLD", "rank": "I", "leaderboardPosition": i + 1, "score": 100,
        }
        for i in range(26)
    }})
    monkeypatch.setattr(menus, "load_json_data", lambda: copy.deepcopy(data))
    monkeypatch.setattr(menus, "writeToJsonFile", lambda path, value: data.update(copy.deepcopy(value)))
    monkeypatch.setattr(menus.AdminMenu, "refresh_public", AsyncMock())
    monkeypatch.setattr(menus, "log_event", lambda *args, **kwargs: None)
    return data


@pytest.mark.parametrize("count,page,expected,pages", [(0, 0, 0, 1), (1, 0, 1, 1), (25, 0, 25, 1), (26, 1, 1, 2), (1, 9, 1, 1)])
def test_pagination_boundaries(count, page, expected, pages):
    items, actual, total = page_slice(list(range(count)), page)
    assert len(items) == expected
    assert total == pages
    assert 0 <= actual < total


@pytest.mark.asyncio
@pytest.mark.parametrize("section", [
    "home", "settings", "players", "players:add", "links", "links:add", "links:primary", "links:unlink",
    "requests", "matchmaking", "matchmaking:queue", "matchmaking:teams", "matchmaking:channels",
    "matchmaking:roles", "matchmaking:member_role", "status", "logs", "maintenance",
])
async def test_admin_sections_fit_discord_limits(section, menu_data, interaction):
    menu = menus.AdminMenu(99)
    menu.section, menu.member_id = section, "1"
    await menu.render(interaction)
    view = menu.session.view
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert embed.description
    assert "menus." not in embed.description
    assert "menus." not in embed.author.name
    navigation = view.children[0]
    assert navigation.placeholder == "Cambiar de pestaña"
    assert all(entry.label.startswith("Pestaña: ") and entry.description for entry in navigation.options)
    widths = {}
    for child in view.children:
        widths[child.row] = widths.get(child.row, 0) + child.width
        if isinstance(child, disnake.ui.Select):
            assert 1 <= len(child.options) <= 25
            assert all(len(item.label) <= 100 for item in child.options)
            assert not child.placeholder.startswith("menus.")
    assert all(width <= 5 for width in widths.values())
    assert len(widths) <= 5
    view.stop()


@pytest.mark.asyncio
async def test_navigation_edits_same_message_and_keeps_deadline(menu_data, interaction):
    menu = menus.AdminMenu(99)
    await menu.render(interaction)
    old = menu.session.view
    deadline = menu.session.deadline
    await menu.go(interaction, "settings")
    assert interaction.followup.send.await_count == 1
    assert menu.session.message.edit.await_count == 1
    assert old.is_finished()
    assert menu.session.deadline == deadline
    assert menu.session.view.timeout <= 120
    menu.session.view.stop()


@pytest.mark.asyncio
async def test_session_rechecks_owner_permission_and_expiry(interaction):
    session = MenuSession(99, admin=True)
    assert await session.check(interaction, {})
    interaction.author.id = 5
    assert not await session.check(interaction, {})
    interaction.author.id = 99
    interaction.author.guild_permissions.manage_guild = False
    assert not await session.check(interaction, {})
    interaction.author.guild_permissions.manage_guild = True
    session.deadline = 0
    assert not await session.check(interaction, {})


@pytest.mark.asyncio
async def test_expiry_disables_controls_when_delete_fails(interaction):
    session = MenuSession(99)
    session.message = interaction.message
    session.message.delete.side_effect = disnake.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
    view = SessionView(session, {})
    view.add_item(disnake.ui.Button(label="Test"))
    session.view = view
    await session.expire()
    assert session.closed
    assert all(child.disabled for child in view.children)
    assert view.is_finished()
    session.message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_removal_requires_confirmation_and_handles_stale_selection(menu_data, interaction, monkeypatch):
    import leaderboard
    monkeypatch.setattr(leaderboard, "remove_summoner_from_data", lambda *args: pytest.fail("Unexpected deletion"))
    menu = menus.AdminMenu(99)
    menu.section, menu.selected = "players", "Player1#EUW"
    await menu.ask(interaction, "remove")
    assert "Player1#EUW" in interaction.followup.send.call_args.kwargs["embed"].description
    assert any(getattr(c, "label", None) == "Confirmar" for c in menu.session.view.children)
    del menu_data["summoners"]["Player1#EUW"]
    await menu.confirm_action(interaction)
    assert menu.selected is None
    menu.session.view.stop()


@pytest.mark.asyncio
async def test_subscriptions_selection_does_not_toggle_and_old_subscription_can_be_cancelled(menu_data, interaction, monkeypatch):
    import solo_queue
    toggle = AsyncMock(return_value=(True, "done"))
    monkeypatch.setattr(solo_queue, "toggle_subscription", toggle)
    menu_data["soloQueueSubscriptions"] = {"99": ["999"]}
    menu = menus.SubscriptionMenu(99)
    menu.section = "mine"
    await menu.render(interaction)
    await menu.select(interaction, "999")
    toggle.assert_not_awaited()
    assert any(getattr(c, "label", None) == "Cancelar suscripción" for c in menu.session.view.children)
    button = next(c for c in menu.session.view.children if getattr(c, "label", None) == "Cancelar suscripción")
    await button.callback(interaction)
    toggle.assert_awaited_once()
    menu.session.view.stop()


@pytest.mark.asyncio
async def test_subscriptions_revalidate_top_25(menu_data, interaction, monkeypatch):
    import solo_queue
    toggle = AsyncMock()
    monkeypatch.setattr(solo_queue, "toggle_subscription", toggle)
    menu = menus.SubscriptionMenu(99)
    await menu.render(interaction)
    await menu.change(interaction, {"playerId": "25", "summonerFullName": "Player25#EUW"}, False)
    toggle.assert_not_awaited()
    menu.session.view.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["teams", "channels", "roles", "my_role"])
async def test_matchmaking_grouping_and_admin_locks(section, menu_data, interaction):
    menu_data["matchmakingTeamModeForced"] = "random"
    menu_data["matchmakingSeparateChannelsForced"] = True
    menu = menus.MatchmakingMenu(99)
    menu.section = section
    await menu.render(interaction)
    children = menu.session.view.children
    assert len([c for c in children if isinstance(c, disnake.ui.Select)]) <= 3
    if section in ("teams", "channels"):
        assert children[1].disabled
    menu.session.view.stop()


def test_all_supported_platforms_have_routing_region():
    assert set(menus.PLATFORM_REGIONS) == set(menus.platforms)
    assert menus.PLATFORM_REGIONS["EUW1"] == "EUROPE"
    assert menus.PLATFORM_REGIONS["NA1"] == "AMERICAS"
    assert menus.PLATFORM_REGIONS["KR"] == "ASIA"


@pytest.mark.asyncio
async def test_account_link_primary_and_confirmed_unlink(menu_data, interaction):
    menu_data["summoners"]["New#EUW"] = {}
    menu = menus.AdminMenu(99)
    menu.member_id = "99"
    menu.section, menu.selected = "links:add", "New#EUW"
    await menu.account_action(interaction)
    assert menu_data["summoners"]["New#EUW"]["discordUserId"] == "99"
    assert menu_data["summoners"]["New#EUW"]["discordPrimary"] is True

    menu_data["summoners"]["Second#EUW"] = {}
    menu.selected = "Second#EUW"
    await menu.account_action(interaction)
    assert menu_data["summoners"]["Second#EUW"]["discordPrimary"] is False
    menu.section = "links:primary"
    await menu.account_action(interaction)
    assert menu_data["summoners"]["Second#EUW"]["discordPrimary"] is True
    assert menu_data["summoners"]["New#EUW"]["discordPrimary"] is False

    menu.section = "links:unlink"
    await menu.ask(interaction, "unlink")
    assert menu_data["summoners"]["Second#EUW"]["discordUserId"] == "99"
    await menu.confirm_action(interaction)
    assert "discordUserId" not in menu_data["summoners"]["Second#EUW"]
    menu.session.view.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("approve", [True, False])
async def test_requests_revalidate_and_do_not_apply_twice(approve, menu_data, interaction):
    menu_data["summoners"]["New#EUW"] = {}
    menu_data["discordLinkRequests"] = [{"id": "request-1", "discordUserId": "99", "summonerFullName": "New#EUW"}]
    menu = menus.AdminMenu(99)
    menu.section, menu.selected = "requests", "request-1"
    await menu.request_action(interaction, approve)
    assert menu_data["discordLinkRequests"] == []
    assert bool(menu_data["summoners"]["New#EUW"].get("discordUserId")) is approve
    menu.selected = "request-1"
    await menu.request_action(interaction, approve)
    assert menu_data["discordLinkRequests"] == []
    menu.session.view.stop()


@pytest.mark.asyncio
async def test_subscription_failed_dm_never_persists(menu_data, interaction, monkeypatch):
    import solo_queue
    monkeypatch.setattr(solo_queue, "send_dm", AsyncMock(return_value=False))
    writes = []
    monkeypatch.setattr(solo_queue, "writeToJsonFile", lambda *args: writes.append(args))
    menu = menus.SubscriptionMenu(99)
    await menu.render(interaction)
    target = menu.targets["1"]
    await menu.change(interaction, target, False)
    assert writes == []
    assert menu_data["soloQueueSubscriptions"] == {}
    menu.session.view.stop()


@pytest.mark.asyncio
async def test_settings_callback_keeps_session_and_updates_button(menu_data, interaction, monkeypatch):
    import admin_panel
    monkeypatch.setattr(admin_panel, "load_json_data", lambda: copy.deepcopy(menu_data))
    monkeypatch.setattr(admin_panel, "writeToJsonFile", lambda path, value: menu_data.update(copy.deepcopy(value)))
    monkeypatch.setattr(admin_panel, "refresh_configured_admin_message", AsyncMock())
    monkeypatch.setattr(admin_panel, "log_event", lambda *args, **kwargs: None)
    menu = menus.AdminMenu(99)
    menu.section = "settings"
    await menu.render(interaction)
    button = next(c for c in menu.session.view.children if c.custom_id == "admin:settings:leaderboard_chat_commands")
    assert button.label == "Activar /add y /remove"
    await button.callback(interaction)
    assert menu_data["leaderboardChatCommandsEnabled"] is True
    assert interaction.followup.send.await_count == 1
    assert any(getattr(c, "label", None) == "Desactivar /add y /remove" for c in menu.session.view.children)
    menu.session.view.stop()


@pytest.mark.asyncio
async def test_add_modal_deduces_region_and_rechecks_permissions(menu_data, interaction, monkeypatch):
    import leaderboard
    add = AsyncMock(return_value=(True, "done"))
    monkeypatch.setattr(leaderboard, "add_summoner_to_data", add)
    menu = menus.AdminMenu(99)
    modal = menus.SummonerModal(menu, "EUW1", menu_data)
    interaction.text_values = {"name": " Player ", "tagline": "EUW"}
    await modal.callback(interaction)
    add.assert_awaited_once_with("Player", "EUW", "EUW1", "EUROPE")
    interaction.author.guild_permissions.manage_guild = False
    await modal.callback(interaction)
    assert add.await_count == 1
    menu.session.view.stop()


@pytest.mark.asyncio
async def test_backup_attaches_files_to_existing_menu(menu_data, interaction, monkeypatch):
    import storage
    monkeypatch.setattr(storage, "export_state", lambda path: menu_data)
    monkeypatch.setattr(menus, "read_audit_events", lambda: [])
    menu = menus.AdminMenu(99)
    menu.section = "maintenance"
    await menu.render(interaction)
    await menu.operation(interaction, "backup")
    assert interaction.followup.send.await_count == 1
    files = menu.session.message.edit.call_args.kwargs["files"]
    assert len(files) == 2
    assert files[0].filename.endswith(".json")
    assert files[1].filename.endswith(".jsonl")
    menu.session.view.stop()


@pytest.mark.asyncio
async def test_pagination_clamps_after_records_are_removed(menu_data, interaction):
    menu = menus.AdminMenu(99)
    menu.section, menu.page = "players", 1
    await menu.render(interaction)
    assert menu.page == 1
    menu_data["summoners"] = {"Only#EUW": {}}
    await menu.render(interaction)
    assert menu.page == 0
    assert not any(getattr(c, "label", None) == "Siguiente" for c in menu.session.view.children)
    menu.session.view.stop()
