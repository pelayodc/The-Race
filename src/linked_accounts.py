import disnake

from bot_runtime import MAX_SELECT_OPTIONS
from discord_helpers import get_guild_member, require_admin_interaction, send_ephemeral_followup, send_ephemeral_response
from i18n import t
from state import ensure_admin_state, load_json_data, utc_now_iso
from utils.auditUtils import interaction_actor, log_event
from utils.commonUtils import jsonFile
from utils.jsonUtils import writeToJsonFile


async def refresh_configured_admin_message(json_data=None):
    from persistent_messages import refresh_configured_admin_message as refresh

    return await refresh(json_data)


def normalize_tagline(tagline):
    return tagline.replace("#", "").strip()

def find_summoner_key(json_data, name, tagline):
    summoners = json_data.get("summoners") or {}
    summoner_full_name = f"{name}#{normalize_tagline(tagline)}"
    for summoner in summoners:
        if summoner.lower() == summoner_full_name.lower():
            return summoner
    return None

def parse_discord_user_id(value):
    value = str(value).strip()
    for char in ["<", ">", "@", "!"]:
        value = value.replace(char, "")
    return value if value.isdigit() else None

async def discord_user_from_text(guild, value):
    user_id = parse_discord_user_id(value)
    if not user_id:
        return None
    return await get_guild_member(guild, user_id)

def rebuild_discord_links_from_summoners(json_data):
    links = {}
    summoners = json_data.get("summoners") or {}

    for summoner_name, summoner_data in summoners.items():
        discord_user_id = summoner_data.get("discordUserId")
        if not discord_user_id:
            continue

        discord_user_id = str(discord_user_id)
        link = links.setdefault(discord_user_id, {
            "displayName": summoner_data.get("discordDisplayName") or discord_user_id,
            "summoners": [],
            "primarySummoner": None
        })
        if summoner_name not in link["summoners"]:
            link["summoners"].append(summoner_name)
        if summoner_data.get("discordPrimary"):
            link["primarySummoner"] = summoner_name

    for discord_user_id, link in links.items():
        if not link["primarySummoner"] and link["summoners"]:
            link["primarySummoner"] = link["summoners"][0]
            summoners[link["primarySummoner"]]["discordPrimary"] = True

    json_data["discordLinks"] = links
    return json_data

def linked_summoners_for_user(json_data, user_id):
    rebuild_discord_links_from_summoners(json_data)
    link = json_data.get("discordLinks", {}).get(str(user_id), {})
    return link.get("summoners", [])

def primary_summoner_for_user(json_data, user_id):
    rebuild_discord_links_from_summoners(json_data)
    link = json_data.get("discordLinks", {}).get(str(user_id), {})
    primary_summoner = link.get("primarySummoner")
    if primary_summoner in (json_data.get("summoners") or {}):
        return primary_summoner
    summoners = link.get("summoners", [])
    return summoners[0] if summoners else None

def link_summoner_to_discord(json_data, user, summoner_full_name, primary=True):
    rebuild_discord_links_from_summoners(json_data)
    summoners = json_data.get("summoners") or {}
    if summoner_full_name not in summoners:
        return False, t(json_data, "leaderboard.not_added", summoner=summoner_full_name)

    user_id = str(user.id)
    display_name = user.display_name
    current_user_id = summoners[summoner_full_name].get("discordUserId")
    if current_user_id and str(current_user_id) != user_id:
        return False, t(json_data, "linked_accounts.already_linked_user", summoner=summoner_full_name, user_id=current_user_id)

    link = json_data["discordLinks"].setdefault(user_id, {
        "displayName": display_name,
        "summoners": [],
        "primarySummoner": None
    })
    link["displayName"] = display_name
    if summoner_full_name not in link["summoners"]:
        link["summoners"].append(summoner_full_name)

    summoners[summoner_full_name]["discordUserId"] = user_id
    summoners[summoner_full_name]["discordDisplayName"] = display_name
    summoners[summoner_full_name]["discordLinkedAt"] = utc_now_iso()

    if primary or not link.get("primarySummoner"):
        for linked_summoner in link["summoners"]:
            if linked_summoner in summoners:
                summoners[linked_summoner]["discordPrimary"] = linked_summoner == summoner_full_name
        link["primarySummoner"] = summoner_full_name
    else:
        summoners[summoner_full_name]["discordPrimary"] = False

    json_data["discordLinkRequests"] = [
        request for request in (json_data.get("discordLinkRequests") or [])
        if not isinstance(request, dict) or request.get("summonerFullName") != summoner_full_name
    ]
    return True, t(json_data, "linked_accounts.linked", summoner=summoner_full_name, user_id=user_id)

