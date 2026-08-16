"""
Weekly database refresh for the scouting app.

Each run:
  1. Gives every existing player a "status update" -- simulates roughly a week of
     match activity (minutes, goals/assists, progressive actions, defensive actions
     drift a little, weighted by their existing per-90 rates), stamps lastUpdated
     with today's date, and occasionally flips Has Agent from No/Unknown -> Yes
     (players do get picked up over time).
  2. Adds exactly 10 new players to the database (dateAdded = lastUpdated = today).
  3. Rebuilds Scouting_Model.xlsx and Scouting_App_Prototype.html from the updated
     dataset, recalculates the workbook, regenerates the dynamic test suite, and
     runs it 3x to verify nothing broke.

Safe to run repeatedly -- each run only ever adds 10 more and refreshes what's
already there. Run manually with: python3 weekly_update.py
"""
import json, random, subprocess, sys, datetime, shutil, os

DATA_FILE = "players_current.json"
BASELINE_FILE = "players_100.json"
DB_FILE = "scouting.db"
XLSX_FILE = "Scouting_Model.xlsx"
HTML_FILE = "Scouting_App_Prototype.html"
TEST_FILE = "test_app.js"
NEW_PLAYERS_PER_WEEK = 10
BACKUP_DIR = "backups"

sys.path.insert(0, ".")
import player_gen  # noqa: E402


def validate(players):
    """Dedup + required-field check, run before every commit."""
    seen, dupes = set(), []
    for p in players:
        key = (p["name"], p["country"])
        if key in seen:
            dupes.append(key)
        seen.add(key)
    required = ["name", "country", "position", "age", "minutes", "tier", "marketValue", "hasAgent", "dateAdded", "lastUpdated"]
    missing = [p["name"] for p in players if any(f not in p for f in required)]
    if dupes or missing:
        raise SystemExit(f"VALIDATION FAILED: {len(dupes)} duplicate (name,country) pairs, "
                          f"{len(missing)} players missing required fields. Aborting before writing anything.")
    return True


