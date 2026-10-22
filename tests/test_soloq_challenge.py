import copy

import pytest

from soloq_challenge import (
    DAY, WEEK, DEFAULTS, TASKS, RuleError, new_edition, phase, register, score,
    record_match, assign, launch, points, streaks, validate_task, resolve_shell,
)


@pytest.fixture
def edition():
    result = new_edition(0, "en")
    for uid in ("1", "2"):
        register(result, uid, {"name": f"Player{uid}#EUW", "puuid": f"p{uid}", "platform": "EUW1", "region": "EUROPE"}, ["Ahri", "Ashe", "Jax", "Nami"], 1)
    return result


def match(edition, number=1, week=0, **stats):
    player = {"puuid": "p1", "participantId": 1, "championName": "Ahri", "teamId": 100,
              "kills": 3, "deaths": 2, "assists": 4, "win": True, "summoner1Id": 4, "summoner2Id": 12, **stats}
    return {"metadata": {"matchId": f"EUW1_{number}"}, "info": {
        "gameStartTimestamp": (edition["starts_at"] + week * WEEK + number * 1800) * 1000,
        "gameDuration": 1200, "queueId": 420, "participants": [player],
    }}


@pytest.mark.parametrize("offset,expected", [(-1, "registration"), (0, "week1"), (WEEK-1, "week1"), (WEEK, "week2"), (2*WEEK, "settling")])
def test_phase_boundaries_are_relative_to_opening(edition, offset, expected):
    assert phase(edition, edition["starts_at"] + offset) == expected
    assert edition["starts_at"] == 3 * DAY


def test_finalization_waits_for_successful_scans(edition):
    now = edition["ends_at"] + 2 * DAY
    assert phase(edition, now) == "settling"
    for player in edition["players"].values():
        player["scan_at"] = now
    assert phase(edition, now) == "finished"


@pytest.mark.parametrize("stats,expected", [
    ({"win": True, "kills": 5, "deaths": 2, "assists": 0}, 11),
    ({"win": False, "kills": 1, "deaths": 5, "assists": 0}, -1),
    ({"win": False, "kills": 1, "deaths": 5, "assists": 5}, 0),
    ({"win": False, "kills": 3, "deaths": 3, "assists": 0}, 0),
    ({"win": True, "kills": 10, "deaths": 0, "assists": 2, "quadraKills": 1, "pentaKills": 1, "participantId": 1}, 13),
])
def test_confirmed_scoring(stats, expected):
    assert sum(score(stats, 1, DEFAULTS).values()) == expected


def test_first_fifteen_games_per_week_and_duplicate_replay(edition):
    for week in (0, 1):
        for number in range(1, 18):
            data = match(edition, week * 100 + number, week)
            record_match(edition, "1", data, None, edition["ends_at"])
            record_match(edition, "1", data, None, edition["ends_at"])
    records = edition["players"]["1"]["records"]
    assert len(records) == 30
    assert [sum(r["week"] == week for r in records) for week in (0, 1)] == [15, 15]


def test_no_weekly_rollover(edition):
    record_match(edition, "1", match(edition), None, 0)
    for number in range(1, 17):
        record_match(edition, "1", match(edition, 100+number, 1), None, 0)
    assert len(edition["players"]["1"]["records"]) == 16


def test_remakes_and_wrong_queue_do_not_count(edition):
    data = match(edition, gameEndedInEarlySurrender=True)
    data["info"]["gameDuration"] = 100
    assert record_match(edition, "1", data, None, 0) is None
    data["info"]["queueId"] = 440
    assert record_match(edition, "1", data, None, 0) is None
    assert edition["players"]["1"]["records"] == []


def test_all_hazards_still_award_one_shell(edition):
    for number in range(1, 6):
        record = record_match(edition, "1", match(edition, number, kills=25 if number == 5 else 3, deaths=0 if number == 5 else 2, assists=35 if number == 5 else 1, pentaKills=1 if number == 5 else 0), None, 0)
    assert len(record["shell_reasons"]) == 5
    assert len(edition["shells"]) == 1
    assert record["shell_awarded"] is True
    assert streaks(edition["players"]["1"]) == (5, 0)


def test_win_reward_counter_is_not_reset_by_other_rewards(edition):
    for number in range(1, 11):
        record_match(edition, "1", match(edition, number, pentaKills=int(number == 2)), None, 0)
    assert [r["id"] for r in edition["players"]["1"]["records"] if r["shell_awarded"]] == ["EUW1_2", "EUW1_5", "EUW1_10"]
    assert streaks(edition["players"]["1"]) == (10, 0)


def test_inventory_cap_and_loss_reset(edition):
    for number in range(1, 6):
        record_match(edition, "1", match(edition, number, deaths=0, win=number != 5), None, 0)
    assert len(edition["shells"]) == 3
    assert not edition["players"]["1"]["records"][-1]["shell_awarded"]
    assert streaks(edition["players"]["1"]) == (-1, 0)


def test_registration_unique_account_deadline_and_champion_pool(edition):
    with pytest.raises(RuleError, match="account_used"):
        register(edition, "3", {"puuid": "p1"}, ["Ahri"], 5)
    with pytest.raises(RuleError, match="registration_closed"):
        register(edition, "3", {"puuid": "p3"}, ["Ahri"], edition["starts_at"])
    with pytest.raises(RuleError, match="champions_required"):
        register(edition, "3", {"puuid": "p3"}, [], 5)