def unlink_summoner_from_discord(json_data, summoner_full_name):
    rebuild_discord_links_from_summoners(json_data)
    summoners = json_data.get("summoners") or {}
    if summoner_full_name not in summoners:
        return False, t(json_data, "leaderboard.not_added", summoner=summoner_full_name)

    user_id = summoners[summoner_full_name].get("discordUserId")
    if not user_id:
        return False, t(json_data, "linked_accounts.not_linked", summoner=summoner_full_name)

    user_id = str(user_id)
    for key in ["discordUserId", "discordDisplayName", "discordLinkedAt", "discordPrimary"]:
        summoners[summoner_full_name].pop(key, None)

    link = json_data.get("discordLinks", {}).get(user_id)
    if link:
        link["summoners"] = [summoner for summoner in link.get("summoners", []) if summoner != summoner_full_name]
        if link.get("primarySummoner") == summoner_full_name:
            link["primarySummoner"] = link["summoners"][0] if link["summoners"] else None
            if link["primarySummoner"] in summoners:
                summoners[link["primarySummoner"]]["discordPrimary"] = True
        if not link["summoners"]:
            del json_data["discordLinks"][user_id]

    return True, t(json_data, "linked_accounts.unlinked", summoner=summoner_full_name, user_id=user_id)

def set_primary_summoner_for_user(json_data, user, summoner_full_name):
    rebuild_discord_links_from_summoners(json_data)
    summoners = json_data.get("summoners") or {}
    user_id = str(user.id)
    link = json_data.get("discordLinks", {}).get(user_id)
    if not link or summoner_full_name not in link.get("summoners", []):
        return False, t(json_data, "linked_accounts.not_linked_to_user", summoner=summoner_full_name, user_id=user_id)

    for linked_summoner in link["summoners"]:
        if linked_summoner in summoners:
            summoners[linked_summoner]["discordPrimary"] = linked_summoner == summoner_full_name
    link["displayName"] = user.display_name
    link["primarySummoner"] = summoner_full_name
    return True, t(json_data, "linked_accounts.primary_set", summoner=summoner_full_name, user_id=user_id)

def discord_display_name(user):
    return getattr(user, "display_name", None) or getattr(user, "name", None) or str(user.id)

def discord_link_request_id(user_id, summoner_full_name):
    return f"{user_id}:{summoner_full_name.lower()}"

def discord_link_requests(json_data):
    ensure_admin_state(json_data)
    requests = []
    seen = set()
    summoners = json_data.get("summoners") or {}
    for request in json_data.get("discordLinkRequests") or []:
        if not isinstance(request, dict):
            continue
        user_id = str(request.get("discordUserId") or "")
        summoner = request.get("summonerFullName")
        if not user_id or summoner not in summoners:
            continue
        if summoners[summoner].get("discordUserId"):
            continue
        request_id = request.get("id") or discord_link_request_id(user_id, summoner)
        if request_id in seen:
            continue
        request["id"] = request_id
        request["discordUserId"] = user_id
        request["summonerFullName"] = summoner
        request.setdefault("discordDisplayName", user_id)
        request.setdefault("requestedAt", utc_now_iso())
        requests.append(request)
        seen.add(request_id)
    json_data["discordLinkRequests"] = requests
    return requests

