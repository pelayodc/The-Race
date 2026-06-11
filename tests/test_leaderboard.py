from types import SimpleNamespace

import pytest

from leaderboard import leaderboard_embed, recent_results_text


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
    assert "✅" in recent_field
    assert "🎮" in recent_field


def test_recent_results_text_marks_active_game(make_summoner, no_database):
    summoner = make_summoner("Player#EUW")
    summoner.game1Win = True
    summoner.game2Win = False
    summoner.game3Remake = True

    assert recent_results_text(summoner, in_solo_queue=True) == "✅❌➖▫️🎮"
