"""Persistent challenge entry points and owner-bound private menus."""

import random
import logging
import json

import disnake

from challenge_store import read_challenges, edit_challenges
from i18n import t
from menu_session import MenuSession, SessionView, MenuButton, MenuSelect, page_slice, add_pager
from soloq_challenge import (
    DAY, WEEK, RuleError, TASKS, new_edition, phase, timestamp, register, points, pending,
    streaks, launch, assign, resolve_shell, moderate_player, audit,
)
from state import can_configure_channels, load_json_data


_display_names = {}


async def refresh_display_names(edition, guild):
    from discord_helpers import get_guild_member
    for uid, player in edition["players"].items():
        member = await get_guild_member(guild, uid)
        _display_names[(guild.id, uid)] = member.display_name if member else player.get("display_name", uid)


def tr(edition, key, **kwargs):
    return t({"botLanguage": edition.get("language", "en")}, f"challenge.{key}", **kwargs)


def display(guild, user_id):
    member = guild.get_member(int(user_id))
    name = member.display_name if member else _display_names.get((guild.id, str(user_id)), str(user_id))
    return disnake.utils.escape_markdown(name)


def registration_text(edition, guild):
    lines = [tr(edition, "schedule", start=f"<t:{int(edition['starts_at'])}:F>", end=f"<t:{int(edition['ends_at'])}:F>"), tr(edition, "rules")]
    for uid, player in edition["players"].items():
        lines.append(f"{display(guild, uid)} · {player['name']} · {tr(edition, 'approved' if player.get('approved', True) else 'review')}")
    return "\n".join(lines)


