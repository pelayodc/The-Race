from unittest.mock import AsyncMock

import pytest

from solo_queue import (
    handle_game_finished,
    handle_game_started,
    retry_pending_finishes,
    toggle_subscription,
    visible_solo_queue_targets,
)


def test_visible_targets_excludes_secondaries_deduplicates_and_limits(make_summoner, no_database):
    summoners = [make_summoner(f"Player{i}#EUW", player_id=str(i), position=i) for i in range(1, 31)]
    secondary = make_summoner("Alt#EUW", player_id="1", primary=False, position=31)
    duplicate = make_summoner("Duplicate#EUW", player_id="2", position=32)
    json_data = {
        "summoners": {
            summoner.fullName: {"discordUserId": str(index), "discordPrimary": True}
            for index, summoner in enumerate(summoners, start=1)
        }
    }
    json_data["summoners"]["Alt#EUW"] = {"discordUserId": "1", "discordPrimary": False}
    json_data["summoners"]["Duplicate#EUW"] = {"discordUserId": "2", "discordPrimary": True}

    targets = visible_solo_queue_targets(json_data, summoners + [secondary, duplicate], limit=25)

    assert len(targets) == 25
    assert targets[0]["playerId"] == "1"
    assert "Alt#EUW" not in [target["summonerFullName"] for target in targets]
    assert len({target["playerId"] for target in targets}) == 25


@pytest.mark.asyncio
async def test_toggle_subscription_requires_dm_before_persisting(monkeypatch, make_summoner, no_database):
    import solo_queue

    writes = []
    monkeypatch.setattr(solo_queue, "writeToJsonFile", lambda path, data: writes.append(data.copy()))
    monkeypatch.setattr(solo_queue, "send_dm", AsyncMock(return_value=False))

    json_data = {"botLanguage": "en", "soloQueueSubscriptions": {}}
    target = {
        "playerId": "10",
        "summonerFullName": "Player#EUW",
        "summoner": make_summoner("Player#EUW", player_id="10"),
    }

    success, message = await toggle_subscription(json_data, "99", target)

    assert success is False
    assert "DM" in message or "direct" in message.lower()
    assert json_data["soloQueueSubscriptions"] == {}
    assert writes == []

    monkeypatch.setattr(solo_queue, "send_dm", AsyncMock(return_value=True))
    success, _message = await toggle_subscription(json_data, "99", target)
    assert success is True
    assert json_data["soloQueueSubscriptions"] == {"99": ["10"]}
    assert writes

    success, _message = await toggle_subscription(json_data, "99", target)
    assert success is True
    assert json_data["soloQueueSubscriptions"] == {}


@pytest.mark.asyncio
async def test_solo_queue_start_pending_and_finish_flow(monkeypatch, make_summoner, no_database):
    import solo_queue

    monkeypatch.setattr(solo_queue, "notify_subscribers", AsyncMock(return_value=["99"]))
    monkeypatch.setattr(solo_queue, "log_event", lambda *args, **kwargs: None)
    summoner = make_summoner("Player#EUW", player_id="10", score=500, lp=50, puuid="puuid-player")
    target = {"playerId": "10", "summoner": summoner, "summonerFullName": summoner.fullName}
    json_data = {
        "botLanguage": "en",
        "summoners": {
            "Player#EUW": {
                "discordUserId": "10",
                "discordPrimary": True,
                "recentMatchIds": ["EUW1_123"],
            }
        },
        "soloQueueStatus": {},
        "soloQueuePendingFinish": {},
        "soloQueueSubscriptions": {"99": ["10"]},
        "matchData": {},
    }

    await handle_game_started(json_data, target, {"gameId": "123", "gameStartTime": 1000})
    started = json_data["soloQueueStatus"]["10"]
    assert started["inGame"] is True
    assert started["gameId"] == "123"
    assert started["notifiedStartUserIds"] == ["99"]

    await handle_game_finished(json_data, target, started)
    assert "10" in json_data["soloQueuePendingFinish"]
    assert json_data["soloQueueStatus"]["10"]["inGame"] is False

    summoner.score = 520
    json_data["matchData"]["EUW1_123"] = {
        "info": {
            "gameId": 123,
            "participants": [{"puuid": "puuid-player", "win": True}],
        }
    }

    assert await retry_pending_finishes(json_data, target) is True
    assert json_data["soloQueuePendingFinish"] == {}
    assert json_data["soloQueueStatus"]["10"]["inGame"] is False
