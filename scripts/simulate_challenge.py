"""Offline challenge simulation. Never imports storage, Discord or Riot clients."""

import argparse
import html
import json
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from soloq_challenge import (
    DAY, WEEK, TASKS, RuleError, assign, launch, new_edition, phase,
    points, record_match, register, resolve_shell,
)


def make_edition():
    edition = new_edition(1800000000, "es")
    for uid, name in (("1", "Ana"), ("2", "Bruno"), ("3", "Carla")):
        register(edition, uid, {
            "name": f"{name}#TEST", "display_name": name,
            "puuid": f"fake-{uid}", "platform": "EUW1", "region": "EUROPE",
            "approved": True,
        }, ["Ahri", "Ashe", "Jax", "Nami"], edition["created_at"] + 1)
    return edition


def make_match(edition, uid, number, week=0, **stats):
    participant = {
        "puuid": f"fake-{uid}", "participantId": 1, "teamId": 100,
        "championName": "Ahri", "teamPosition": "MIDDLE",
        "kills": 4, "deaths": 2, "assists": 5, "win": True,
        "summoner1Id": 7, "summoner2Id": 12, **stats,
    }
    return {"metadata": {"matchId": f"SIM_{week}_{uid}_{number}"}, "info": {
        "gameStartTimestamp": (edition["starts_at"] + week * WEEK + number * 3600) * 1000,
        "gameDuration": 1800, "queueId": 420, "participants": [participant],
    }}


def simulate(seed=42):
    rng = random.Random(seed)
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})

    edition = make_edition()
    check("Inscripcion de tres jugadores", len(edition["players"]) == 3)
    check("Inscripcion abierta durante tres dias", phase(edition, edition["starts_at"] - 1) == "registration")
    try:
        register(edition, "4", {"puuid": "late"}, ["Ahri"], edition["starts_at"])
    except RuleError as exc:
        check("Inscripcion cerrada al empezar", str(exc) == "registration_closed")
    else:
        check("Inscripcion cerrada al empezar", False)

    ignored = 0
    for week in (0, 1):
        check(f"Inicio de semana {week + 1}", phase(edition, edition["starts_at"] + week * WEEK) == f"week{week + 1}")
        for number in range(1, 18):
            for uid in edition["players"]:
                data = make_match(edition, uid, number, week,
                                  win=number <= 5 or rng.choice([True, False]),
                                  kills=25 if number == 1 else rng.randrange(1, 12),
                                  deaths=rng.randrange(1, 8), assists=rng.randrange(0, 15))
                now = data["info"]["gameStartTimestamp"] / 1000 + 1800
                result = record_match(edition, uid, data, 1, now)
                ignored += result is None
                duplicate = record_match(edition, uid, data, 1, now)
                check(f"Sin duplicados: {data['metadata']['matchId']}", duplicate is None)
            if week == 0 and number == 1:
                # Both attackers earned a shell through a real counted match above.
                edition["config"]["tasks"] = ["no_flash"]
                now += 1
                first = launch(edition, "2", "1", now)
                second = launch(edition, "3", "1", now)
                assign(edition, first["id"], rng.choice, now + 1)
                assign(edition, second["id"], rng.choice, now + 1)
                check("Dos ataques consecutivos sin espera", first["target"] == second["target"] == "1")
                edition["config"]["tasks"] = list(TASKS)

    for uid, player in edition["players"].items():
        check(f"{player['display_name']}: 15 partidas por semana",
              [sum(r["week"] == w for r in player["records"]) for w in (0, 1)] == [15, 15])
        check(f"{player['display_name']}: maximo una Blue Shell por partida",
              len({s["earned_match"] for s in edition["shells"] if s["owner"] == uid}) ==
              sum(s["owner"] == uid for s in edition["shells"]))
    check("Doce partidas extra ignoradas", ignored == 12)
    records = edition["players"]["1"]["records"]
    check("Retos en orden: uno por partida", records[1]["task"] == first["id"] and records[2]["task"] == second["id"])
    check("Ambos retos completados", first["state"] == second["state"] == "completed")
    check("Conciliacion al finalizar", phase(edition, edition["ends_at"]) == "settling")
    for player in edition["players"].values():
        player["scan_at"] = edition["ends_at"] + DAY
    check("Edicion finalizada tras conciliacion", phase(edition, edition["ends_at"] + DAY) == "finished")

    scenarios = []
    for task in TASKS:
        for outcome in ("completed", "failed", "review"):
            if outcome == "review" and task not in ("no_boots", "no_starter", "random_role", "no_flash"):
                continue
            case = make_edition()
            case["config"]["tasks"] = [task]
            record_match(case, "2", make_match(case, "2", 1, kills=25), 1, case["starts_at"] + 5400)
            now = case["starts_at"] + 5500
            shell = launch(case, "2", "1", now)
            assign(case, shell["id"], rng.choice, now + 1)
            stats = {}
            timeline = {"info": {"frames": [{"events": [{"type": "GAME_END"}]}]}}
            if task in ("new_champion", "no_last_three"):
                stats["championName"] = shell["value"][0] if outcome == "completed" else "Zed"
            elif task in ("random_champion", "sender_main"):
                stats["championName"] = shell["value"] if outcome == "completed" else "Zed"
            elif task == "no_flash":
                stats["summoner1Id"] = {"completed": 7, "failed": 4, "review": None}[outcome]
            elif task == "random_role":
                # A role mismatch is always review (autofill cannot be proven).
                if outcome == "failed":
                    continue
                stats["teamPosition"] = shell["value"] if outcome == "completed" else "UNKNOWN"
            elif outcome == "review":
                timeline = None
            elif outcome == "failed":
                timeline["info"]["frames"][0]["events"].append({
                    "type": "ITEM_PURCHASED", "participantId": 1,
                    "itemId": 1001 if task == "no_boots" else 1055,
                })
            record = record_match(case, "1", make_match(case, "1", 2, **stats), 1,
                                  now + 4000, timeline=timeline, boots={1001})
            check(f"Reto {task}: {outcome}", shell["state"] == outcome)
            before = shell["state"]
            if outcome in ("review", "failed"):
                resolve_shell(case, shell["id"], "waived", "admin", "Revision simulada", now + 5000)
                check(f"Moderacion {task}: {outcome}", shell["state"] == "waived" and bool(case["audit"][-1]["reason"]))
            scenarios.append({"task": task, "expected": outcome, "actual": before, "record": record, "edition": case})
    check("KDA negativo resta un punto", record_match(
        case, "3", make_match(case, "3", 3, win=False, kills=1, deaths=8, assists=1), None,
        now + 8000)["points"] == {"result": 0, "negative_kda": -1})

    ranking = sorted(({
        "player": p["display_name"], "points": points(p), "games": len(p["records"]),
    } for p in edition["players"].values()), key=lambda p: (-p["points"], p["player"]))
    return {"simulation": True, "seed": seed, "checks": checks,
            "passed": all(c["passed"] for c in checks), "ranking": ranking,
            "edition": edition, "task_scenarios": scenarios}


