"""Session-based menus; business operations remain in their owning modules."""

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO

import disnake

from discord_helpers import get_guild_member
from i18n import available_languages, t
from menu_session import MenuButton, MenuSelect, MenuSession, SessionView, add_pager, page_slice
from state import ensure_admin_state, load_json_data, missing_bot_channel_permissions
from utils.auditUtils import interaction_actor, log_event, read_audit_events
from utils.commonUtils import jsonFile, platforms
from utils.jsonUtils import writeToJsonFile


# Riot routing follows the platform groups already supported by this bot.
PLATFORM_REGIONS = {
    **dict.fromkeys(("BR1", "LA1", "LA2", "NA1"), "AMERICAS"),
    **dict.fromkeys(("EUN1", "EUW1", "TR1", "RU"), "EUROPE"),
    **dict.fromkeys(("JP1", "KR"), "ASIA"),
    **dict.fromkeys(("OC1", "PH2", "SG2", "TH2", "TW2", "VN2"), "SEA"),
}
ADMIN_SECTIONS = ("home", "settings", "players", "links", "requests", "matchmaking", "challenge", "status", "logs", "maintenance")


def text(data, key, **values):
    return t(data, f"menus.{key}", **values)


def option(label, value, **kwargs):
    return disnake.SelectOption(label=str(label)[:100], value=str(value), **kwargs)


class MemberSelect(disnake.ui.UserSelect):
    def __init__(self, menu, data):
        super().__init__(placeholder=text(data, "member"), row=1)
        self.menu = menu

    async def callback(self, inter):
        await self.menu.session.acknowledge(inter)
        self.menu.member_id = str(self.values[0].id)
        self.menu.selected = None
        self.menu.page = 0
        await self.menu.render(inter)


class Menu:
    def __init__(self, owner_id, admin=False):
        self.session = MenuSession(owner_id, admin)
        self.section = "home"
        self.page = 0
        self.selected = None
        self.member_id = None
        self.confirmation = None

    async def go(self, inter, section):
        await self.session.acknowledge(inter)
        self.section, self.page, self.selected = section, 0, None
        self.confirmation = None
        await self.render(inter)

    async def paginate(self, inter, page):
        await self.session.acknowledge(inter)
        self.page, self.selected = page, None
        await self.render(inter)

    async def select(self, inter, value):
        await self.session.acknowledge(inter)
        self.selected = value
        self.confirmation = None
        await self.render(inter)

    def button(self, view, data, key, callback, **kwargs):
        view.add_item(MenuButton(text(data, key), callback, **kwargs))

    def navigation(self, view, data, sections):
        current = self.section.split(":")[0]
        if current == "my_role":
            current = "roles"
        view.add_item(MenuSelect(text(data, "change_tab"), [
            option(text(data, "tab_label", name=text(data, name)), name,
                   description=text(data, f"tab_info.{name}")[:100], default=name == current)
            for name in sections
        ], self.go, row=0))

    def describe(self, embed, data, context):
        section = self.section.replace(":", "_")
        info = text(data, f"page_info.{section}")
        if info.startswith("menus."):
            info = text(data, f"tab_info.{self.section.split(':')[0]}")
        if not embed.description:
            embed.description = info
        elif self.section not in ("home", "settings", "matchmaking"):
            embed.description = f"{info}\n\n{embed.description}"
        path = [context] + self.section.split(":") if context != self.section else [context]
        labels = {"add": "link" if self.section.startswith("links:") else "add", "primary": "set_primary", "member_role": "set_role"}
        embed.set_author(name=" / ".join(text(data, labels.get(part, part)) for part in path))
        embed.set_footer(text=text(data, "session_info"))

    def listing(self, view, embed, data, entries):
        total = len(entries)
        entries, self.page, pages = page_slice(entries, self.page)
        embed.add_field(name=text(data, "entries"), value=str(total), inline=True)
        if pages > 1:
            embed.add_field(name=text(data, "page"), value=f"{self.page + 1} / {pages}", inline=True)
        valid = {str(entry.value) for entry in entries}
        if self.selected not in valid:
            self.selected = None
        if entries:
            for entry in entries:
                entry.default = entry.value == self.selected
            view.add_item(MenuSelect(text(data, "select"), entries, self.select))
        else:
            embed.description = text(data, "empty")
        if pages > 1:
            add_pager(view, self.page, pages, self.paginate, data)


