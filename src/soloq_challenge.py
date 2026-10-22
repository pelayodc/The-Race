"""Deterministic competition rules; no Discord or network calls."""

from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4


DAY = 86400
WEEK = 7 * DAY
TASKS = ("new_champion", "random_role", "no_boots", "random_champion", "no_starter", "sender_main", "no_flash", "no_last_three")
DEFAULTS = {
    "win": 10, "loss": 0, "positive_kd": 1, "negative_kda": -1, "multi_kill": 1, "mvp": 1,
    "weekly_limit": 15, "shell_cap": 3,
    "failed_task_penalty": 0, "tasks": list(TASKS),
    "starter_items": [1054, 1055, 1056, 1082, 3865, 1101, 1102, 1103],
}


class RuleError(ValueError):
    pass


def timestamp():
    return datetime.now(timezone.utc).timestamp()


def phase(edition, now):
    if edition.get("cancelled"):
        return "cancelled"
    if now < edition["starts_at"]:
        return "registration"
    if now < edition["starts_at"] + WEEK:
        return "week1"
    if now < edition["ends_at"]:
        return "week2"
    if now < edition["ends_at"] + DAY or any(p.get("approved", True) and p.get("scan_at", 0) < edition["ends_at"] for p in edition["players"].values()):
        return "settling"
    if any(s["state"] in ("review", "failed") and edition["players"].get(s.get("target"), {}).get("approved", True) for s in edition["shells"]):
        return "settling"
    return "finished"


def new_edition(now, language="en"):
    return {"id": uuid4().hex, "created_at": now, "starts_at": now + 3 * DAY,
            "ends_at": now + 3 * DAY + 2 * WEEK, "language": language,
            "config": {**DEFAULTS, "tasks": list(TASKS), "starter_items": list(DEFAULTS["starter_items"])},
            "players": {}, "shells": [], "channels": {}, "messages": {}, "audit": []}


def audit(edition, action, actor, now, **details):
    edition["audit"].append({"at": now, "action": action, "actor": str(actor), **details})


def register(edition, user_id, account, champions, now):
    if phase(edition, now) != "registration":
        raise RuleError("registration_closed")
    user_id = str(user_id)
    if not champions:
        raise RuleError("champions_required")
    claims = [uid for uid, p in edition["players"].items() if p["puuid"] == account["puuid"] and uid != user_id]
    if claims:
        if not account.get("approved", False) or any(edition["players"][uid].get("approved", True) for uid in claims):
            raise RuleError("account_used")
        for uid in claims:
            del edition["players"][uid]
            audit(edition, "unverified_claim_replaced", user_id, now, previous_user=uid, puuid=account["puuid"])
    edition["players"][user_id] = {**account, "champions": sorted(set(champions)), "records": [], "scan_at": 0}
    audit(edition, "register", user_id, now, puuid=account["puuid"])