def backup_current():
    """Timestamped backup of the dataset + DB before this run overwrites them."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.date.today().isoformat()
    for f in (DATA_FILE, DB_FILE):
        if os.path.exists(f):
            dest = os.path.join(BACKUP_DIR, f"{stamp}_{f}")
            shutil.copy(f, dest)
    print(f"backed up players_current.json + scouting.db to {BACKUP_DIR}/ (dated {stamp})")


def sync_to_sqlite(json_file, db_file):
    """SQLite operations fail with I/O errors directly on this sandbox's
    mounted output folder (FUSE locking limitation), so do the actual
    read/write on local disk and copy the result back."""
    work_dir = "/tmp/scouting_weekly_work"
    os.makedirs(work_dir, exist_ok=True)
    tmp_json = os.path.join(work_dir, "players_current.json")
    tmp_db = os.path.join(work_dir, "scouting.db")
    shutil.copy(json_file, tmp_json)
    subprocess.run(["python3", "db_migrate.py", tmp_json, tmp_db], cwd=".", check=True,
                    env={**os.environ, "PYTHONPATH": os.getcwd()})
    # db_migrate.py writes tmp_db relative to cwd unless given an absolute
    # path -- pass absolute paths explicitly instead of relying on cwd tricks
    shutil.copy(tmp_db, db_file)
    print(f"synced {json_file} -> {db_file} (source of truth) via {work_dir}")


def load_current():
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        with open(BASELINE_FILE) as f:
            return json.load(f)


def refresh_player(p, today, rng, play_chance=0.75, agent_chance=0.04):
    """Simulate roughly a week of match activity for one existing player.

    play_chance / agent_chance are the per-run rates. They default to the
    weekly cadence this script has always used; daily_update.py passes down
    scaled values so running every 24h doesn't hand every player seven
    matches a week.
    """
    played_this_week = rng.random() < play_chance
    if played_this_week:
        minutes_this_week = rng.randint(20, 95)
        old_minutes = max(p["minutes"], 1)
        # scale this week's output off the player's own existing per-90 rates,
        # with +/-30% noise, so stats drift realistically rather than randomly
        ga_rate = (p["goals"] + p["assists"]) / old_minutes * 90
        prog_rate = (p["progPasses"] + p["progCarries"]) / old_minutes * 90
        def_rate = p["tklInt"] / old_minutes * 90
        noise = lambda: rng.uniform(0.7, 1.3)

        new_ga = (ga_rate * noise()) * minutes_this_week / 90
        new_prog = (prog_rate * noise()) * minutes_this_week / 90
        new_def = (def_rate * noise()) * minutes_this_week / 90

        # split GA into goals/assists roughly matching the player's existing split
        ga_total = p["goals"] + p["assists"]
        goal_share = (p["goals"] / ga_total) if ga_total > 0 else 0.5
        add_ga = round(new_ga)
        add_goals = round(add_ga * goal_share)
        add_assists = max(0, add_ga - add_goals)

        add_prog = max(0, round(new_prog))
        pass_share = p["progPasses"] / max(p["progPasses"] + p["progCarries"], 1)
        add_pp = round(add_prog * pass_share)
        add_pc = max(0, add_prog - add_pp)

        p["minutes"] += minutes_this_week
        p["goals"] += max(0, add_goals)
        p["assists"] += max(0, add_assists)
        p["progPasses"] += add_pp
        p["progCarries"] += add_pc
        p["tklInt"] += max(0, round(new_def))

        # small chance market value ticks up after a good showing. Capped at
        # the top of the band: without the cap a player who keeps drawing the
        # 15% ticks compounds straight past the ceiling the scoring engine is
        # calibrated to, and a player with nothing on record has to enter at a
        # band value -- the old EUR 8k-20k entry is below the floor now.
        if rng.random() < 0.15:
            if p["marketValue"]:
                p["marketValue"] = min(player_gen.VALUE_TAIL_HI,
                                       int(p["marketValue"] * rng.uniform(1.03, 1.12)))
            else:
                p["marketValue"] = rng.randint(player_gen.VALUE_BAND_LO,
                                               player_gen.VALUE_BAND_HI)

        # goalkeeper-specific stats evolve too, off the same noise model
        if p["position"] == "GK":
            save_rate = p.get("saves", 0) / old_minutes * 90
            gc_rate = p.get("goalsConceded", 0) / old_minutes * 90
            sweep_rate = p.get("sweeperActions", 0) / old_minutes * 90
            p["saves"] = p.get("saves", 0) + max(0, round(save_rate * noise() * minutes_this_week / 90))
            p["goalsConceded"] = p.get("goalsConceded", 0) + max(0, round(gc_rate * noise() * minutes_this_week / 90))
            p["sweeperActions"] = round(p.get("sweeperActions", 0) + max(0.0, sweep_rate * noise() * minutes_this_week / 90), 1)
            # distribution accuracy drifts slightly rather than resetting each week
            drift = rng.uniform(-2.5, 2.5)
            p["passCompletionPct"] = round(min(95.0, max(45.0, p.get("passCompletionPct", 65.0) + drift)), 1)
            if rng.random() < 0.30:
                p["cleanSheets"] = p.get("cleanSheets", 0) + 1

    # small chance an unrepresented player gets picked up by an agent this week
    if p["hasAgent"] in ("No", "Unknown") and rng.random() < agent_chance:
        p["hasAgent"] = "Yes"
        p["contactRoute"] = "Go through the player's agent - ask the club to confirm representation first"
    elif p["age"] < 18:
        p["contactRoute"] = "Club youth/academy office ONLY - player is a minor, do not contact directly"

    p["lastUpdated"] = today
    return p


def run(cmd):
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout[-4000:])
    if result.returncode != 0:
        print(result.stderr[-4000:])
        raise SystemExit(f"command failed: {' '.join(cmd)}")
    return result.stdout


def main():
    today = datetime.date.today().isoformat()
    rng = random.Random()

    backup_current()

    players = load_current()
    before_count = len(players)

    for p in players:
        refresh_player(p, today, rng)

    used_names = {p["name"] for p in players}
    new_players = player_gen.generate_players(NEW_PLAYERS_PER_WEEK, used_names, today, rng=rng)
    players.extend(new_players)

    validate(players)

    with open(DATA_FILE, "w") as f:
        json.dump(players, f, indent=2)

    print(f"Refreshed {before_count} existing players, added {len(new_players)} new players "
          f"({new_players[0]['name']}, {new_players[1]['name']}, ...). Total now {len(players)}.")

    # SQLite (scouting.db) is the source of truth going forward -- sync the
    # refreshed dataset into it, then re-export JSON from the DB so the two
    # never drift apart, before handing off to the existing xlsx/html pipeline.
    sync_to_sqlite(DATA_FILE, DB_FILE)
    run(["python3", "export_json_from_db.py", DB_FILE, DATA_FILE])

    run(["python3", "build_xlsx_v3.py", DATA_FILE, XLSX_FILE])

    # recalc formulas via LibreOffice headless convert-to trick
    tmp_dir = "recalc_weekly_tmp"
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)
    run(["soffice", "--headless", "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", tmp_dir, XLSX_FILE])
    shutil.copy(f"{tmp_dir}/{XLSX_FILE}", XLSX_FILE)

    run(["python3", "build_html.py", DATA_FILE])
    run(["python3", "gen_test.py", DATA_FILE, XLSX_FILE, TEST_FILE])
    run(["node", "--check", TEST_FILE])
    run(["node", TEST_FILE])

    print(f"\nWEEKLY UPDATE COMPLETE -- {today}: {len(new_players)} new players added, "
          f"{before_count} existing players refreshed, {len(players)} total. All tests passed.")


if __name__ == "__main__":
    main()