class AdminMenu(Menu):
    def __init__(self, owner_id):
        super().__init__(owner_id, admin=True)
        self.category = None
        self.actor_query = None

    async def go(self, inter, section):
        if section == "challenge":
            from discord_helpers import require_admin_interaction
            if not await require_admin_interaction(inter):
                return
            await self.session.acknowledge(inter)
            await self.session.expire()
            from challenge_ui import open_challenge
            await open_challenge(inter, section="admin")
            return
        await super().go(inter, section)

    async def render(self, inter, notice=None, detail=None):
        from admin_panel import admin_embed, settings_admin_embed
        from linked_accounts import rebuild_discord_links_from_summoners
        from matchmaking import matchmaking_admin_embed

        await self.session.acknowledge(inter)
        data = ensure_admin_state(load_json_data())
        rebuild_discord_links_from_summoners(data)
        view = SessionView(self.session, data)
        root = self.section.split(":")[0]
        self.navigation(view, data, ADMIN_SECTIONS)
        for entry in view.children[0].options:
            entry.default = entry.value == root
        embed = disnake.Embed(title=text(data, root), colour=disnake.Colour.dark_teal())
        if self.section == "home":
            embed = admin_embed(data)
        elif self.section == "settings":
            embed = settings_admin_embed(data)
            self.add_settings(view, data)
        elif self.section == "players":
            self.listing(view, embed, data, [option(name, name) for name in sorted(data["summoners"])])
            self.button(view, data, "add", lambda i: self.go(i, "players:add"))
            if self.selected:
                record = data["summoners"][self.selected]
                rank = " ".join(str(record.get(key) or "-") for key in ("tier", "rank"))
                value = f"{rank} · {record.get('leaguePoints', 0)} LP"
                if record.get("discordUserId"):
                    value += f"\n<@{record['discordUserId']}> · {text(data, 'primary' if record.get('discordPrimary') else 'secondary')}"
                embed.add_field(name=self.selected, value=value, inline=False)
                self.button(view, data, "remove", lambda i: self.ask(i, "remove"), style=disnake.ButtonStyle.danger)
        elif self.section == "players:add":
            view.add_item(MenuSelect(text(data, "platform"), [option(p, p) for p in platforms], self.add_summoner))
        elif self.section == "links":
            view.add_item(MemberSelect(self, data))
            if self.member_id:
                embed.description = f"<@{self.member_id}>"
                accounts = [
                    f"{name} · {text(data, 'primary' if record.get('discordPrimary') else 'secondary')}"
                    for name, record in data["summoners"].items()
                    if str(record.get("discordUserId")) == self.member_id
                ]
                embed.add_field(name=text(data, "links"), value="\n".join(accounts)[:1024] or text(data, "empty"), inline=False)
                self.button(view, data, "link", lambda i: self.go(i, "links:add"))
                self.button(view, data, "set_primary", lambda i: self.go(i, "links:primary"))
                self.button(view, data, "unlink", lambda i: self.go(i, "links:unlink"), style=disnake.ButtonStyle.danger)
        elif self.section.startswith("links:"):
            action = self.section.split(":")[1]
            records = data["summoners"]
            entries = []
            for name, record in sorted(records.items()):
                linked_id = str(record.get("discordUserId") or "")
                if (action == "add" and (not linked_id or linked_id == self.member_id)) or (action != "add" and linked_id == self.member_id):
                    label = "primary" if record.get("discordPrimary") else "secondary"
                    entries.append(option(name, name, description=text(data, label) if linked_id else None))
            self.listing(view, embed, data, entries)
            if self.selected:
                embed.description = f"<@{self.member_id}> · {self.selected}"
                key = {"add": "link", "primary": "set_primary", "unlink": "unlink"}[action]
                handler = (lambda i: self.ask(i, "unlink")) if action == "unlink" else self.account_action
                self.button(view, data, key, handler, style=disnake.ButtonStyle.danger if action == "unlink" else disnake.ButtonStyle.primary)
        elif self.section == "requests":
            from linked_accounts import discord_link_requests, find_discord_link_request
            entries = [option(f"{r.get('discordDisplayName') or r['discordUserId']} · {r['summonerFullName']}", r["id"]) for r in discord_link_requests(data)]
            self.listing(view, embed, data, entries)
            request = find_discord_link_request(data, self.selected) if self.selected else None
            if request:
                embed.description = f"<@{request['discordUserId']}> · {request['summonerFullName']}"
                self.button(view, data, "approve", lambda i: self.request_action(i, True), style=disnake.ButtonStyle.success)
                self.button(view, data, "reject", lambda i: self.request_action(i, False), style=disnake.ButtonStyle.danger)
        elif self.section == "matchmaking":
            embed = matchmaking_admin_embed(data)
            for section in ("teams", "channels", "roles"):
                self.button(view, data, section, lambda i, name=section: self.go(i, f"matchmaking:{name}"), row=1)
            self.button(view, data, "queue", lambda i: self.go(i, "matchmaking:queue"), row=2)
            from matchmaking import MatchmakingAdminView
            legacy = MatchmakingAdminView(data)
            force = next(c for c in legacy.children if c.custom_id == "admin:matchmaking:force_start")
            self.add_legacy_action(view, force, text(data, "start"))
            legacy.stop()
        elif self.section == "matchmaking:queue":
            from matchmaking import QueueRemoveSelect
            queue, self.page, pages = page_slice(data["matchmakingQueue"], self.page)
            if queue:
                item = QueueRemoveSelect(queue, data)
                item.row = 1
                self.borrow(view, item)
            else:
                embed.description = text(data, "empty")
            if pages > 1:
                add_pager(view, self.page, pages, self.paginate, data)
        elif self.section in ("matchmaking:teams", "matchmaking:channels", "matchmaking:roles"):
            await self.matchmaking_settings(view, embed, data, inter)
        elif self.section == "matchmaking:member_role":
            view.add_item(MemberSelect(self, data))
            if self.member_id:
                from matchmaking import role_options
                embed.description = f"<@{self.member_id}>"
                view.add_item(MenuSelect(text(data, "roles"), role_options(None, data), self.set_role, row=2))
        elif self.section == "status":
            embed = self.status_embed(data)
            self.button(view, data, "refresh", self.render)
            self.add_operations(view, data, ("health", "permissions"))
        elif self.section == "logs":
            from admin_panel import filter_audit_events, format_log_event
            from bot_runtime import AUDIT_CATEGORY_LABELS
            events = filter_audit_events(category=self.category, actor_query=self.actor_query)
            embed.description = "\n".join(format_log_event(event) for event in events)[-4000:] or text(data, "empty")
            if self.category:
                embed.add_field(name=text(data, "categories"), value=text(data, self.category), inline=True)
            if self.actor_query:
                embed.add_field(name=text(data, "actor"), value=self.actor_query, inline=True)
            view.add_item(MenuSelect(text(data, "logs"), [option(text(data, "all"), "all", default=self.category is None)] + [option(text(data, category), category, default=self.category == category) for category in AUDIT_CATEGORY_LABELS], self.filter_logs))
            self.button(view, data, "search", self.search_actor)
            self.add_operations(view, data, ("summary",))
        elif self.section == "maintenance":
            self.add_operations(view, data, ("backup", "recreate", "force_leaderboard", "force_daily"))
        if self.confirmation:
            # Confirmation replaces the action rows, retaining section navigation.
            for child in list(view.children)[1:]:
                view.remove_item(child)
            embed.description = text(data, "confirm_remove", name=self.selected)
            self.button(view, data, "confirm", self.confirm_action, style=disnake.ButtonStyle.danger)
            self.button(view, data, "cancel", self.cancel_confirmation)
        if self.section != "home":
            parent = root if ":" in self.section else "home"
            if detail:
                self.button(view, data, "back", self.render)
            elif self.selected and ":" not in self.section:
                self.button(view, data, "back", lambda i: self.select(i, None))
            else:
                self.button(view, data, "back", lambda i: self.go(i, parent))
        if detail:
            embed = detail
        if not self.confirmation and not detail:
            self.describe(embed, data, "home")
        await self.session.show(inter, embed, view, notice)

    async def ask(self, inter, action):
        await self.session.acknowledge(inter)
        self.confirmation = action
        await self.render(inter)

    async def cancel_confirmation(self, inter):
        self.confirmation = None
        await self.render(inter)

    async def confirm_action(self, inter):
        await self.session.acknowledge(inter)
        data = ensure_admin_state(load_json_data())
        action = self.confirmation
        self.confirmation = None
        if action == "remove" and self.selected in data["summoners"]:
            from leaderboard import remove_summoner_from_data
            name, tag = self.selected.split("#", 1)
            success, notice = remove_summoner_from_data(name, tag)
        elif action == "unlink" and str(data["summoners"].get(self.selected, {}).get("discordUserId")) == self.member_id:
            from linked_accounts import unlink_summoner_from_discord
            success, notice = unlink_summoner_from_discord(data, self.selected)
            writeToJsonFile(jsonFile, data)
        else:
            success, notice = False, text(data, "stale")
        log_event("leaderboard_summoner_remove" if action == "remove" else "discord_link_removed", actor=interaction_actor(inter), status="success" if success else "error", summary=notice)
        self.selected = None
        await self.refresh_public()
        await self.render(inter, notice)

    async def refresh_public(self):
        from persistent_messages import refresh_configured_admin_message
        await refresh_configured_admin_message()

    async def add_summoner(self, inter, platform):
        await inter.response.send_modal(SummonerModal(self, platform, ensure_admin_state(load_json_data())))

    async def account_action(self, inter):
        from linked_accounts import link_summoner_to_discord, set_primary_summoner_for_user
        await self.session.acknowledge(inter)
        member = await get_guild_member(inter.guild, self.member_id)
        data = ensure_admin_state(load_json_data())
        if member is None or self.selected not in data["summoners"]:
            await self.render(inter, text(data, "stale"))
            return
        if self.section == "links:add":
            success, notice = link_summoner_to_discord(data, member, self.selected, primary=False)
            event = "discord_link_created"
        else:
            success, notice = set_primary_summoner_for_user(data, member, self.selected)
            event = "discord_link_primary_changed"
        if success:
            writeToJsonFile(jsonFile, data)
        log_event(event, actor=interaction_actor(inter), status="success" if success else "error", summary=notice)
        await self.refresh_public()
        await self.render(inter, notice)

    async def request_action(self, inter, approve):
        from linked_accounts import find_discord_link_request, approve_discord_link_request, remove_discord_link_request
        await self.session.acknowledge(inter)
        data = ensure_admin_state(load_json_data())
        request = find_discord_link_request(data, self.selected)
        if not request:
            await self.render(inter, text(data, "stale"))
            return
        if approve:
            member = await get_guild_member(inter.guild, request["discordUserId"])
            if not member:
                await self.render(inter, text(data, "stale"))
                return
            # Reload after the member lookup, which may yield to another admin.
            data = ensure_admin_state(load_json_data())
            success, notice = approve_discord_link_request(data, self.selected, member)
        else:
            success = bool(remove_discord_link_request(data, self.selected))
            notice = t(data, "linked_accounts.request_rejected", summoner=request["summonerFullName"])
        if success:
            writeToJsonFile(jsonFile, data)
        log_event("discord_link_request_accepted" if approve else "discord_link_request_rejected", actor=interaction_actor(inter), status="success" if success else "error", summary=notice)
        self.selected = None
        await self.refresh_public()
        await self.render(inter, notice)

    def add_settings(self, view, data):
        from admin_panel import SettingsAdminView
        legacy = SettingsAdminView(data)
        for child in list(legacy.children):
            child.row = {"admin:settings:leaderboard": 1, "admin:settings:matchmaking": 2, "admin:settings:language": 3}.get(child.custom_id, 4)
            if child.custom_id == "admin:settings:leaderboard_chat_commands":
                child.label = text(data, "disable_commands" if data["leaderboardChatCommandsEnabled"] else "enable_commands")
            self.borrow(view, child)
        legacy.stop()

    def borrow(self, view, item):
        callback = item.callback
        async def wrapped(inter):
            await self.session.acknowledge(inter)
            await callback(MenuInteraction(inter, self))
        item.callback = wrapped
        view.add_item(item)

    def add_legacy_action(self, view, item, label):
        item.label, item.row = label, 4
        if item.style == disnake.ButtonStyle.danger:
            item.style = disnake.ButtonStyle.secondary
        self.borrow(view, item)

    def add_operations(self, view, data, operations):
        for key in operations:
            self.button(view, data, key, lambda i, name=key: self.operation(i, name))

    def status_embed(self, data):
        from bot_runtime import TASKS
        from utils.dataUtils import riotBackoffRemaining
        from leaderboard import estimate_leaderboard_api_calls
        embed = disnake.Embed(title=text(data, "status"), colour=disnake.Colour.dark_teal())
        for key in ("leaderboardLastUpdateAt", "leaderboardLastUpdateStatus", "leaderboardLastDailyImageAt", "leaderboardLastDailyImageStatus"):
            embed.add_field(name=text(data, key), value=str(data.get(key) or "-"), inline=False)
        embed.add_field(name=text(data, "backoff"), value=f"{int(riotBackoffRemaining())} s")
        embed.add_field(name=text(data, "api_calls"), value=str(estimate_leaderboard_api_calls(data)))
        for name, task in TASKS.items():
            embed.add_field(name=name, value=t(data, "common.running" if task.is_running() else "common.stopped"))
        error = (data.get("lastRiotError") or {}).get("summary")
        if error:
            embed.add_field(name=text(data, "errors"), value=error[:1024], inline=False)
        return embed

    async def operation(self, inter, operation):
        await self.session.acknowledge(inter)
        data = ensure_admin_state(load_json_data())
        notice, detail, files = None, None, []
        success = True
        if operation == "backup":
            from storage import export_state
            state = export_state(jsonFile)
            if state is None:
                await self.render(inter, text(data, "empty"))
                return
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            files = [disnake.File(BytesIO(json.dumps(state, ensure_ascii=False, indent=2).encode()), filename=f"data-backup-{stamp}.json"), disnake.File(BytesIO("\n".join(json.dumps(e, ensure_ascii=False) for e in read_audit_events()).encode()), filename=f"audit-{stamp}.jsonl")]
        elif operation == "recreate":
            from persistent_messages import recreate_persistent_messages
            notice = await recreate_persistent_messages(data)
        elif operation in ("force_leaderboard", "force_daily"):
            from leaderboard import force_leaderboard_refresh, force_daily_rank_image
            action = force_leaderboard_refresh if operation == "force_leaderboard" else force_daily_rank_image
            success, notice, data = await action(interaction_actor(inter))
        elif operation == "summary":
            from collections import Counter
            from admin_panel import filter_audit_events, audit_event_category, format_log_event
            events = filter_audit_events(since=datetime.now(timezone.utc) - timedelta(hours=24), limit=None)
            detail = disnake.Embed(title=text(data, "summary"), colour=disnake.Colour.dark_teal())
            detail.add_field(name=text(data, "events"), value=str(len(events)))
            detail.add_field(name=text(data, "errors"), value=str(sum(e.get("status") == "error" for e in events)))
            categories = Counter(audit_event_category(event) for event in events)
            detail.add_field(name=text(data, "categories"), value="\n".join(f"{text(data, category)}: {count}" for category, count in sorted(categories.items())) or text(data, "empty"), inline=False)
            errors = [event for event in events if event.get("status") == "error"]
            if errors:
                detail.add_field(name=text(data, "errors"), value="\n".join(format_log_event(event) for event in errors[-5:])[-1024:], inline=False)
        elif operation == "health":
            from persistent_messages import configured_message_status
            detail = self.status_embed(data)
            detail.title = text(data, "health")
            for kind in ("admin", "leaderboard", "matchmaking"):
                detail.add_field(name=text(data, kind), value=await configured_message_status(data, kind), inline=False)
            detail.add_field(name=text(data, "players"), value=str(len(data["summoners"])))
            detail.add_field(name=text(data, "requests"), value=str(len(data["discordLinkRequests"])))
            detail.add_field(name=text(data, "queue"), value=str(len(data["matchmakingQueue"])))
        elif operation == "permissions":
            from discord_helpers import get_discord_channel
            from state import admin_channel_id, leaderboard_channel_id, matchmaking_channel_id
            detail = disnake.Embed(title=text(data, "permissions"), colour=disnake.Colour.dark_teal())
            member = inter.guild.me
            for name, channel_id in (("admin", admin_channel_id(data)), ("leaderboard", leaderboard_channel_id(data)), ("matchmaking", matchmaking_channel_id(data))):
                channel = await get_discord_channel(channel_id)
                missing = missing_bot_channel_permissions(channel, member) if channel else [text(data, "empty")]
                if channel and member and name == "matchmaking":
                    permissions = channel.permissions_for(member)
                    missing.extend(p for p in ("manage_channels", "move_members") if not getattr(permissions, p, False))
                detail.add_field(name=text(data, name), value=", ".join(missing) if missing else "OK", inline=False)
        log_event(f"operations_menu_{operation}", actor=interaction_actor(inter), status="success" if success else "error", summary=notice or operation)
        await self.render(inter, notice, detail)
        if files and not self.session.closed:
            await self.session.message.edit(attachments=[], files=files)

    async def filter_logs(self, inter, category):
        self.category = None if category == "all" else category
        await self.render(inter)

    async def search_actor(self, inter):
        await inter.response.send_modal(ActorModal(self, ensure_admin_state(load_json_data())))

    async def matchmaking_settings(self, view, embed, data, inter):
        from matchmaking import (
            AdminTeamModeLockSelect, AdminOddPolicySelect, AdminVoiceModeLockSelect,
            AdminRoleSourceSelect, AdminRoleModeSelect, matchmaking_settings_embed,
        )
        embed_data = matchmaking_settings_embed(data, admin=True)
        embed.description = embed_data.description
        group = self.section.split(":")[1]
        classes = {"teams": (AdminTeamModeLockSelect, AdminOddPolicySelect), "channels": (AdminVoiceModeLockSelect,), "roles": (AdminRoleSourceSelect, AdminRoleModeSelect)}[group]
        for row, cls in enumerate(classes, 1):
            item = cls(data)
            item.row = row
            self.borrow(view, item)
        if group == "roles":
            self.button(view, data, "set_role", lambda i: self.go(i, "matchmaking:member_role"))
            self.button(view, data, "roles", self.role_report)

    async def role_report(self, inter):
        from matchmaking import role_preferences_admin_embed
        await self.session.acknowledge(inter)
        data = ensure_admin_state(load_json_data())
        await self.render(inter, detail=role_preferences_admin_embed(data))

    async def set_role(self, inter, role):
        from matchmaking import set_player_role_preference
        from persistent_messages import refresh_configured_matchmaking_message
        await self.session.acknowledge(inter)
        member = await get_guild_member(inter.guild, self.member_id)
        data = ensure_admin_state(load_json_data())
        if not member:
            await self.render(inter, text(data, "stale"))
            return
        set_player_role_preference(data, member, role, "admin", actor=interaction_actor(inter))
        writeToJsonFile(jsonFile, data)
        log_event("matchmaking_admin_role_set", actor=interaction_actor(inter), status="success", summary=f"{member.id}: {role}")
        await refresh_configured_matchmaking_message(data)
        await self.refresh_public()
        await self.render(inter, text(data, "saved"))