def find_discord_link_request(json_data, request_id):
    for request in discord_link_requests(json_data):
        if request.get("id") == request_id:
            return request
    return None

def remove_discord_link_request(json_data, request_id):
    requests = discord_link_requests(json_data)
    before = len(requests)
    json_data["discordLinkRequests"] = [request for request in requests if request.get("id") != request_id]
    return len(json_data["discordLinkRequests"]) != before

def request_discord_link(json_data, user, summoner_full_name):
    ensure_admin_state(json_data)
    rebuild_discord_links_from_summoners(json_data)
    summoners = json_data.get("summoners") or {}
    if summoner_full_name not in summoners:
        return False, t(json_data, "leaderboard.not_added", summoner=summoner_full_name)

    user_id = str(user.id)
    current_user_id = summoners[summoner_full_name].get("discordUserId")
    if current_user_id and str(current_user_id) == user_id:
        return False, t(json_data, "linked_accounts.already_linked_self", summoner=summoner_full_name)
    if current_user_id:
        return False, t(json_data, "linked_accounts.already_linked_user", summoner=summoner_full_name, user_id=current_user_id)

    requests = discord_link_requests(json_data)
    request_id = discord_link_request_id(user_id, summoner_full_name)
    for request in requests:
        if request.get("summonerFullName", "").lower() != summoner_full_name.lower():
            continue
        if request.get("discordUserId") == user_id:
            request["discordDisplayName"] = discord_display_name(user)
            request["requestedAt"] = utc_now_iso()
            return True, t(json_data, "linked_accounts.request_refreshed", summoner=summoner_full_name)
        return False, t(json_data, "linked_accounts.request_pending_conflict", summoner=summoner_full_name)

    requests.append({
        "id": request_id,
        "discordUserId": user_id,
        "discordDisplayName": discord_display_name(user),
        "summonerFullName": summoner_full_name,
        "requestedAt": utc_now_iso()
    })
    json_data["discordLinkRequests"] = requests
    return True, t(json_data, "linked_accounts.request_sent", summoner=summoner_full_name)

def approve_discord_link_request(json_data, request_id, user):
    request = find_discord_link_request(json_data, request_id)
    if not request:
        return False, t(json_data, "linked_accounts.request_no_longer_pending")

    primary = not bool(linked_summoners_for_user(json_data, user.id))
    success, message = link_summoner_to_discord(json_data, user, request["summonerFullName"], primary=primary)
    if success:
        remove_discord_link_request(json_data, request_id)
    return success, message

def primary_summoner_queue_data(json_data, user_id):
    summoner_name = primary_summoner_for_user(json_data, user_id)
    if not summoner_name:
        return {}

    summoner_data = (json_data.get("summoners") or {}).get(summoner_name, {})
    return {
        "summonerFullName": summoner_name,
        "puuid": summoner_data.get("puuid"),
        "platform": summoner_data.get("platform"),
        "region": summoner_data.get("region"),
        "score": summoner_data.get("score", 0),
        "tier": summoner_data.get("tier"),
        "rank": summoner_data.get("rank"),
        "leaguePoints": summoner_data.get("leaguePoints")
    }

def format_linked_accounts_summary(json_data):
    rebuild_discord_links_from_summoners(json_data)
    links = json_data.get("discordLinks", {})
    if not links:
        return t(json_data, "linked_accounts.no_links")

    lines = []
    for user_id, link in [item for item in links.items()][:10]:
        primary = link.get("primarySummoner") or "-"
        count = len(link.get("summoners", []))
        lines.append(f"<@{user_id}> - {count} account(s), primary: **{primary}**")
    if len(links) > 10:
        lines.append(t(json_data, "linked_accounts.more", count=len(links) - 10))
    return "\n".join(lines)

