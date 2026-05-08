import os
from datetime import datetime, timedelta, timezone
from io import BytesIO

import disnake

from bot_runtime import AUDIT_CATEGORY_LABELS, AUDIT_CATEGORY_PREFIXES, MAX_SELECT_OPTIONS, TASKS, bot
from discord_helpers import get_discord_channel, get_guild_member, require_admin_interaction, send_ephemeral, send_ephemeral_followup, send_ephemeral_response
from i18n import available_languages, language_label, t
from leaderboard import add_summoner_to_data, estimate_leaderboard_api_calls, force_leaderboard_refresh, format_summoner_summary, remove_summoner_from_data
from linked_accounts import LinkedAccountsAdminView, linked_accounts_admin_embed, rebuild_discord_links_from_summoners
from matchmaking import MatchmakingAdminView, matchmaking_admin_embed
from persistent_messages import configure_leaderboard_channel, configure_matchmaking_channel, configured_message_status, recreate_persistent_messages, refresh_configured_admin_message, refresh_configured_matchmaking_message
from state import admin_channel_id, effective_matchmaking_separate_channels, effective_matchmaking_team_mode, effective_odd_players_policy, ensure_admin_state, ensure_matchmaking_state, forced_mode_text, leaderboard_channel_id, leaderboard_chat_commands_enabled, load_json_data, matchmaking_channel_id, missing_bot_channel_permissions, odd_players_policy_label, team_mode_label, team_mode_lock_text, voice_mode_label
from utils.auditUtils import AUDIT_LOG_PATH, interaction_actor, log_event, read_audit_events, recent_error_events
from utils.commonUtils import jsonFile, platforms, regions
from utils.dataUtils import riotBackoffRemaining, riotBackoffTimestamp
from utils.jsonUtils import writeToJsonFile


def format_log_event(event):
    timestamp = event.get("timestamp", "-")
    timestamp = timestamp.replace("T", " ")[:19]
    actor = event.get("actorName") or event.get("actorId") or "system"
    status = event.get("status", "info")
    summary = event.get("summary", "")
    return f"`{timestamp}` **{event.get('event', 'event')}** [{status}] {actor}: {summary}"[:1000]

def audit_event_category(event):
    event_name = event.get("event", "")
    for category, prefixes in AUDIT_CATEGORY_PREFIXES.items():
        if any(event_name.startswith(prefix) for prefix in prefixes):
            return category
    return "other"

def parse_audit_timestamp(event):
    timestamp = event.get("timestamp")
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None

def filter_audit_events(category=None, actor_query=None, since=None, limit=10):
    events = read_audit_events()
    if category:
        events = [event for event in events if audit_event_category(event) == category]
    if actor_query:
        query = actor_query.lower()
        events = [
            event for event in events
            if query in str(event.get("actorId", "")).lower()
            or query in str(event.get("actorName", "")).lower()
        ]
    if since:
        events = [
            event for event in events
            if (parse_audit_timestamp(event) or datetime.min.replace(tzinfo=timezone.utc)) >= since
        ]
    if limit:
        return events[-limit:]
    return events

def audit_logs_embed(title="Audit logs", category=None, actor_query=None, limit=10):
    events = filter_audit_events(category=category, actor_query=actor_query, limit=limit)
    embed = disnake.Embed(
        title=title,
        colour=disnake.Colour.dark_teal(),
        timestamp=datetime.now()
    )
    if category:
        embed.add_field(name="Category", value=AUDIT_CATEGORY_LABELS.get(category, category), inline=True)
    if actor_query:
        embed.add_field(name="Actor filter", value=actor_query[:100], inline=True)
    if not events:
        embed.description = "No matching audit logs found."
        return embed

    value = "\n".join(format_log_event(event) for event in events)
    embed.description = value[-4000:]
    return embed