class MenuInteraction:
    """Keep established operation callbacks inside their originating menu session."""
    def __init__(self, inter, menu):
        self.original = inter
        self.menu = menu
        self.response = self
        self.followup = self

    def __getattr__(self, name):
        return getattr(self.original, name)

    def is_done(self):
        return True

    async def defer(self, **kwargs):
        await self.menu.session.acknowledge(self.original)

    async def send(self, content=None, **kwargs):
        files = kwargs.get("files") or ([kwargs["file"]] if "file" in kwargs else [])
        if files:
            # Attach downloads to the existing private message as well.
            await self.menu.render(self.original, content)
            await self.menu.session.message.edit(attachments=[], files=files)
        else:
            await self.menu.render(self.original, content, kwargs.get("embed"))
        return None

    async def send_message(self, content=None, **kwargs):
        return await self.send(content, **kwargs)

    async def edit_message(self, **kwargs):
        # Rebuild current controls instead of accepting a legacy view.
        await self.menu.render(self.original, kwargs.get("content"))


class SummonerModal(disnake.ui.Modal):
    def __init__(self, menu, platform, data):
        self.menu, self.platform = menu, platform
        super().__init__(title=text(data, "add"), custom_id="menus:add_summoner", components=[
            disnake.ui.TextInput(label=text(data, "name"), custom_id="name", max_length=32),
            disnake.ui.TextInput(label=text(data, "tagline"), custom_id="tagline", max_length=16),
        ], timeout=max(1, menu.session.remaining()))

    async def callback(self, inter):
        from leaderboard import add_summoner_to_data
        data = ensure_admin_state(load_json_data())
        if not await self.menu.session.check(inter, data):
            return
        await self.menu.session.acknowledge(inter)
        success, notice = await add_summoner_to_data(inter.text_values["name"].strip(), inter.text_values["tagline"].strip(), self.platform, PLATFORM_REGIONS[self.platform])
        log_event("leaderboard_summoner_add", actor=interaction_actor(inter), status="success" if success else "error", summary=notice)
        self.menu.section = "players"
        await self.menu.refresh_public()
        await self.menu.render(inter, notice)