def match_week(edition, start):
    if not edition["starts_at"] <= start < edition["ends_at"]:
        return None
    return int((start - edition["starts_at"]) // WEEK)


def score(participant, mvp_id, config):
    kills, deaths, assists = (participant.get(k, 0) for k in ("kills", "deaths", "assists"))
    parts = {"result": config["win"] if participant.get("win") else config["loss"]}
    if kills > deaths:
        parts["positive_kd"] = config["positive_kd"]
    if kills + assists < deaths:
        parts["negative_kda"] = config["negative_kda"]
    if participant.get("quadraKills", 0) or participant.get("pentaKills", 0):
        parts["multi_kill"] = config["multi_kill"]
    if mvp_id is not None and participant.get("participantId") == mvp_id:
        parts["mvp"] = config["mvp"]
    return parts


def streaks(player):
    visible, wins = 0, 0
    for record in player["records"]:
        won = record["win"]
        visible = max(0, visible) + 1 if won else min(0, visible) - 1
        wins = wins + 1 if won else 0
        # This reward counter resets every five wins, even when inventory is full.
        if wins == 5:
            wins = 0
    return visible, wins


def shell_reasons(participant, consecutive):
    reasons = []
    if consecutive == 5:
        reasons.append("streak5")
    if participant.get("quadraKills", 0) or participant.get("pentaKills", 0):
        reasons.append("multi_kill")
    if participant.get("kills", 0) >= 20:
        reasons.append("kills20")
    if participant.get("deaths") == 0:
        reasons.append("deathless")
    if participant.get("assists", 0) >= 30:
        reasons.append("assists30")
    return reasons


def points(player):
    return player.get("moderation_points", 0) + sum(sum(r["points"].values()) + r.get("adjustment", 0) for r in player["records"])


def pending(edition, target):
    return [s for s in edition["shells"] if s.get("target") == str(target) and s["state"] in ("unrevealed", "assigned", "review", "failed")]


def launch(edition, sender, target, now):
    sender, target = str(sender), str(target)
    if phase(edition, now) not in ("week1", "week2") or sender == target:
        raise RuleError("invalid_target")
    if sender not in edition["players"] or target not in edition["players"] or not edition["players"][sender].get("approved", True) or not edition["players"][target].get("approved", True):
        raise RuleError("invalid_target")
    week = int((now - edition["starts_at"]) // WEEK)
    if sum(r["week"] == week for r in edition["players"][target]["records"]) >= edition["config"]["weekly_limit"]:
        raise RuleError("quota_full")
    shell = next((s for s in edition["shells"] if s["owner"] == sender and s["state"] == "inventory"), None)
    if not shell:
        raise RuleError("no_shells")
    shell.update(target=target, launched_at=now, state="unrevealed")
    audit(edition, "launch", sender, now, shell=shell["id"], target=target)
    return shell


def assign(edition, shell_id, choose, now):
    shell = next((s for s in edition["shells"] if s["id"] == shell_id), None)
    if shell is None:
        raise RuleError("stale")
    if shell["state"] != "unrevealed":
        return shell
    target = edition["players"][shell["target"]]
    sender = edition["players"][shell["owner"]]
    records = target["records"]
    pool = set(target["champions"])
    candidates = []
    for task in edition["config"]["tasks"]:
        value = None
        if task == "new_champion":
            value = sorted(pool - {r["champion"] for r in records})
            if not value:
                continue
        elif task == "no_last_three":
            value = sorted(pool - {r["champion"] for r in records[-3:]})
            if not value:
                continue
        elif task == "random_champion":
            value = choose(sorted(pool))
        elif task == "random_role":
            value = choose(["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"])
        elif task == "sender_main":
            counts = Counter(r["champion"] for r in sender["records"])
            if not counts:
                continue
            value = sorted(counts, key=lambda c: (-counts[c], c))[0]
            if value not in pool:
                continue
        candidates.append((task, value))
    if not candidates:
        raise RuleError("no_tasks")
    task, value = choose(candidates)
    shell.update(task=task, value=value, assigned_at=now, state="assigned")
    audit(edition, "assign", shell["target"], now, shell=shell["id"], task=task, value=value)
    return shell


def validate_task(shell, participant, timeline=None, boots=None, starter_items=()):
    task, value = shell["task"], shell.get("value")
    champion = participant.get("championName")
    if task in ("new_champion", "no_last_three"):
        return "completed" if champion in value else "failed"
    if task in ("random_champion", "sender_main"):
        return "completed" if champion == value else "failed"
    if task == "no_flash":
        spells = [participant.get("summoner1Id"), participant.get("summoner2Id")]
        if None in spells:
            return "review"
        return "failed" if 4 in spells else "completed"
    if task == "random_role":
        if participant.get("teamPosition") != value:
            return "review"  # Inference cannot distinguish autofill from ignoring the challenge.
        return "completed"
    if task in ("no_boots", "no_starter"):
        if not timeline or (task == "no_boots" and boots is None):
            return "review"
        forbidden = set(boots or ()) if task == "no_boots" else set(starter_items)
        events = [event for frame in timeline.get("info", {}).get("frames", []) for event in frame.get("events", [])]
        if not any(e.get("type") == "GAME_END" for e in events):
            return "review"
        purchases = [e for e in events if e.get("participantId") == participant["participantId"] and e.get("type") == "ITEM_PURCHASED"]
        # A purchase counts even if sold or undone; no inventory inference required.
        return "failed" if any(e.get("itemId") in forbidden for e in purchases) else "completed"
    return "review"


def record_match(edition, user_id, match, mvp_id, now, timeline=None, boots=None):
    player = edition["players"][str(user_id)]
    match_id = match["metadata"]["matchId"]
    if any(r["id"] == match_id for r in player["records"]):
        return None
    info = match["info"]
    start = info["gameStartTimestamp"] / 1000
    week = match_week(edition, start)
    if week is None or info.get("queueId") != 420:
        return None
    participant = next((p for p in info["participants"] if p.get("puuid") == player["puuid"]), None)
    if not participant:
        raise RuleError("missing_participant")
    if any(not isinstance(participant.get(key), int) or participant[key] < 0 for key in ("kills", "deaths", "assists")) or not isinstance(participant.get("win"), bool) or not participant.get("championName"):
        raise RuleError("incomplete_match")
    if info.get("gameDuration", 0) < 300 and any(p.get("gameEndedInEarlySurrender") for p in info["participants"]):
        return None
    if sum(r["week"] == week for r in player["records"]) >= edition["config"]["weekly_limit"]:
        return None
    if player["records"] and start < player["records"][-1]["start"]:
        raise RuleError("out_of_order")
    _, win_counter = streaks(player)
    reasons = shell_reasons(participant, win_counter + 1 if participant.get("win") else 0)
    record = {"id": match_id, "week": week, "start": start, "win": bool(participant.get("win")),
              "champion": participant["championName"], "kills": participant.get("kills", 0), "deaths": participant.get("deaths", 0),
              "assists": participant.get("assists", 0), "points": score(participant, mvp_id, edition["config"]),
              "shell_reasons": reasons, "shell_awarded": False, "task": None, "duo": "unverifiable"}
    tasks = sorted([s for s in pending(edition, user_id) if s.get("launched_at", now) <= start], key=lambda s: s["launched_at"])
    if tasks:
        shell = tasks[0]
        if shell["state"] == "assigned" and shell["assigned_at"] <= start:
            result = validate_task(shell, participant, timeline, boots, edition["config"]["starter_items"])
            shell.update(state=result, match_id=match_id)
            record["task_evidence"] = {
                "task": shell["task"], "expected": shell.get("value"),
                "champion": participant.get("championName"), "role": participant.get("teamPosition"),
                "spells": [participant.get("summoner1Id"), participant.get("summoner2Id")],
                "timeline_available": timeline is not None, "boots_catalog_available": boots is not None,
                "purchases": [event.get("itemId") for frame in (timeline or {}).get("info", {}).get("frames", []) for event in frame.get("events", []) if event.get("type") == "ITEM_PURCHASED" and event.get("participantId") == participant.get("participantId")],
            }
            if result == "failed":
                record["adjustment"] = edition["config"]["failed_task_penalty"]
            record["task"] = shell["id"]
        elif shell["state"] == "unrevealed" or (shell["state"] == "assigned" and shell["assigned_at"] > start):
            shell.update(state="review", match_id=match_id)
            record["task"] = shell["id"]
        else:
            record["blocked_by"] = shell["id"]
    if reasons:
        inventory = sum(s["owner"] == str(user_id) and s["state"] == "inventory" for s in edition["shells"])
        if inventory < edition["config"]["shell_cap"]:
            edition["shells"].append({"id": uuid4().hex, "owner": str(user_id), "state": "inventory", "earned_match": match_id})
            record["shell_awarded"] = True
    player["records"].append(record)
    record["same_team_participants"] = [uid for uid, other in edition["players"].items() if uid != str(user_id) and any(p.get("puuid") == other["puuid"] and p.get("teamId") == participant.get("teamId") for p in info["participants"])]
    audit(edition, "match", user_id, now, match=match_id)
    return record


def resolve_shell(edition, shell_id, result, actor, reason, now):
    if result not in ("completed", "waived", "assigned") or not reason.strip():
        raise RuleError("invalid_resolution")
    shell = next((s for s in edition["shells"] if s["id"] == shell_id), None)
    if shell is None:
        raise RuleError("stale")
    if shell["state"] not in ("review", "failed", "assigned", "unrevealed"):
        raise RuleError("stale")
    if result == "assigned" and "task" not in shell:
        raise RuleError("no_tasks")
    shell.update(state=result, resolution=reason, resolved_by=str(actor))
    if result == "assigned":
        shell["assigned_at"] = now
        shell.pop("match_id", None)
    elif result in ("completed", "waived"):
        for record in edition["players"].get(shell.get("target"), {}).get("records", []):
            if record["id"] == shell.get("match_id"):
                record["adjustment"] = 0
    audit(edition, "resolve", actor, now, shell=shell_id, result=result, reason=reason)


def moderate_player(edition, user_id, action, actor, reason, now):
    player = edition["players"].get(str(user_id))
    if not player or not reason.strip():
        raise RuleError("invalid_resolution")
    if action == "remove":
        if phase(edition, now) == "registration":
            del edition["players"][str(user_id)]
        else:
            player["approved"] = False
    elif action.startswith("points:"):
        try:
            adjustment = int(action.split(":", 1)[1])
        except ValueError:
            raise RuleError("invalid_resolution")
        if not -100 <= adjustment <= 100:
            raise RuleError("invalid_resolution")
        player["moderation_points"] = player.get("moderation_points", 0) + adjustment
    else:
        raise RuleError("invalid_resolution")
    audit(edition, "moderate_player", actor, now, user=str(user_id), result=action, reason=reason)