def audit_summary_24h_embed():
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    events = filter_audit_events(since=since, limit=None)
    error_events = [event for event in events if event.get("status") == "error"]
    critical_events = [
        event for event in events
        if audit_event_category(event) in ["admin", "operations", "leaderboard", "matchmaking"]
    ]
    category_counts = {}
    for event in events:
        category = audit_event_category(event)
        category_counts[category] = category_counts.get(category, 0) + 1

    embed = disnake.Embed(
        title="Audit summary - last 24h",
        colour=disnake.Colour.dark_teal(),
        timestamp=datetime.now()
    )
    embed.add_field(name="Total events", value=str(len(events)), inline=True)
    embed.add_field(name="Errors", value=str(len(error_events)), inline=True)
    embed.add_field(name="Critical/admin events", value=str(len(critical_events)), inline=True)

    if category_counts:
        category_lines = [
            f"**{AUDIT_CATEGORY_LABELS.get(category, category.title())}:** {count}"
            for category, count in sorted(category_counts.items(), key=lambda item: item[0])
        ]
        embed.add_field(name="By category", value="\n".join(category_lines)[:1024], inline=False)
    else:
        embed.add_field(name="By category", value="No audit events in the last 24h.", inline=False)

    if error_events:
        embed.add_field(name="Recent errors", value="\n".join(format_log_event(event) for event in error_events[-5:])[-1024:], inline=False)
    return embed

def admin_embed(json_data):
    ensure_admin_state(json_data)
    rebuild_discord_links_from_summoners(json_data)
    summoners = json_data.get("summoners") or {}
    queue = json_data.get("matchmakingQueue", [])
    linked_accounts = json_data.get("discordLinks", {})
    leaderboard_channel = leaderboard_channel_id(json_data)
    matchmaking_channel = matchmaking_channel_id(json_data)
    admin_channel = admin_channel_id(json_data)

    embed = disnake.Embed(
        title=t(json_data, "admin.main.title"),
        description=t(json_data, "admin.main.description"),
        colour=disnake.Colour.dark_teal(),
        timestamp=datetime.now()
    )
    embed.add_field(name=t(json_data, "admin.main.leaderboard_channel"), value=f"<#{leaderboard_channel}>", inline=True)
    embed.add_field(name=t(json_data, "admin.main.matchmaking_channel"), value=f"<#{matchmaking_channel}>", inline=True)
    embed.add_field(name=t(json_data, "admin.main.admin_channel"), value=f"<#{admin_channel}>", inline=True)
    embed.add_field(name=t(json_data, "admin.main.leaderboard_users"), value=str(len(summoners)), inline=True)
    embed.add_field(name=t(json_data, "admin.main.linked_discord_users"), value=str(len(linked_accounts)), inline=True)
    embed.add_field(name=t(json_data, "admin.main.matchmaking_queue"), value=f"{len(queue)}/10", inline=True)
    embed.add_field(name=t(json_data, "admin.main.voice_mode"), value=f"{voice_mode_label(effective_matchmaking_separate_channels(json_data), json_data)} ({forced_mode_text(json_data)})", inline=True)
    embed.add_field(name=t(json_data, "admin.main.team_mode"), value=f"{team_mode_label(effective_matchmaking_team_mode(json_data), json_data)} ({team_mode_lock_text(json_data)})", inline=True)
    embed.add_field(name=t(json_data, "admin.main.odd_players"), value=odd_players_policy_label(effective_odd_players_policy(json_data), json_data), inline=True)
    embed.add_field(name=t(json_data, "admin.main.leaderboard_chat_commands"), value=t(json_data, "common.enabled") if leaderboard_chat_commands_enabled(json_data) else t(json_data, "common.disabled"), inline=True)
    embed.add_field(name=t(json_data, "admin.main.leaderboard_status"), value=json_data.get("leaderboardLastUpdateStatus") or t(json_data, "common.unknown"), inline=True)
    embed.set_footer(text=t(json_data, "admin.main.footer"))
    return embed

def status_admin_embed(json_data):
    ensure_admin_state(json_data)
    backoff_remaining = int(riotBackoffRemaining())
    if backoff_remaining > 0:
        backoff_until = datetime.fromtimestamp(riotBackoffTimestamp()).strftime("%H:%M:%S")
        backoff_text = f"Active until {backoff_until} ({backoff_remaining}s remaining)"
    else:
        backoff_text = "Inactive"

    last_update = json_data.get("leaderboardLastUpdateAt") or "Never"
    update_mode = json_data.get("leaderboardLastUpdateMode") or "-"
    update_status = json_data.get("leaderboardLastUpdateStatus") or "-"
    estimated_calls = estimate_leaderboard_api_calls(json_data)
    stored_estimate = json_data.get("leaderboardLastEstimatedApiCalls", 0)
    last_error = json_data.get("lastRiotError") or {}
    error_summary = last_error.get("summary", "No Riot errors recorded.")

    embed = disnake.Embed(
        title="Status / Logs",
        description="Operational status for leaderboard updates and Riot API usage.",
        colour=disnake.Colour.dark_teal(),
        timestamp=datetime.now()
    )
    embed.add_field(name="Riot backoff", value=backoff_text, inline=False)
    embed.add_field(name="Last leaderboard update", value=f"{last_update}\nMode: **{update_mode}**\nStatus: **{update_status}**", inline=False)
    embed.add_field(name="Estimated API calls", value=f"Next normal cycle: **{estimated_calls}**\nLast stored estimate: **{stored_estimate}**\nMatch history/details only refresh on new games, daily, or force.", inline=False)
    embed.add_field(name="Last Riot error", value=error_summary[:1024], inline=False)

    errors = recent_error_events(5)
    if errors:
        embed.add_field(name="Recent errors", value="\n".join(format_log_event(event) for event in errors)[-1024:], inline=False)
    else:
        embed.add_field(name="Recent errors", value="No recent errors.", inline=False)
    return embed