def leaderboard_text(edition, guild):
    now = timestamp()
    stage = phase(edition, now)
    heading = tr(edition, stage)
    if stage in ("week1", "week2"):
        end = edition["starts_at"] + WEEK if stage == "week1" else edition["ends_at"]
        seconds = max(0, end - now)
        if seconds < 3600:
            remaining = tr(edition, "week_remaining_under_hour")
        else:
            remaining = tr(edition, "week_remaining", days=int(seconds // DAY), hours=int(seconds % DAY // 3600))
        heading += "\n" + remaining
    rows = []
    ranked = sorted(((uid, p) for uid, p in edition["players"].items() if p.get("approved", True)), key=lambda pair: (-points(pair[1]), pair[0]))
    for rank, (uid, player) in enumerate(ranked, 1):
        inventory = sum(s["owner"] == uid and s["state"] == "inventory" for s in edition["shells"])
        tasks = pending(edition, uid)
        unrevealed = sum(s["state"] == "unrevealed" for s in tasks)
        counts = [sum(r["week"] == w for r in player["records"]) for w in (0, 1)]
        rows.append(f"**#{rank} {display(guild, uid)} · {points(player)} pts**\n{counts[0]}/15 + {counts[1]}/15 · {len(player['records'])}/30 · {tr(edition, 'streak')}: {streaks(player)[0]:+d}\n{tr(edition, 'inventory')}: {inventory} · {tr(edition, 'unrevealed')}: {unrevealed} · {tr(edition, 'pending')}: {len(tasks) - unrevealed}")
    return heading + "\n\n" + ("\n\n".join(rows) or tr(edition, "empty"))


def shell_description(edition, shell, guild):
    sender = display(guild, shell["owner"])
    target = display(guild, shell["target"]) if shell.get("target") else "-"
    task = tr(edition, shell["task"]) if shell.get("task") else "?"
    value = shell.get("value")
    if isinstance(value, list):
        value = ", ".join(value[:15]) + (f" (+{len(value) - 15})" if len(value) > 15 else "")
    return f"`{shell['id'][:8]}` {sender} → {target} · **{tr(edition, shell['state'])}**\n{task}{': ' + str(value) if value else ''}"


def shell_board_text(edition, guild):
    rows = [shell_description(edition, shell, guild) for shell in edition["shells"] if shell["state"] != "inventory"]
    return "\n\n".join(rows[-15:]) or tr(edition, "empty")


def match_text(edition, guild, uid, record):
    shell = next((s for s in edition["shells"] if s["id"] == record.get("task")), None)
    evidence = tr(edition, "duo_unknown")
    if record.get("same_team_participants"):
        evidence += "\n" + tr(edition, "coincidence") + ": " + ", ".join(display(guild, p) for p in record["same_team_participants"])
    score = sum(record["points"].values()) + record.get("adjustment", 0)
    parts = ", ".join(f"{tr(edition, key)} {value:+d}" for key, value in record["points"].items())
    return f"**{display(guild, uid)} · {record['champion']} · {tr(edition, 'win' if record['win'] else 'loss')}**\n{record['kills']}/{record['deaths']}/{record['assists']} · **{score:+d} pts** ({parts})\n{tr(edition, 'week')} {record['week'] + 1} · `{record['id']}`\n{tr(edition, 'shell_awarded' if record['shell_awarded'] else 'no_reward')} · {', '.join(tr(edition, r) for r in record['shell_reasons'])}\n{shell_description(edition, shell, guild) if shell else ''}\n{evidence}"


class OpenChallengeButton(disnake.ui.Button):
    def __init__(self, kind):
        super().__init__(label="SoloQ Challenge", style=disnake.ButtonStyle.primary, custom_id=f"soloq-challenge:{kind}")

    async def callback(self, inter):
        await open_challenge(inter)


class ChallengePublicView(disnake.ui.View):
    def __init__(self, kind):
        super().__init__(timeout=None)
        self.add_item(OpenChallengeButton(kind))


class ChallengeMenu:
    def __init__(self, inter):
        self.session = MenuSession(inter.author.id)
        self.guild_id = inter.guild.id
        self.guild = inter.guild
        self.section = "overview"
        self.selected = None
        self.page = 0
        self.edition_id = None
        self.history_user = str(inter.author.id)

    def edition(self):
        document = read_challenges(self.guild_id)
        edition = document["editions"][-1] if document["editions"] else None
        if self.edition_id and (not edition or edition["id"] != self.edition_id):
            raise RuleError("stale")
        if edition:
            self.edition_id = edition["id"]
        return edition

    def change(self, inter, action, admin=False):
        if admin and not can_configure_channels(inter):
            raise RuleError("forbidden")
        def apply(document):
            if not document["editions"] or document["editions"][-1]["id"] != self.edition_id:
                raise RuleError("stale")
            return action(document["editions"][-1])
        return edit_challenges(self.guild_id, apply)

    async def navigate(self, inter, section):
        await self.session.acknowledge(inter)
        self.section, self.selected, self.page = section, None, 0
        await self.render(inter)

    async def select(self, inter, selected):
        await self.session.acknowledge(inter)
        self.selected = selected
        await self.render(inter)

    async def paginate(self, inter, page):
        self.page = page
        await self.render(inter)

    async def run(self, inter, operation, admin=False):
        await self.session.acknowledge(inter)
        edition = self.edition() or {"language": load_json_data().get("botLanguage", "en")}
        try:
            if admin and not can_configure_channels(inter):
                raise RuleError("forbidden")
            await operation()
            await self.render(inter, tr(edition, "saved"))
        except RuleError as error:
            await self.render(inter, tr(edition, str(error)))
        except Exception:
            logging.getLogger(__name__).exception("Challenge action failed")
            await self.render(inter, tr(edition, "busy"))

    def button(self, view, edition, key, handler, danger=False):
        view.add_item(MenuButton(tr(edition, key), handler, style=disnake.ButtonStyle.danger if danger else disnake.ButtonStyle.secondary))

    def listing(self, view, edition, entries, callback=None, row=1):
        entries, self.page, pages = page_slice(entries, self.page)
        if self.selected not in {entry.value for entry in entries}:
            self.selected = None
        if entries:
            for entry in entries:
                entry.default = entry.value == self.selected
            view.add_item(MenuSelect(tr(edition, "select"), entries, callback or self.select, row=row))
        if pages > 1:
            add_pager(view, self.page, pages, self.paginate, {"botLanguage": edition["language"]})

    async def render(self, inter, notice=None):
        await self.session.acknowledge(inter)
        edition = self.edition()
        language = edition["language"] if edition else load_json_data().get("botLanguage", "en")
        context = edition or {"language": language}
        if edition:
            await refresh_display_names(edition, inter.guild)
        if self.section == "admin" and not can_configure_channels(inter):
            self.section = "overview"
            notice = tr(context, "forbidden")
        view = SessionView(self.session, {"botLanguage": language})
        # Guard permission changes and domain errors even for callbacks created on an earlier page.
        original_check = view.interaction_check
        async def check(interaction):
            if not await original_check(interaction):
                return False
            if self.section == "admin" and not can_configure_channels(interaction):
                await interaction.response.send_message(tr(context, "forbidden"), ephemeral=True)
                return False
            return True
        view.interaction_check = check
        embed = disnake.Embed(title="SoloQ Challenge", colour=disnake.Colour.gold())
        if not edition:
            embed.description = tr(context, "no_edition")
            if can_configure_channels(inter):
                self.button(view, context, "create", self.create)
            await self.session.show(inter, embed, view, notice)
            return
        sections = ["overview", "registration", "ranking", "shells", "incoming", "completed", "all_shells", "history"]
        if can_configure_channels(inter):
            sections.append("admin")
        active_section = "history" if self.section == "history_detail" else self.section
        view.add_item(MenuSelect(tr(edition, "tabs"), [disnake.SelectOption(label=t({"botLanguage": language}, "menus.tab_label", name=tr(edition, s)), value=s, default=s == active_section) for s in sections], self.navigate, row=0))
        embed.set_author(name=tr(edition, self.section))
        stage = phase(edition, timestamp())
        if self.section == "overview":
            embed.description = registration_text(edition, inter.guild)
        elif self.section == "registration":
            embed.description = tr(edition, "schedule", start=f"<t:{int(edition['starts_at'])}:F>", end=f"<t:{int(edition['ends_at'])}:F>") + "\n" + tr(edition, "rules")
            entries = [disnake.SelectOption(label=display(inter.guild, uid)[:100], value=uid, description=(p["name"] + " · " + tr(edition, "approved" if p.get("approved", True) else "review"))[:100]) for uid, p in edition["players"].items()]
            self.listing(view, edition, entries)
            if stage == "registration":
                from utils.commonUtils import platforms
                view.add_item(MenuSelect(tr(edition, "platform"), [disnake.SelectOption(label=p, value=p) for p in platforms], self.signup_modal, row=2))
                self.button(view, edition, "withdraw", self.withdraw, danger=True)
        elif self.section == "ranking":
            rows = leaderboard_text(edition, inter.guild).split("\n\n")
            entries, self.page, pages = page_slice(rows[1:], self.page, 8)
            embed.description = rows[0] + "\n\n" + "\n\n".join(entries)
            if pages > 1:
                add_pager(view, self.page, pages, self.paginate, {"botLanguage": language})
        elif self.section == "shells":
            own = str(inter.author.id)
            available = [s for s in edition["shells"] if s["owner"] == own and s["state"] == "inventory"]
            embed.description = tr(edition, "shell_info", count=len(available)) + "\n\n" + shell_board_text(edition, inter.guild)
            targets = [disnake.SelectOption(label=display(inter.guild, uid)[:100], value=uid) for uid, p in edition["players"].items() if uid != own and p.get("approved", True)]
            self.listing(view, edition, targets)
            if available and self.selected and stage in ("week1", "week2"):
                self.button(view, edition, "launch", self.throw)
            unrevealed = next((s for s in pending(edition, own) if s["state"] == "unrevealed"), None)
            if unrevealed and stage in ("week1", "week2"):
                self.button(view, edition, "reveal", self.reveal)
        elif self.section in ("incoming", "completed", "all_shells"):
            own = str(inter.author.id)
            shells = [s for s in edition["shells"] if s["state"] != "inventory"]
            if self.section == "incoming":
                shells = [s for s in shells if s.get("target") == own and s["state"] not in ("completed", "waived")]
            elif self.section == "completed":
                shells = [s for s in shells if s["state"] in ("completed", "waived") and (s.get("target") == own or s["owner"] == own)]
            entries = [disnake.SelectOption(label=f"{s['id'][:8]} · {tr(edition, s['state'])}"[:100], value=s["id"]) for s in reversed(shells)]
            self.listing(view, edition, entries)
            chosen = next((s for s in shells if s["id"] == self.selected), None)
            embed.description = shell_description(edition, chosen, inter.guild) if chosen else tr(edition, "empty")
            if chosen and chosen.get("resolution"):
                embed.description += "\n" + chosen["resolution"]
            if chosen and chosen.get("target") == own and chosen["state"] == "unrevealed" and stage in ("week1", "week2"):
                self.button(view, edition, "reveal", self.reveal)
        elif self.section == "history":
            users = [disnake.SelectOption(label=display(inter.guild, uid)[:100], value=uid) for uid in edition["players"]]
            async def choose(interaction, uid):
                self.history_user, self.page, self.section = uid, 0, "history_detail"
                await self.render(interaction)
            self.listing(view, edition, users, choose)
            embed.description = tr(edition, "select")
        elif self.section == "history_detail":
            records = edition["players"].get(self.history_user, {}).get("records", [])
            records, record_page, pages = page_slice(list(reversed(records)), self.page, 3)
            embed.description = "\n\n".join(match_text(edition, inter.guild, self.history_user, r) for r in records) or tr(edition, "empty")
            if pages > 1:
                add_pager(view, record_page, pages, self.paginate, {"botLanguage": language})
            self.button(view, edition, "history", lambda i: self.navigate(i, "history"))
        elif self.section == "admin":
            embed.description = tr(edition, "admin_info") + "\n" + json_config(edition)
            if stage == "registration":
                self.button(view, edition, "configure", self.config_modal)
                candidates = [disnake.SelectOption(label=display(inter.guild, uid)[:100], value="approve:" + uid) for uid, p in edition["players"].items() if not p.get("approved", True)]
            else:
                candidates = []
            candidates += [disnake.SelectOption(label=display(inter.guild, uid)[:100], value="player:" + uid) for uid in edition["players"]]
            candidates += [disnake.SelectOption(label=f"{s['id'][:8]} {display(inter.guild, s['target'])}"[:100], value=s["id"]) for s in edition["shells"] if s["state"] in ("review", "failed", "assigned", "unrevealed")]
            self.listing(view, edition, candidates)
            if self.selected:
                if self.selected.startswith("approve:"):
                    self.button(view, edition, "approve", self.approve)
                elif self.selected.startswith("player:"):
                    self.button(view, edition, "moderate", self.resolve_modal)
                else:
                    shell = next(s for s in edition["shells"] if s["id"] == self.selected)
                    embed.description += "\n\n" + shell_description(edition, shell, inter.guild)
                    record = next((r for r in edition["players"].get(shell.get("target"), {}).get("records", []) if r["id"] == shell.get("match_id")), None)
                    if record:
                        embed.description += f"\n`{record['id']}`\n```json\n{json.dumps(record.get('task_evidence', {}), ensure_ascii=True)[:1000]}\n```"
                    self.button(view, edition, "moderate", self.resolve_modal)
            if stage in ("finished", "cancelled"):
                self.button(view, edition, "create", self.create)
        embed.set_footer(text=tr(edition, stage) + " · " + t({"botLanguage": language}, "menus.session_info"))
        if embed.description:
            embed.description = embed.description[:4096]
        await self.session.show(inter, embed, view, notice)

    async def create(self, inter):
        async def operation():
            def apply(document):
                if document["editions"] and phase(document["editions"][-1], timestamp()) not in ("finished", "cancelled"):
                    raise RuleError("edition_active")
                document["editions"].append(new_edition(timestamp(), load_json_data().get("botLanguage", "en")))
                return document["editions"][-1]["id"]
            self.edition_id = edit_challenges(inter.guild.id, apply)
            self.section = "admin"
            from challenge_service import ensure_channels
            await ensure_channels(inter.guild, self.edition())
        await self.run(inter, operation, admin=True)

    async def signup_modal(self, inter, platform):
        await inter.response.send_modal(SignupModal(self, platform))

    async def withdraw(self, inter):
        async def operation():
            def change(edition):
                if phase(edition, timestamp()) != "registration":
                    raise RuleError("registration_closed")
                edition["players"].pop(str(inter.author.id), None)
                audit(edition, "withdraw", inter.author.id, timestamp())
            self.change(inter, change)
        await self.run(inter, operation)

    async def throw(self, inter):
        async def operation():
            self.change(inter, lambda e: launch(e, inter.author.id, self.selected, timestamp()))
        await self.run(inter, operation)

    async def reveal(self, inter):
        async def operation():
            def change(edition):
                shell = next((s for s in pending(edition, inter.author.id) if s["state"] == "unrevealed"), None)
                if not shell:
                    raise RuleError("stale")
                assign(edition, shell["id"], random.SystemRandom().choice, timestamp())
            self.change(inter, change)
        await self.run(inter, operation)

    async def approve(self, inter):
        async def operation():
            def change(edition):
                if phase(edition, timestamp()) != "registration":
                    raise RuleError("registration_closed")
                uid = self.selected.split(":", 1)[1]
                edition["players"][uid]["approved"] = True
                audit(edition, "approve", inter.author.id, timestamp(), user=uid)
            self.change(inter, change, admin=True)
        await self.run(inter, operation, admin=True)

    async def config_modal(self, inter):
        if not can_configure_channels(inter):
            raise RuleError("forbidden")
        await inter.response.send_modal(ConfigModal(self))

    async def resolve_modal(self, inter):
        if not can_configure_channels(inter):
            raise RuleError("forbidden")
        await inter.response.send_modal(ResolveModal(self))


def json_config(edition):
    c = edition["config"]
    return tr(edition, "config_summary", win=c["win"], loss=c["loss"], cap=c["shell_cap"], penalty=c["failed_task_penalty"])


class SignupModal(disnake.ui.Modal):
    def __init__(self, menu, platform):
        self.menu, self.platform = menu, platform
        edition = menu.edition()
        super().__init__(title=tr(edition, "registration"), components=[
            disnake.ui.TextInput(label="Riot ID", custom_id="account", placeholder="Name#TAG", max_length=70),
            disnake.ui.TextInput(label=tr(edition, "champions"), custom_id="champions", style=disnake.TextInputStyle.paragraph, max_length=3500),
        ], timeout=max(1, menu.session.remaining()))

    async def callback(self, inter):
        if not await self.menu.session.check(inter, {}):
            return
        async def operation():
            from challenge_service import resolve_account, champion_catalog
            catalog = await champion_catalog()
            aliases = {value["name"].casefold(): key for key, value in catalog.items()}
            aliases.update({key.casefold(): key for key in catalog})
            try:
                champions = [aliases[name.strip().casefold()] for name in inter.text_values["champions"].split(",") if name.strip()]
                account = await resolve_account(inter.text_values["account"], self.platform)
            except (KeyError, ValueError):
                raise RuleError("invalid_account")
            state = load_json_data()
            account["display_name"] = inter.author.display_name
            account["approved"] = any(p.get("puuid") == account["puuid"] and str(p.get("discordUserId")) == str(inter.author.id) for p in state.get("summoners", {}).values())
            self.menu.change(inter, lambda e: register(e, inter.author.id, account, champions, timestamp()))
        await self.menu.run(inter, operation)


class ConfigModal(disnake.ui.Modal):
    def __init__(self, menu):
        self.menu = menu
        edition = menu.edition()
        config = edition["config"]
        super().__init__(title=tr(edition, "configure"), components=[
            disnake.ui.TextInput(label=tr(edition, "score_config"), custom_id="scores", value=",".join(str(config[k]) for k in ("win", "loss", "positive_kd", "negative_kda", "multi_kill", "mvp"))),
            disnake.ui.TextInput(label=tr(edition, "limits_config"), custom_id="limits", value=f"{config['shell_cap']},{config['failed_task_penalty']}"),
            disnake.ui.TextInput(label=tr(edition, "tasks_config"), custom_id="tasks", style=disnake.TextInputStyle.paragraph, value=",".join(config["tasks"]), max_length=500),
        ], timeout=max(1, menu.session.remaining()))

    async def callback(self, inter):
        if not await self.menu.session.check(inter, {}):
            return
        async def operation():
            try:
                scores = [int(v) for v in inter.text_values["scores"].split(",")]
                cap, penalty = [int(v) for v in inter.text_values["limits"].split(",")]
                tasks = list(dict.fromkeys(v.strip() for v in inter.text_values["tasks"].split(",")))
                if len(scores) != 6 or any(abs(v) > 100 for v in scores) or not 1 <= cap <= 10 or not -100 <= penalty <= 0 or not tasks or set(tasks) - set(TASKS):
                    raise ValueError()
            except ValueError:
                raise RuleError("invalid_config")
            def change(edition):
                if phase(edition, timestamp()) != "registration":
                    raise RuleError("registration_closed")
                edition["config"].update(dict(zip(("win", "loss", "positive_kd", "negative_kda", "multi_kill", "mvp"), scores)))
                edition["config"].update(shell_cap=cap, failed_task_penalty=penalty, tasks=tasks)
                audit(edition, "configure", inter.author.id, timestamp(), config=edition["config"].copy())
            self.menu.change(inter, change, admin=True)
        await self.menu.run(inter, operation, admin=True)


class ResolveModal(disnake.ui.Modal):
    def __init__(self, menu):
        self.menu, self.shell_id = menu, menu.selected
        edition = menu.edition()
        super().__init__(title=tr(edition, "moderate"), components=[
            disnake.ui.TextInput(label=tr(edition, "player_resolution" if self.shell_id.startswith("player:") else "resolution"), custom_id="result", placeholder="remove / points:-3" if self.shell_id.startswith("player:") else "completed / waived / assigned", max_length=20),
            disnake.ui.TextInput(label=tr(edition, "reason"), custom_id="reason", style=disnake.TextInputStyle.paragraph, max_length=500),
        ], timeout=max(1, menu.session.remaining()))

    async def callback(self, inter):
        if not await self.menu.session.check(inter, {}):
            return
        async def operation():
            if self.shell_id.startswith("player:"):
                self.menu.change(inter, lambda e: moderate_player(e, self.shell_id.split(":", 1)[1], inter.text_values["result"].strip(), inter.author.id, inter.text_values["reason"], timestamp()), admin=True)
            else:
                self.menu.change(inter, lambda e: resolve_shell(e, self.shell_id, inter.text_values["result"].strip(), inter.author.id, inter.text_values["reason"], timestamp()), admin=True)
        await self.menu.run(inter, operation, admin=True)


async def open_challenge(inter, section="overview"):
    from storage import database_enabled
    if not inter.response.is_done():
        await inter.response.defer(ephemeral=True)
    if not inter.guild or not database_enabled():
        await inter.followup.send("SoloQ Challenge requires a server and PostgreSQL.", ephemeral=True)
        return
    menu = ChallengeMenu(inter)
    menu.section = section
    await menu.render(inter)