def format_discord_link_requests_summary(json_data):
    requests = discord_link_requests(json_data)
    if not requests:
        return t(json_data, "linked_accounts.no_requests")

    lines = []
    for request in requests[:10]:
        user_id = request.get("discordUserId")
        summoner = request.get("summonerFullName")
        display_name = request.get("discordDisplayName") or user_id
        lines.append(f"<@{user_id}> ({display_name}) - **{summoner}**")
    if len(requests) > 10:
        lines.append(t(json_data, "linked_accounts.more", count=len(requests) - 10))
    return "\n".join(lines)

class LinkAccountModal(disnake.ui.Modal):
    def __init__(self, json_data=None):
        json_data = ensure_admin_state(json_data or load_json_data())
        components = [
            disnake.ui.TextInput(label=t(json_data, "linked_accounts.discord_user_label"), custom_id="discord_user", required=True, max_length=32),
            disnake.ui.TextInput(label=t(json_data, "linked_accounts.summoner_name_label"), custom_id="name", required=True, max_length=32),
            disnake.ui.TextInput(label=t(json_data, "linked_accounts.tagline_label"), custom_id="tagline", required=True, max_length=16),
            disnake.ui.TextInput(label=t(json_data, "linked_accounts.primary_label"), custom_id="primary", required=False, max_length=8, placeholder="yes"),
        ]
        super().__init__(title=t(json_data, "linked_accounts.link_modal_title"), custom_id="admin:link_account_modal", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        if not await require_admin_interaction(inter):
            return

        await inter.response.defer(ephemeral=True)
        member = await discord_user_from_text(inter.guild, inter.text_values["discord_user"])
        if not member:
            message = t(ensure_admin_state(load_json_data()), "linked_accounts.invalid_discord_user")
            log_event("discord_link_created", actor=interaction_actor(inter), status="error", summary=message)
            await send_ephemeral_followup(inter, message)
            return

        json_data = ensure_admin_state(load_json_data())
        summoner = find_summoner_key(json_data, inter.text_values["name"].strip(), inter.text_values["tagline"].strip())
        if not summoner:
            message = f"{inter.text_values['name']}#{normalize_tagline(inter.text_values['tagline'])} has not been added"
            log_event("discord_link_created", actor=interaction_actor(inter), status="error", summary=message, details={"discordUserId": str(member.id)})
            await send_ephemeral_followup(inter, message)
            return

        primary_value = inter.text_values.get("primary", "").strip().lower()
        primary = primary_value not in ["no", "false", "0", "n"]
        success, message = link_summoner_to_discord(json_data, member, summoner, primary)
        writeToJsonFile(jsonFile, json_data)
        log_event("discord_link_created", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"discordUserId": str(member.id), "summoner": summoner, "primary": primary})
        await refresh_configured_admin_message(json_data)
        await send_ephemeral_followup(inter, message)

class UnlinkAccountModal(disnake.ui.Modal):
    def __init__(self, json_data=None):
        json_data = ensure_admin_state(json_data or load_json_data())
        components = [
            disnake.ui.TextInput(label=t(json_data, "linked_accounts.summoner_name_label"), custom_id="name", required=True, max_length=32),
            disnake.ui.TextInput(label=t(json_data, "linked_accounts.tagline_label"), custom_id="tagline", required=True, max_length=16),
        ]
        super().__init__(title=t(json_data, "linked_accounts.unlink_modal_title"), custom_id="admin:unlink_account_modal", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        summoner = find_summoner_key(json_data, inter.text_values["name"].strip(), inter.text_values["tagline"].strip())
        if not summoner:
            message = f"{inter.text_values['name']}#{normalize_tagline(inter.text_values['tagline'])} has not been added"
            log_event("discord_link_removed", actor=interaction_actor(inter), status="error", summary=message)
            await send_ephemeral_response(inter, message)
            return

        success, message = unlink_summoner_from_discord(json_data, summoner)
        writeToJsonFile(jsonFile, json_data)
        log_event("discord_link_removed", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"summoner": summoner})
        await refresh_configured_admin_message(json_data)
        await send_ephemeral_response(inter, message)