def recent_logs_embed(limit=10):
    return audit_logs_embed(title="Recent audit logs", limit=limit)

def task_status(task):
    if task is None:
        return "Not registered"
    return "Running" if task.is_running() else "Stopped"

def file_size_text(path):
    if not os.path.exists(path):
        return "missing"
    size = os.path.getsize(path)
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{round(size / 1024, 1)} KB"
    return f"{round(size / (1024 * 1024), 1)} MB"

def data_summary(json_data):
    ensure_admin_state(json_data)
    return (
        f"Summoners: **{len(json_data.get('summoners') or {})}**\n"
        f"Matchmaking queue: **{len(json_data.get('matchmakingQueue') or [])}/10**\n"
        f"Draft active: **{'Yes' if json_data.get('matchmakingDraft') else 'No'}**\n"
        f"Match cache: **{len(json_data.get('matchData') or {})}**\n"
        f"Audit log: **{file_size_text(AUDIT_LOG_PATH)}**\n"
        f"Data file: **{file_size_text(jsonFile)}**"
    )

async def operations_health_embed(json_data):
    ensure_admin_state(json_data)
    backoff_remaining = int(riotBackoffRemaining())
    if backoff_remaining > 0:
        backoff_text = f"Active for {backoff_remaining}s"
    else:
        backoff_text = "Inactive"

    embed = disnake.Embed(
        title="Operations health check",
        colour=disnake.Colour.dark_teal(),
        timestamp=datetime.now()
    )
    embed.add_field(
        name="Persistent messages",
        value=(
            f"Admin: {await configured_message_status(json_data, 'admin')}\n"
            f"Leaderboard: {await configured_message_status(json_data, 'leaderboard')}\n"
            f"Matchmaking: {await configured_message_status(json_data, 'matchmaking')}"
        )[:1024],
        inline=False
    )
    embed.add_field(
        name="Tasks",
        value=(
            f"Leaderboard loop: **{task_status(TASKS.get('updateRaceImage'))}**\n"
            f"Patch loop: **{task_status(TASKS.get('updatePatchNotes'))}**\n"
            f"Captain draft timeout: **{task_status(TASKS.get('captainDraftTimeout'))}**"
        ),
        inline=False
    )
    embed.add_field(
        name="Riot / leaderboard",
        value=(
            f"Backoff: **{backoff_text}**\n"
            f"Last update: **{json_data.get('leaderboardLastUpdateStatus') or 'Unknown'}**\n"
            f"Last update at: **{json_data.get('leaderboardLastUpdateAt') or 'Never'}**\n"
            f"Last error: {(json_data.get('lastRiotError') or {}).get('summary', 'None')}"
        )[:1024],
        inline=False
    )
    embed.add_field(name="Data", value=data_summary(json_data), inline=False)
    return embed

async def bot_permission_report(guild, json_data):
    bot_member = guild.me or await get_guild_member(guild, bot.user.id)
    checks = [
        ("Admin", admin_channel_id(json_data), ["view_channel", "send_messages", "embed_links", "read_message_history"]),
        ("Leaderboard", leaderboard_channel_id(json_data), ["view_channel", "send_messages", "embed_links", "read_message_history"]),
        ("Matchmaking", matchmaking_channel_id(json_data), ["view_channel", "send_messages", "embed_links", "read_message_history", "manage_channels", "move_members"]),
    ]
    labels = {
        "view_channel": "View Channel",
        "send_messages": "Send Messages",
        "embed_links": "Embed Links",
        "read_message_history": "Read Message History",
        "manage_channels": "Manage Channels",
        "move_members": "Move Members",
    }
    lines = []
    for name, channel_id, permissions_to_check in checks:
        channel = await get_discord_channel(channel_id)
        if not channel:
            lines.append(f"**{name}:** channel missing (<#{channel_id}>)")
            continue
        permissions = channel.permissions_for(bot_member)
        missing = [labels[item] for item in permissions_to_check if not getattr(permissions, item, False)]
        if missing:
            lines.append(f"**{name}:** missing {', '.join(missing)} in {channel.mention}")
        else:
            lines.append(f"**{name}:** OK in {channel.mention}")
    return "\n".join(lines)

