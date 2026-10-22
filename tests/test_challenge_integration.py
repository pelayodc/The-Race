import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import disnake
import pytest

import challenge_ui as ui
import challenge_service as service
from soloq_challenge import new_edition, register, DAY, WEEK


@pytest.fixture
def memory(monkeypatch, no_database):
    edition = new_edition(0)
    for uid in range(1, 28):
        register(edition, uid, {"name": f"Player{uid}#EUW", "puuid": f"p{uid}", "region": "EUROPE", "platform": "EUW1"}, ["Ahri", "Ashe"], 1)
    document = {"editions": [edition]}
    def read(_guild):
        return copy.deepcopy(document)
    def edit(_guild, callback):
        candidate = copy.deepcopy(document)
        result = callback(candidate)
        document.clear()
        document.update(candidate)
        return result
    for module in (ui, service):
        monkeypatch.setattr(module, "read_challenges", read)
        monkeypatch.setattr(module, "edit_challenges", edit)
    monkeypatch.setattr(ui, "load_json_data", lambda: {"botLanguage": "en"})
    return document


@pytest.fixture
def inter():
    class Response:
        def __init__(self):
            self.done = False
            self.send_message = AsyncMock()
            self.send_modal = AsyncMock()
        def is_done(self):
            return self.done
        async def defer(self, **kwargs):
            self.done = True
    user = SimpleNamespace(id=1, display_name="Current Discord Name", guild_permissions=SimpleNamespace(manage_guild=True))
    guild = SimpleNamespace(id=10, get_member=lambda uid: user)
    message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(guild=guild, author=user, response=Response(), followup=SimpleNamespace(send=AsyncMock(return_value=message)))


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["overview", "registration", "ranking", "shells", "incoming", "completed", "all_shells", "history", "history_detail", "admin"])
async def test_challenge_menus_fit_discord_and_keep_owner(section, memory, inter):
    menu = ui.ChallengeMenu(inter)
    menu.section = section
    await menu.render(inter)
    view = menu.session.view
    widths = {}
    for item in view.children:
        widths[item.row] = widths.get(item.row, 0) + item.width
        if isinstance(item, disnake.ui.Select):
            assert 1 <= len(item.options) <= 25
    assert max(widths.values()) <= 5
    embed = inter.followup.send.call_args.kwargs["embed"]
    assert len(embed.description or "") <= 4096
    assert menu.session.owner_id == inter.author.id
    menu.session.view.stop()


@pytest.mark.asyncio
async def test_config_modal_rechecks_permissions(memory, inter):
    menu = ui.ChallengeMenu(inter)
    menu.edition()
    modal = ui.ConfigModal(menu)
    inter.author.guild_permissions.manage_guild = False
    inter.text_values = {"scores": "100,0,1,-1,1,1", "limits": "3,24,0", "tasks": "no_flash"}
    await modal.callback(inter)
    assert memory["editions"][0]["config"]["win"] == 10
    if menu.session.view:
        menu.session.view.stop()


@pytest.mark.asyncio
async def test_reconnect_does_not_duplicate_counted_games(memory, monkeypatch):
    edition = memory["editions"][0]
    start = edition["starts_at"]
    monkeypatch.setattr(service, "timestamp", lambda: start + 10000)
    calls = []
    async def request(url):
        calls.append(url)
        if "/ids?" in url:
            return ["EUW1_2", "EUW1_1"]
        number = int(url.rsplit("_", 1)[1])
        return {"metadata": {"matchId": f"EUW1_{number}"}, "info": {"queueId": 420, "gameStartTimestamp": (start + number * 1000) * 1000, "gameDuration": 1000, "participants": [{"puuid": "p1", "participantId": 1, "championName": "Ahri", "kills": 1, "deaths": 1, "assists": 1, "win": True}]}}
    monkeypatch.setattr(service, "riot_json", request)
    await service.collect_player(10, edition["id"], "1")
    await service.collect_player(10, edition["id"], "1")
    records = memory["editions"][0]["players"]["1"]["records"]
    assert [r["id"] for r in records] == ["EUW1_1", "EUW1_2"]
    assert len([url for url in calls if "/ids?" not in url]) == 2