class SetPrimaryAccountModal(disnake.ui.Modal):
    def __init__(self, json_data=None):
        json_data = ensure_admin_state(json_data or load_json_data())
        components = [
            disnake.ui.TextInput(label=t(json_data, "linked_accounts.discord_user_label"), custom_id="discord_user", required=True, max_length=32),
            disnake.ui.TextInput(label=t(json_data, "linked_accounts.summoner_name_label"), custom_id="name", required=True, max_length=32),
            disnake.ui.TextInput(label=t(json_data, "linked_accounts.tagline_label"), custom_id="tagline", required=True, max_length=16),
        ]
        super().__init__(title=t(json_data, "linked_accounts.primary_modal_title"), custom_id="admin:primary_account_modal", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        if not await require_admin_interaction(inter):
            return

        await inter.response.defer(ephemeral=True)
        member = await discord_user_from_text(inter.guild, inter.text_values["discord_user"])
        if not member:
            message = t(ensure_admin_state(load_json_data()), "linked_accounts.invalid_discord_user")
            log_event("discord_link_primary_changed", actor=interaction_actor(inter), status="error", summary=message)
            await send_ephemeral_followup(inter, message)
            return

        json_data = ensure_admin_state(load_json_data())
        summoner = find_summoner_key(json_data, inter.text_values["name"].strip(), inter.text_values["tagline"].strip())
        if not summoner:
            message = f"{inter.text_values['name']}#{normalize_tagline(inter.text_values['tagline'])} has not been added"
            log_event("discord_link_primary_changed", actor=interaction_actor(inter), status="error", summary=message, details={"discordUserId": str(member.id)})
            await send_ephemeral_followup(inter, message)
            return

        success, message = set_primary_summoner_for_user(json_data, member, summoner)
        writeToJsonFile(jsonFile, json_data)
        log_event("discord_link_primary_changed", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"discordUserId": str(member.id), "summoner": summoner})
        await refresh_configured_admin_message(json_data)
        await send_ephemeral_followup(inter, message)

def link_request_select_options(requests, json_data):
    options = []
    for request in requests[:MAX_SELECT_OPTIONS]:
        user_id = request.get("discordUserId")
        summoner = request.get("summonerFullName") or "Unknown summoner"
        display_name = request.get("discordDisplayName") or user_id
        label = f"{display_name} - {summoner}"[:100]
        options.append(disnake.SelectOption(label=label, value=request["id"], description=f"Discord ID: {user_id}"[:100]))
    return options