async def permission_report_embed(guild, json_data):
    embed = disnake.Embed(
        title="Permission check",
        description=(await bot_permission_report(guild, json_data))[:4000],
        colour=disnake.Colour.dark_teal(),
        timestamp=datetime.now()
    )
    return embed

class AddSummonerModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(label="Name", custom_id="name", required=True, max_length=32),
            disnake.ui.TextInput(label="Tagline", custom_id="tagline", required=True, max_length=16),
            disnake.ui.TextInput(label="Platform", custom_id="platform", required=True, max_length=8, placeholder="EUW1"),
            disnake.ui.TextInput(label="Region", custom_id="region", required=True, max_length=12, placeholder="EUROPE"),
        ]
        super().__init__(title="Add summoner", custom_id="admin:add_summoner_modal", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        if not await require_admin_interaction(inter):
            return

        name = inter.text_values["name"].strip()
        tagline = inter.text_values["tagline"].strip()
        platform = inter.text_values["platform"].strip().upper()
        region = inter.text_values["region"].strip().upper()

        if platform not in platforms:
            await send_ephemeral(inter, f"Invalid platform. Use one of: {', '.join(platforms)}")
            return
        if region not in regions:
            await send_ephemeral(inter, f"Invalid region. Use one of: {', '.join(regions)}")
            return

        await inter.response.defer(ephemeral=True)
        success, message = await add_summoner_to_data(name, tagline, platform, region)
        log_event("leaderboard_summoner_add", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"name": name, "tagline": tagline, "platform": platform, "region": region})
        await refresh_configured_admin_message()
        await send_ephemeral_followup(inter, message)

class LeaderboardRemoveSelect(disnake.ui.Select):
    def __init__(self, summoners, json_data=None):
        json_data = ensure_admin_state(json_data or load_json_data())
        options = [
            disnake.SelectOption(label=summoner[:100], value=summoner)
            for summoner in summoners[:MAX_SELECT_OPTIONS]
        ]
        super().__init__(
            placeholder=t(json_data, "admin.leaderboard_users.remove_placeholder"),
            min_values=1,
            max_values=1,
            options=options,
            custom_id="admin:leaderboard:remove"
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        summoner = self.values[0]
        name, tagline = summoner.split("#", 1)
        success, message = remove_summoner_from_data(name, tagline)
        log_event("leaderboard_summoner_remove", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"summoner": summoner})
        await refresh_configured_admin_message()
        json_data = load_json_data()
        await inter.response.edit_message(embed=leaderboard_users_admin_embed(json_data), view=LeaderboardUsersAdminView(json_data))
        await send_ephemeral_followup(inter, message)

