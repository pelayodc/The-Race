from types import SimpleNamespace

import pytest

from leaderboard import SoloQueueSubscriptionView, leaderboard_embed, recent_results_text


class FakeGuild:
    def __init__(self, members):
        self.members = members

    def get_member(self, user_id):
        return self.members.get(user_id)

    async def fetch_member(self, user_id):
        return self.members.get(user_id)


@pytest.mark.asyncio
async def test_public_leaderboard_hides_secondaries_renumbers_and_links(make_summoner, no_database):
    primary = make_summoner("Primary#EUW", player_id="1", position=1, puuid="puuid-primary")
    secondary = make_summoner("Secondary#EUW", player_id="1", primary=False, position=2)
    other = make_summoner("Other#EUW", player_id="2", position=3, puuid="puuid-other")
    json_data = {
        "botLanguage": "en",
        "summoners": {
            "Primary#EUW": {
                "discordUserId": "1",
                "discordPrimary": True,
                "discordDisplayName": "CachedPrimary",
                "recentMatchIds": ["EUW1_1"],
            },
            "Secondary#EUW": {"discordUserId": "1", "discordPrimary": False},
            "Other#EUW": {"discordUserId": "2", "discordPrimary": True},
        },
        "matchData": {
            "EUW1_1": {
                "info": {
                    "participants": [
                        {"puuid": "puuid-primary", "win": True, "gameEndedInEarlySurrender": False}
                    ]
                }
            }
        },
        "soloQueueStatus": {"1": {"inGame": True}},
    }
    guild = FakeGuild({1: SimpleNamespace(display_name="LivePrimary")})

    embed = await leaderboard_embed(
        json_data,
        [primary, secondary, other],
        guild=guild,
        include_secondaries=False,
        use_discord_display_names=True,
        renumber_visible=True,
    )

    summoners_field = embed.fields[0].value
    recent_field = embed.fields[2].value
    assert "LivePrimary" in summoners_field
    assert "Secondary" not in summoners_field
    assert "**#1**" in summoners_field
    assert "**#2**" in summoners_field
    assert "**#3**" not in summoners_field
    assert "https://dpm.lol/Primary-EUW" in summoners_field
    assert "🎮" in summoners_field
    assert recent_field.splitlines()[0] == "🎮✅▫️▫️▫️"


def test_recent_results_text_marks_active_game(make_summoner, no_database):
    summoner = make_summoner("Player#EUW")
    summoner.game1Win = True
    summoner.game2Win = False
    summoner.game3Remake = True

    assert recent_results_text(summoner, in_solo_queue=True) == "🎮✅❌➖▫️"

    summoner.game4Win = True
    summoner.game5Win = False
    assert recent_results_text(summoner, in_solo_queue=False) == "✅❌➖✅❌"
    assert recent_results_text(summoner, in_solo_queue=True) == "🎮✅❌➖✅"


@pytest.mark.asyncio
async def test_subscription_view_builds_with_existing_subscription(make_summoner, no_database):
    json_data = {
        "botLanguage": "en",
        "soloQueueSubscriptions": {"99": ["10"]},
    }
    target = {
        "playerId": "10",
        "displayName": "Player",
        "summonerFullName": "Player#EUW",
        "summoner": make_summoner("Player#EUW", player_id="10"),
    }

    view = SoloQueueSubscriptionView(json_data, "99", [target])

    assert len(view.children) == 1
    assert view.children[0].options[0].label.startswith("✓ ")


def test_format_tier_rank_omits_division_for_apex_tiers():
    from personal_report import format_cached_rank
    from utils.commonUtils import Rank

    assert Rank.formatTierRank("MASTER", "I") == "MASTER"
    assert Rank.formatTierRank("MASTER", "I", numeric=True) == "MASTER"
    assert Rank.formatTierRank("MASTER", 42, numeric=True) == "MASTER"
    assert Rank.formatTierRank("GRANDMASTER", "I") == "GRANDMASTER"
    assert Rank.formatTierRank("CHALLENGER", "I", numeric=True) == "CHALLENGER"
    assert Rank.formatTierRank("GOLD", "II") == "GOLD II"
    assert Rank.formatTierRank("GOLD", "II", numeric=True) == "GOLD 2"
    assert "MASTER - 150 LP" in format_cached_rank({"tier": "MASTER", "rank": "I", "leaguePoints": 150})
    assert "MASTER I" not in format_cached_rank({"tier": "MASTER", "rank": "I", "leaguePoints": 150})


@pytest.mark.asyncio
async def test_leaderboard_embed_shows_master_without_division(make_summoner, no_database):
    master = make_summoner("MasterPlayer#EUW", player_id="1", position=1, tier="MASTER", rank="I", lp=150)
    gold = make_summoner("GoldPlayer#EUW", player_id="2", position=2, tier="GOLD", rank="II", lp=50)
    json_data = {
        "botLanguage": "en",
        "summoners": {
            "MasterPlayer#EUW": {"discordUserId": "1", "discordPrimary": True},
            "GoldPlayer#EUW": {"discordUserId": "2", "discordPrimary": True},
        },
    }

    embed = await leaderboard_embed(json_data, [master, gold], include_secondaries=False, use_discord_display_names=False)
    rank_lines = embed.fields[1].value.splitlines()
    assert "MASTER - **150 LP**" in rank_lines[0]
    assert "MASTER I" not in rank_lines[0]
    assert "GOLD II - **50 LP**" in rank_lines[1]