class ActorModal(disnake.ui.Modal):
    def __init__(self, menu, data):
        self.menu = menu
        super().__init__(title=text(data, "search"), custom_id="menus:actor", components=[
            disnake.ui.TextInput(label=text(data, "actor"), custom_id="actor", required=False, max_length=100)
        ], timeout=max(1, menu.session.remaining()))

    async def callback(self, inter):
        if not await self.menu.session.check(inter, ensure_admin_state(load_json_data())):
            return
        self.menu.actor_query = inter.text_values["actor"].strip() or None
        await self.menu.render(inter)


class SubscriptionMenu(Menu):
    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.section = "available"
        self.targets = {}

    async def render(self, inter, notice=None):
        from leaderboard import cached_leaderboard_summoners
        from solo_queue import subscription_targets, refresh_target_display_names, is_subscribed
        await self.session.acknowledge(inter)
        data = ensure_admin_state(load_json_data())
        targets = subscription_targets(data, cached_leaderboard_summoners(data))
        self.targets = {target["playerId"]: target for target in targets}
        if self.section == "mine":
            targets = []
            for player_id in data["soloQueueSubscriptions"].get(str(self.session.owner_id), []):
                player_id = str(player_id)
                target = self.targets.get(player_id)
                if not target:
                    name = next((name for name, record in data["summoners"].items() if str(record.get("discordUserId")) == player_id and record.get("discordPrimary") is not False), text(data, "unknown_player", id=player_id))
                    target = {"playerId": player_id, "summonerFullName": name, "displayName": name}
                targets.append(target)
        targets = await refresh_target_display_names(targets, inter.guild)
        view = SessionView(self.session, data)
        self.navigation(view, data, ("available", "mine"))
        embed = disnake.Embed(title=text(data, "subscriptions"), colour=disnake.Colour.dark_teal())
        self.listing(view, embed, data, [option(target["displayName"], target["playerId"], description=target["summonerFullName"][:100]) for target in targets])
        target = next((target for target in targets if target["playerId"] == self.selected), None)
        if target:
            subscribed = is_subscribed(data, self.session.owner_id, self.selected)
            embed.description = f"{target['displayName']} · {target['summonerFullName']}\n{text(data, 'subscribed' if subscribed else 'not_subscribed')}"
            async def apply(interaction):
                await self.change(interaction, target, subscribed)
            self.button(view, data, "unsubscribe" if subscribed else "subscribe", apply, style=disnake.ButtonStyle.danger if subscribed else disnake.ButtonStyle.primary)
        if self.selected:
            self.button(view, data, "back", lambda i: self.go(i, self.section))
        if self.section == "available":
            embed.add_field(name=text(data, "mine"), value=str(len(data["soloQueueSubscriptions"].get(str(self.session.owner_id), []))), inline=True)
        self.describe(embed, data, "subscriptions")
        await self.session.show(inter, embed, view, notice)

    async def change(self, inter, target, expected_subscribed):
        from leaderboard import cached_leaderboard_summoners
        from solo_queue import subscription_targets, is_subscribed, toggle_subscription
        await self.session.acknowledge(inter)
        data = ensure_admin_state(load_json_data())
        subscribed = is_subscribed(data, self.session.owner_id, target["playerId"])
        eligible = {item["playerId"] for item in subscription_targets(data, cached_leaderboard_summoners(data))}
        if subscribed != expected_subscribed or (not subscribed and target["playerId"] not in eligible):
            await self.render(inter, text(data, "stale"))
            return
        success, notice = await toggle_subscription(data, self.session.owner_id, target)
        await self.render(inter, notice)