class SettingsAdminView(disnake.ui.View):
    def __init__(self, json_data=None):
        super().__init__(timeout=300)
        json_data = ensure_admin_state(json_data or load_json_data())
        self.add_item(LanguageSelect(json_data))
        for child in self.children:
            if getattr(child, "custom_id", None) == "admin:settings:leaderboard":
                child.placeholder = t(json_data, "admin.settings.set_leaderboard_channel")
            elif getattr(child, "custom_id", None) == "admin:settings:matchmaking":
                child.placeholder = t(json_data, "admin.settings.set_matchmaking_channel")
            elif getattr(child, "custom_id", None) == "admin:settings:leaderboard_chat_commands":
                child.label = t(json_data, "admin.settings.toggle_commands")

    @disnake.ui.channel_select(placeholder="Set leaderboard channel", channel_types=[disnake.ChannelType.text], custom_id="admin:settings:leaderboard", min_values=1, max_values=1)
    async def leaderboard_channel(self, select: disnake.ui.ChannelSelect, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        channel = select.values[0]
        bot_member = inter.guild.me or await get_guild_member(inter.guild, bot.user.id)
        missing_permissions = missing_bot_channel_permissions(channel, bot_member)
        if missing_permissions:
            await send_ephemeral(inter, f"I am missing permissions in {channel.mention}: {', '.join(missing_permissions)}.")
            return

        await inter.response.defer(ephemeral=True)
        message = await configure_leaderboard_channel(channel, interaction_actor(inter))
        await send_ephemeral_followup(inter, message)

    @disnake.ui.channel_select(placeholder="Set matchmaking channel", channel_types=[disnake.ChannelType.text], custom_id="admin:settings:matchmaking", min_values=1, max_values=1)
    async def matchmaking_channel(self, select: disnake.ui.ChannelSelect, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        channel = select.values[0]
        bot_member = inter.guild.me or await get_guild_member(inter.guild, bot.user.id)
        missing_permissions = missing_bot_channel_permissions(channel, bot_member)
        if missing_permissions:
            await send_ephemeral(inter, f"I am missing permissions in {channel.mention}: {', '.join(missing_permissions)}.")
            return

        await inter.response.defer(ephemeral=True)
        message = await configure_matchmaking_channel(channel, interaction_actor(inter))
        await send_ephemeral_followup(inter, message)

    @disnake.ui.button(label="Toggle /add /remove", style=disnake.ButtonStyle.gray, custom_id="admin:settings:leaderboard_chat_commands", row=2)
    async def toggle_leaderboard_chat_commands(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        json_data["leaderboardChatCommandsEnabled"] = not leaderboard_chat_commands_enabled(json_data)
        writeToJsonFile(jsonFile, json_data)
        enabled = leaderboard_chat_commands_enabled(json_data)
        status = "enabled" if enabled else "disabled"
        log_event("leaderboard_chat_commands_toggle", actor=interaction_actor(inter), status="success", summary=f"Leaderboard /add and /remove commands {status}.", details={"enabled": enabled})
        await refresh_configured_admin_message(json_data)
        await inter.response.edit_message(embed=settings_admin_embed(json_data), view=SettingsAdminView(json_data))


class LanguageSelect(disnake.ui.Select):
    def __init__(self, json_data):
        current_language = json_data.get("botLanguage", "en")
        options = [
            disnake.SelectOption(label=label, value=language, default=language == current_language)
            for language, label in available_languages().items()
        ]
        super().__init__(
            placeholder=t(json_data, "admin.settings.language_placeholder"),
            min_values=1,
            max_values=1,
            options=options,
            custom_id="admin:settings:language",
            row=1
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        json_data["botLanguage"] = self.values[0]
        ensure_admin_state(json_data)
        writeToJsonFile(jsonFile, json_data)
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        language = language_label(json_data["botLanguage"])
        log_event("admin_language_changed", actor=interaction_actor(inter), status="success", summary=f"Bot language changed to {language}.", details={"language": json_data["botLanguage"]})
        await inter.response.edit_message(embed=settings_admin_embed(json_data), view=SettingsAdminView(json_data))
        await send_ephemeral_followup(inter, t(json_data, "admin.settings.language_changed", language=language))

class LeaderboardUsersAdminView(disnake.ui.View):
    def __init__(self, json_data):
        super().__init__(timeout=300)
        json_data = ensure_admin_state(json_data)
        summoners = [summoner for summoner in (json_data.get("summoners") or {}).keys()]
        if summoners:
            self.add_item(LeaderboardRemoveSelect(summoners, json_data))
        for child in self.children:
            if getattr(child, "custom_id", None) == "admin:leaderboard:add":
                child.label = t(json_data, "admin.leaderboard_users.add_button")

    @disnake.ui.button(label="Add summoner", style=disnake.ButtonStyle.green, custom_id="admin:leaderboard:add")
    async def add_summoner(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        await inter.response.send_modal(AddSummonerModal())

class AuditActorSearchModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(
                label="Actor ID or name",
                custom_id="actor_query",
                required=True,
                max_length=100,
                placeholder="Discord ID, display name, or system"
            )
        ]
        super().__init__(title="Search audit logs by actor", custom_id="admin:audit:actor_search_modal", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        if not await require_admin_interaction(inter):
            return

        actor_query = inter.text_values["actor_query"].strip()
        log_event("operations_audit_actor_search", actor=interaction_actor(inter), status="success", summary=f"Audit actor search requested for {actor_query}.")
        await send_ephemeral_response(inter, embed=audit_logs_embed(title="Audit logs by actor", actor_query=actor_query, limit=15))

class AuditCategorySelect(disnake.ui.Select):
    def __init__(self):
        options = [
            disnake.SelectOption(label=label, value=category)
            for category, label in AUDIT_CATEGORY_LABELS.items()
        ]
        super().__init__(
            placeholder="Filter audit logs by category",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="admin:audit:category",
            row=3
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        category = self.values[0]
        label = AUDIT_CATEGORY_LABELS.get(category, category)
        log_event("operations_audit_category_filter", actor=interaction_actor(inter), status="success", summary=f"Audit category filter requested for {label}.")
        await send_ephemeral_response(inter, embed=audit_logs_embed(title=f"{label} audit logs", category=category, limit=15))

class StatusLogsAdminView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(AuditCategorySelect())

    @disnake.ui.button(label="Refresh status", style=disnake.ButtonStyle.blurple, custom_id="admin:status:refresh", row=0)
    async def refresh_status(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        await inter.response.edit_message(embed=status_admin_embed(json_data), view=StatusLogsAdminView())

    @disnake.ui.button(label="Health check", style=disnake.ButtonStyle.green, custom_id="admin:status:health", row=0)
    async def health_check(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        log_event("operations_health_check", actor=interaction_actor(inter), status="success", summary="Health check requested.")
        await send_ephemeral_response(inter, embed=await operations_health_embed(json_data))

    @disnake.ui.button(label="Test permissions", style=disnake.ButtonStyle.gray, custom_id="admin:status:permissions", row=0)
    async def test_permissions(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        embed = await permission_report_embed(inter.guild, json_data)
        log_event("operations_permission_check", actor=interaction_actor(inter), status="success", summary="Permission check requested.")
        await send_ephemeral_response(inter, embed=embed)

    @disnake.ui.button(label="Recreate messages", style=disnake.ButtonStyle.blurple, custom_id="admin:status:recreate_messages", row=1)
    async def recreate_messages(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        await inter.response.defer(ephemeral=True)
        json_data = ensure_admin_state(load_json_data())
        summary = await recreate_persistent_messages(json_data)
        log_event("operations_recreate_messages", actor=interaction_actor(inter), status="success", summary=summary)
        await send_ephemeral_followup(inter, f"Persistent messages checked/recreated:\n{summary}")

    @disnake.ui.button(label="Download data backup", style=disnake.ButtonStyle.gray, custom_id="admin:status:data_backup", row=1)
    async def download_data_backup(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        if not os.path.exists(jsonFile):
            await send_ephemeral_response(inter, "No data file exists yet.")
            return

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        with open(jsonFile, "rb") as file:
            data_file = disnake.File(BytesIO(file.read()), filename=f"data-backup-{timestamp}.json")

        files = [data_file]
        if os.path.exists(AUDIT_LOG_PATH):
            files.append(disnake.File(AUDIT_LOG_PATH, filename=f"audit-{timestamp}.jsonl"))

        log_event("operations_data_backup_download", actor=interaction_actor(inter), status="success", summary="Data backup downloaded.", details={"includedAuditLog": os.path.exists(AUDIT_LOG_PATH)})
        await send_ephemeral_response(inter, "Data backup:", files=files)

    @disnake.ui.button(label="Force leaderboard refresh", style=disnake.ButtonStyle.red, custom_id="admin:status:force_leaderboard", row=1)
    async def force_refresh_leaderboard(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        await inter.response.defer(ephemeral=True)
        success, message, json_data = await force_leaderboard_refresh(interaction_actor(inter))
        await refresh_configured_admin_message(json_data)
        await send_ephemeral_followup(inter, message)

    @disnake.ui.button(label="View recent logs", style=disnake.ButtonStyle.gray, custom_id="admin:status:recent_logs", row=2)
    async def view_recent_logs(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        await send_ephemeral_response(inter, embed=recent_logs_embed())

    @disnake.ui.button(label="Audit summary 24h", style=disnake.ButtonStyle.blurple, custom_id="admin:audit:summary_24h", row=4)
    async def audit_summary_24h(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        log_event("operations_audit_summary_24h", actor=interaction_actor(inter), status="success", summary="Audit summary requested.")
        await send_ephemeral_response(inter, embed=audit_summary_24h_embed())

    @disnake.ui.button(label="Search actor", style=disnake.ButtonStyle.gray, custom_id="admin:audit:actor_search", row=4)
    async def search_audit_actor(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        await inter.response.send_modal(AuditActorSearchModal())

class AdminView(disnake.ui.View):
    def __init__(self, json_data=None):
        super().__init__(timeout=None)
        json_data = ensure_admin_state(json_data or load_json_data())
        labels = {
            "admin:settings": "admin.settings.title",
            "admin:leaderboard": "admin.main.leaderboard_users",
            "admin:links": "linked_accounts.title",
            "admin:matchmaking": "matchmaking.title",
            "admin:status": "admin.status.title",
            "admin:refresh": "admin.status.refresh_status",
        }
        for child in self.children:
            key = labels.get(getattr(child, "custom_id", None))
            if key:
                child.label = t(json_data, key)

    @disnake.ui.button(label="App settings", style=disnake.ButtonStyle.blurple, custom_id="admin:settings")
    async def settings(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        await send_ephemeral_response(inter, embed=settings_admin_embed(json_data), view=SettingsAdminView(json_data))

    @disnake.ui.button(label="Leaderboard users", style=disnake.ButtonStyle.green, custom_id="admin:leaderboard")
    async def leaderboard_users(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = load_json_data()
        await send_ephemeral_response(inter, embed=leaderboard_users_admin_embed(json_data), view=LeaderboardUsersAdminView(json_data))

    @disnake.ui.button(label="Linked accounts", style=disnake.ButtonStyle.green, custom_id="admin:links", row=1)
    async def linked_accounts(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        rebuild_discord_links_from_summoners(json_data)
        writeToJsonFile(jsonFile, json_data)
        await send_ephemeral_response(inter, embed=linked_accounts_admin_embed(json_data), view=LinkedAccountsAdminView(json_data))

    @disnake.ui.button(label="Matchmaking", style=disnake.ButtonStyle.gray, custom_id="admin:matchmaking")
    async def matchmaking(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_matchmaking_state(load_json_data())
        await send_ephemeral_response(inter, embed=matchmaking_admin_embed(json_data), view=MatchmakingAdminView(json_data))

    @disnake.ui.button(label="Status / Logs", style=disnake.ButtonStyle.blurple, custom_id="admin:status")
    async def status_logs(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        await send_ephemeral_response(inter, embed=status_admin_embed(json_data), view=StatusLogsAdminView())

    @disnake.ui.button(label="Refresh", style=disnake.ButtonStyle.gray, custom_id="admin:refresh")
    async def refresh_admin_panel(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        await refresh_configured_matchmaking_message(json_data)
        await refresh_configured_admin_message(json_data)
        await send_ephemeral_response(inter, t(json_data, "admin.main.refreshed"))

def settings_admin_embed(json_data):
    ensure_admin_state(json_data)
    commands_status = t(json_data, "common.enabled") if leaderboard_chat_commands_enabled(json_data) else t(json_data, "common.disabled")
    embed = disnake.Embed(
        title=t(json_data, "admin.settings.title"),
        description=t(json_data, "admin.settings.description"),
        colour=disnake.Colour.dark_teal()
    )
    embed.add_field(name=t(json_data, "admin.settings.leaderboard"), value=f"<#{leaderboard_channel_id(json_data)}>", inline=True)
    embed.add_field(name=t(json_data, "admin.settings.matchmaking"), value=f"<#{matchmaking_channel_id(json_data)}>", inline=True)
    embed.add_field(name=t(json_data, "admin.settings.language"), value=f"**{language_label(json_data.get('botLanguage', 'en'))}**", inline=True)
    embed.add_field(name=t(json_data, "admin.settings.commands"), value=f"**{commands_status}**\n{t(json_data, 'admin.settings.commands_help')}", inline=False)
    return embed

def leaderboard_users_admin_embed(json_data):
    ensure_admin_state(json_data)
    summoners = json_data.get("summoners") or {}
    embed = disnake.Embed(
        title=t(json_data, "admin.leaderboard_users.title"),
        description=t(json_data, "admin.leaderboard_users.description", count=len(summoners)),
        colour=disnake.Colour.green()
    )
    embed.add_field(name=t(json_data, "admin.leaderboard_users.current_users"), value=format_summoner_summary(json_data), inline=False)
    if len(summoners) > MAX_SELECT_OPTIONS:
        embed.set_footer(text=t(json_data, "admin.leaderboard_users.footer_limit", count=MAX_SELECT_OPTIONS))
    return embed