class ApproveLinkRequestSelect(disnake.ui.Select):
    def __init__(self, requests, json_data):
        super().__init__(
            placeholder=t(json_data, "linked_accounts.accept_placeholder"),
            min_values=1,
            max_values=1,
            options=link_request_select_options(requests, json_data),
            custom_id="admin:links:request_accept",
            row=1
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        request_id = self.values[0]
        request = find_discord_link_request(json_data, request_id)
        if not request:
            await inter.response.edit_message(embed=linked_accounts_admin_embed(json_data), view=LinkedAccountsAdminView(json_data))
            await send_ephemeral_followup(inter, t(json_data, "linked_accounts.request_no_longer_pending"))
            return

        member = await get_guild_member(inter.guild, request["discordUserId"])
        if not member:
            await inter.response.edit_message(embed=linked_accounts_admin_embed(json_data), view=LinkedAccountsAdminView(json_data))
            await send_ephemeral_followup(inter, t(json_data, "linked_accounts.request_user_missing"))
            return

        success, message = approve_discord_link_request(json_data, request_id, member)
        if success:
            writeToJsonFile(jsonFile, json_data)
            await refresh_configured_admin_message(json_data)
        log_event("discord_link_request_accepted", actor=interaction_actor(inter), status="success" if success else "error", summary=message, details={"requestId": request_id, "discordUserId": request["discordUserId"], "summoner": request["summonerFullName"]})
        await inter.response.edit_message(embed=linked_accounts_admin_embed(json_data), view=LinkedAccountsAdminView(json_data))
        await send_ephemeral_followup(inter, message)

class RejectLinkRequestSelect(disnake.ui.Select):
    def __init__(self, requests, json_data):
        super().__init__(
            placeholder=t(json_data, "linked_accounts.reject_placeholder"),
            min_values=1,
            max_values=1,
            options=link_request_select_options(requests, json_data),
            custom_id="admin:links:request_reject",
            row=2
        )

    async def callback(self, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return

        json_data = ensure_admin_state(load_json_data())
        request_id = self.values[0]
        request = find_discord_link_request(json_data, request_id)
        removed = remove_discord_link_request(json_data, request_id)
        if removed:
            writeToJsonFile(jsonFile, json_data)
            await refresh_configured_admin_message(json_data)
        summoner = request.get("summonerFullName") if request else None
        message = t(json_data, "linked_accounts.request_rejected", summoner=summoner) if removed else t(json_data, "linked_accounts.request_no_longer_pending")
        log_event("discord_link_request_rejected", actor=interaction_actor(inter), status="success" if removed else "error", summary=message, details={"requestId": request_id, "summoner": summoner})
        await inter.response.edit_message(embed=linked_accounts_admin_embed(json_data), view=LinkedAccountsAdminView(json_data))
        await send_ephemeral_followup(inter, message)

class LinkedAccountsAdminView(disnake.ui.View):
    def __init__(self, json_data=None):
        super().__init__(timeout=300)
        if json_data is None:
            json_data = ensure_admin_state(load_json_data())
        requests = discord_link_requests(json_data)
        if requests:
            self.add_item(ApproveLinkRequestSelect(requests, json_data))
            self.add_item(RejectLinkRequestSelect(requests, json_data))
        for child in self.children:
            if getattr(child, "custom_id", None) == "admin:links:link":
                child.label = t(json_data, "linked_accounts.link_button")
            elif getattr(child, "custom_id", None) == "admin:links:unlink":
                child.label = t(json_data, "linked_accounts.unlink_button")
            elif getattr(child, "custom_id", None) == "admin:links:primary":
                child.label = t(json_data, "linked_accounts.primary_button")

    @disnake.ui.button(label="Link account", style=disnake.ButtonStyle.green, custom_id="admin:links:link")
    async def link_account(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        await inter.response.send_modal(LinkAccountModal(ensure_admin_state(load_json_data())))

    @disnake.ui.button(label="Unlink account", style=disnake.ButtonStyle.red, custom_id="admin:links:unlink")
    async def unlink_account(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        await inter.response.send_modal(UnlinkAccountModal(ensure_admin_state(load_json_data())))

    @disnake.ui.button(label="Set primary", style=disnake.ButtonStyle.blurple, custom_id="admin:links:primary")
    async def set_primary(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if not await require_admin_interaction(inter):
            return
        await inter.response.send_modal(SetPrimaryAccountModal(ensure_admin_state(load_json_data())))

def linked_accounts_admin_embed(json_data):
    rebuild_discord_links_from_summoners(json_data)
    links = json_data.get("discordLinks", {})
    requests = discord_link_requests(json_data)
    linked_summoners = [
        summoner for summoner, data in (json_data.get("summoners") or {}).items()
        if data.get("discordUserId")
    ]

    embed = disnake.Embed(
        title=t(json_data, "linked_accounts.title"),
        description=t(json_data, "linked_accounts.description", users=len(links), summoners=len(linked_summoners), requests=len(requests)),
        colour=disnake.Colour.green()
    )
    embed.add_field(name=t(json_data, "linked_accounts.current_links"), value=format_linked_accounts_summary(json_data), inline=False)
    embed.add_field(name=t(json_data, "linked_accounts.pending_requests"), value=format_discord_link_requests_summary(json_data), inline=False)
    return embed