@pytest.mark.asyncio
async def test_failed_old_match_blocks_newer_match(memory, monkeypatch):
    edition = memory["editions"][0]
    monkeypatch.setattr(service, "timestamp", lambda: edition["starts_at"] + 10000)
    calls = []
    async def request(url):
        calls.append(url)
        if "/ids?" in url:
            return ["EUW1_2", "EUW1_1"]
        raise RuntimeError("temporary Riot failure")
    monkeypatch.setattr(service, "riot_json", request)
    with pytest.raises(RuntimeError):
        await service.collect_player(10, edition["id"], "1")
    assert not any(url.endswith("EUW1_2") for url in calls)
    assert memory["editions"][0]["players"]["1"]["scan_at"] == 0


@pytest.mark.postgres
def test_challenge_store_survives_legacy_saves_and_exports(postgres_storage):
    from challenge_store import edit_challenges, read_challenges
    edit_challenges(10, lambda doc: doc["editions"].append(new_edition(0)))
    postgres_storage.save_state({"summoners": {}, "runtime": 3})
    assert len(read_challenges(10)["editions"]) == 1
    backup = postgres_storage.export_state()
    assert len(backup["soloChallenges"]["10"]["editions"]) == 1
    postgres_storage.save_state(backup)
    assert "soloChallenges" not in postgres_storage.load_state()


@pytest.mark.postgres
def test_challenge_transaction_rolls_back_on_error(postgres_storage):
    from challenge_store import edit_challenges, read_challenges
    edit_challenges(10, lambda doc: doc["editions"].append(new_edition(0)))
    def failed(doc):
        doc["editions"].clear()
        raise ValueError("cancel")
    with pytest.raises(ValueError):
        edit_challenges(10, failed)
    assert len(read_challenges(10)["editions"]) == 1


@pytest.mark.postgres
def test_challenge_concurrent_transactions_do_not_lose_updates(postgres_storage):
    from concurrent.futures import ThreadPoolExecutor
    from challenge_store import edit_challenges, read_challenges
    def increment(_):
        def change(doc):
            doc["counter"] = doc.get("counter", 0) + 1
        edit_challenges(10, change)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(increment, range(20)))
    assert read_challenges(10)["counter"] == 20


@pytest.mark.asyncio
async def test_channel_lifecycle_preserves_ids_and_private_admin_channel(memory, monkeypatch):
    class Role:
        def __init__(self, admin=False):
            self.permissions = SimpleNamespace(manage_guild=admin, administrator=admin)
    class Channel:
        def __init__(self, guild, category=None):
            self.id = len(guild.channels) + 1
            self.name = ""
            self.text_channels = []
            self.messages = {}
            self.overwrites = {}
            self.edit = AsyncMock()
            self.delete = AsyncMock()
            guild.channels[self.id] = self
            if category:
                category.text_channels.append(self)
        async def send(self, **kwargs):
            message = SimpleNamespace(id=len(self.messages) + 100, edit=AsyncMock())
            self.messages[message.id] = message
            return message
        async def fetch_message(self, message_id):
            return self.messages[message_id]
    class Guild:
        id = 10
        def __init__(self):
            self.channels = {}
            self.default_role, self.me, self.admin_role = Role(), Role(True), Role(True)
            self.roles = [self.default_role, self.admin_role]
        def get_channel(self, channel_id):
            return self.channels.get(channel_id)
        def get_member(self, uid):
            return SimpleNamespace(display_name=f"User{uid}")
        async def create_category(self, name, **kwargs):
            result = Channel(self)
            result.name = name
            return result
        async def create_text_channel(self, name, category, overwrites):
            result = Channel(self, category)
            result.name, result.overwrites = name, overwrites
            return result
    guild = Guild()
    monkeypatch.setattr(service, "timestamp", lambda: 1)
    await service.ensure_channels(guild, copy.deepcopy(memory["editions"][0]))
    first_ids = memory["editions"][0]["channels"].copy()
    private = guild.get_channel(first_ids["admin"])
    assert private.overwrites[guild.default_role].view_channel is False
    assert private.overwrites[guild.admin_role].view_channel is True
    await service.ensure_channels(guild, copy.deepcopy(memory["editions"][0]))
    assert first_ids == memory["editions"][0]["channels"]
    monkeypatch.setattr(service, "timestamp", lambda: memory["editions"][0]["starts_at"])
    await service.ensure_channels(guild, copy.deepcopy(memory["editions"][0]))
    channels = memory["editions"][0]["channels"]
    assert all(kind in channels for kind in ("ranking", "shells", "history", "admin"))
    guild.get_channel(first_ids["registration"]).delete.assert_awaited_once()