class MatchmakingMenu(Menu):
    def __init__(self, owner_id):
        super().__init__(owner_id)
        self.section = "teams"

    async def render(self, inter, notice=None, detail=None):
        from matchmaking import (
            PublicTeamModeSelect, PublicOddPolicySelect, PublicVoiceModeSelect,
            PublicRoleSourceSelect, PublicRoleModeSelect, matchmaking_settings_embed,
        )
        await self.session.acknowledge(inter)
        data = ensure_admin_state(load_json_data())
        view = SessionView(self.session, data)
        self.navigation(view, data, ("teams", "channels", "roles"))
        if self.section == "my_role":
            next(c for c in view.children if isinstance(c, MenuSelect)).options[2].default = True
        embed = matchmaking_settings_embed(data)
        if self.section == "my_role":
            from matchmaking import PlayerRoleSelect
            items = [PlayerRoleSelect(self.session.owner_id, data)]
        else:
            classes = {"teams": (PublicTeamModeSelect, PublicOddPolicySelect), "channels": (PublicVoiceModeSelect,), "roles": (PublicRoleSourceSelect, PublicRoleModeSelect)}[self.section]
            items = [cls(data) for cls in classes]
        for row, item in enumerate(items, 1):
            item.row = row
            callback = item.callback
            async def wrapped(interaction, handler=callback):
                await self.session.acknowledge(interaction)
                await handler(MenuInteraction(interaction, self))
            item.callback = wrapped
            view.add_item(item)
        if self.section == "roles":
            view.add_item(MenuButton(t(data, "matchmaking.my_role"), lambda i: self.go(i, "my_role")))
        if any(item.disabled for item in view.children):
            embed.add_field(name=text(data, "locked"), value=text(data, self.section), inline=False)
        if self.section != "teams":
            self.button(view, data, "back", lambda i: self.go(i, "teams"))
        if not detail:
            self.describe(embed, data, "matchmaking")
        await self.session.show(inter, detail or embed, view, notice)


async def open_admin_menu(inter, section="home"):
    menu = AdminMenu(inter.author.id)
    if not await menu.session.check(inter, {}):
        return
    await inter.response.defer(ephemeral=True)
    menu.section = section
    await menu.render(inter)


async def open_subscription_menu(inter):
    await inter.response.defer(ephemeral=True)
    await SubscriptionMenu(inter.author.id).render(inter)


async def open_matchmaking_menu(inter, section="teams"):
    from matchmaking import require_queued_settings_user
    await inter.response.defer(ephemeral=True)
    if not await require_queued_settings_user(inter, ensure_admin_state(load_json_data())):
        return
    menu = MatchmakingMenu(inter.author.id)
    menu.section = section
    await menu.render(inter)