@pytest.mark.parametrize("task,value,stats,expected", [
    ("no_flash", None, {"summoner1Id": 4, "summoner2Id": 12}, "failed"),
    ("no_flash", None, {"summoner1Id": 7, "summoner2Id": 12}, "completed"),
    ("no_flash", None, {}, "review"),
    ("random_role", "JUNGLE", {"teamPosition": "TOP"}, "review"),
    ("random_role", "TOP", {"teamPosition": "TOP"}, "completed"),
    ("random_champion", "Ahri", {"championName": "Ahri"}, "completed"),
    ("sender_main", "Ahri", {"championName": "Ashe"}, "failed"),
    ("new_champion", ["Nami"], {"championName": "Nami"}, "completed"),
    ("no_last_three", ["Nami"], {"championName": "Ahri"}, "failed"),
])
def test_task_validation(task, value, stats, expected):
    assert validate_task({"task": task, "value": value}, stats) == expected


@pytest.mark.parametrize("task,item", [("no_boots", 1001), ("no_starter", 1055)])
def test_purchase_history_catches_sold_items(task, item):
    shell = {"task": task}
    events = [{"type": "ITEM_PURCHASED", "itemId": item, "participantId": 1}, {"type": "ITEM_SOLD", "itemId": item, "participantId": 1}, {"type": "GAME_END"}]
    timeline = {"info": {"frames": [{"events": events}]}}
    assert validate_task(shell, {"participantId": 1}, timeline, {1001}, [1055]) == "failed"
    assert validate_task(shell, {"participantId": 1}, None, {1001}, [1055]) == "review"


def test_complete_timeline_required_for_purchase_success():
    shell = {"task": "no_boots"}
    timeline = {"info": {"frames": [{"events": []}]}}
    assert validate_task(shell, {"participantId": 1}, timeline, {1001}) == "review"
    timeline["info"]["frames"][0]["events"].append({"type": "GAME_END"})
    assert validate_task(shell, {"participantId": 1}, timeline, {1001}) == "completed"


def test_consecutive_attacks_have_no_receive_cooldown(edition):
    edition["config"]["receive_cooldown_hours"] = 24  # Legacy settings must not restrict attacks.
    edition["shells"] = [
        {"id": shell_id, "owner": "2", "state": "inventory"}
        for shell_id in ("s1", "s2")
    ]
    now = edition["starts_at"] + 1
    launch(edition, "2", "1", now)
    launch(edition, "2", "1", now)
    assert all(shell["state"] == "unrevealed" for shell in edition["shells"])


def test_launch_assign_and_next_match_completion(edition):
    edition["shells"] = [{"id": "shell1", "owner": "2", "state": "inventory"}]
    now = edition["starts_at"] + 1
    shell = launch(edition, "2", "1", now)
    assert shell["state"] == "unrevealed"
    edition["config"]["tasks"] = ["no_flash"]
    assign(edition, "shell1", lambda choices: choices[0], now + 1)
    record = record_match(edition, "1", match(edition, summoner1Id=7), None, now+2000)
    assert record["task"] == "shell1"
    assert shell["state"] == "completed"
    assert assign(edition, "shell1", lambda _: pytest.fail("Reroll"), now+2001)["state"] == "completed"


def test_unrevealed_task_is_not_evaded_and_moderation_preserves_evidence(edition):
    edition["shells"] = [{"id": "s1", "owner": "2", "state": "inventory"}]
    launch(edition, "2", "1", edition["starts_at"]+1)
    record_match(edition, "1", match(edition, 1), None, 0)
    record_match(edition, "1", match(edition, 2), None, 0)
    shell = edition["shells"][0]
    assert shell["state"] == "review"
    assert shell["match_id"] == "EUW1_1"
    resolve_shell(edition, "s1", "waived", "admin", "Verified evidence", 100)
    assert shell["state"] == "waived"
    assert edition["audit"][-1]["actor"] == "admin"


def test_assignment_uses_declared_pool_and_freezes_recent_champions(edition):
    record_match(edition, "1", match(edition, 1, championName="Ahri"), None, 0)
    edition["config"]["tasks"] = ["no_last_three"]
    edition["shells"] = [{"id": "s", "owner": "2", "target": "1", "state": "unrevealed"}]
    shell = assign(edition, "s", lambda choices: choices[0], edition["starts_at"]+2000)
    assert "Ahri" not in shell["value"]
    assert set(shell["value"]) <= set(edition["players"]["1"]["champions"])


def test_same_team_does_not_automatically_penalize_duo(edition):
    data = match(edition)
    data["info"]["participants"].append({"puuid": "p2", "teamId": 100})
    record = record_match(edition, "1", data, None, 0)
    assert record["same_team_participants"] == ["2"]
    assert record["duo"] == "unverifiable"
    assert "adjustment" not in record


def test_failures_do_not_shift_week_boundaries_or_reorder_games(edition):
    record_match(edition, "1", match(edition, 2), None, 0)
    with pytest.raises(RuleError, match="out_of_order"):
        record_match(edition, "1", match(edition, 1), None, 0)


def test_revealing_during_a_game_does_not_evade_review(edition):
    edition["shells"] = [{"id": "s", "owner": "2", "state": "inventory"}]
    start = edition["starts_at"]
    launch(edition, "2", "1", start+1)
    edition["config"]["tasks"] = ["no_flash"]
    assign(edition, "s", lambda choices: choices[0], start+2000)
    record_match(edition, "1", match(edition, 1), None, start+3000)
    assert edition["shells"][0]["state"] == "review"