def render_report(report):
    escape = lambda value: html.escape(str(value))
    rows = "".join(f"<tr><td>{escape(p['player'])}</td><td>{p['points']}</td><td>{p['games']}</td></tr>" for p in report["ranking"])
    checks = "".join(f"<li class={'ok' if c['passed'] else 'fail'}>{'OK' if c['passed'] else 'ERROR'}: {escape(c['name'])}</li>" for c in report["checks"])
    history = "".join(f"<details><summary>{escape(p['display_name'])}: historial de partidas</summary><pre>{escape(json.dumps(p['records'], indent=2, ensure_ascii=False))}</pre></details>" for p in report["edition"]["players"].values())
    return f"""<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Simulacion SoloQ Challenge</title><style>
body{{font:16px system-ui;margin:32px auto;padding:0 20px;max-width:1000px;color:#202124;background:#fff}}
table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #ddd}}
.ok{{color:#176b36}}.fail{{color:#b32025}}li{{margin:6px 0}}pre{{overflow:auto;background:#f3f4f5;padding:12px}}summary{{cursor:pointer;padding:12px 0}}
</style><h1>Simulacion SoloQ Challenge</h1>
<p>Datos ficticios. Sin conexiones a Discord, Riot o PostgreSQL. Semilla: {report['seed']}.</p>
<h2>{'Correcto' if report['passed'] else 'Hay errores'}: {sum(c['passed'] for c in report['checks'])}/{len(report['checks'])} comprobaciones</h2>
<h2>Clasificacion final</h2><table><tr><th>Jugador</th><th>Puntos</th><th>Partidas</th></tr>{rows}</table>
<h2>Comprobaciones</h2><ul>{checks}</ul><h2>Historial</h2>{history}</html>"""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Simula una edicion completa sin modificar datos reales.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("simulation-report"))
    args = parser.parse_args(argv)
    report = simulate(args.seed)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.output / "index.html").write_text(render_report(report), encoding="utf-8")
    print(f"Simulacion: {sum(c['passed'] for c in report['checks'])}/{len(report['checks'])} comprobaciones correctas.")
    for check in report["checks"]:
        if not check["passed"]:
            print(f"ERROR: {check['name']}")
    print(f"Informe: {(args.output / 'index.html').resolve()}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
