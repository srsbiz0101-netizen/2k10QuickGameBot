import csv
import time
from collections import defaultdict
from pathlib import Path

# -----------------------------
# FILE PATHS
# -----------------------------
CSV_PATH = Path("nba2k10_results.csv")
OUT_PATH = Path("overlay.txt")

REFRESH_SECONDS = 5


# -----------------------------
# CSV READ
# -----------------------------

def read_games():
    if not CSV_PATH.exists():
        return []

    with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        r = csv.reader(f)
        rows = [row for row in r if row and any(cell.strip() for cell in row)]

    if len(rows) <= 1:
        return []

    games = []

    # Defensive header detection
    header_idx = 0
    while header_idx < len(rows) and not rows[header_idx][0].strip():
        header_idx += 1

    for row in rows[header_idx + 1:]:
        try:
            gnum = int(row[0])
            ts = row[1].strip() if len(row) > 1 else ""
            t1 = row[2].strip()
            s1 = int(row[3])
            t2 = row[4].strip()
            s2 = int(row[5])
        except Exception:
            continue

        if not t1 or not t2:
            continue

        games.append((gnum, ts, t1, s1, t2, s2))

    games.sort(key=lambda x: x[0])
    return games


# -----------------------------
# TEAM STATS
# -----------------------------

def compute_team_stats(games):
    wins = defaultdict(int)
    losses = defaultdict(int)
    points_for = defaultdict(int)
    games_played = defaultdict(int)

    for _, _, t1, s1, t2, s2 in games:
        points_for[t1] += s1
        points_for[t2] += s2
        games_played[t1] += 1
        games_played[t2] += 1

        if s1 > s2:
            wins[t1] += 1
            losses[t2] += 1
        elif s2 > s1:
            wins[t2] += 1
            losses[t1] += 1

    win_pct = {}
    ppg = {}

    for t in games_played:
        gp = games_played[t]
        win_pct[t] = wins[t] / gp if gp else 0.0
        ppg[t] = points_for[t] / gp if gp else 0.0

    return wins, losses, win_pct, ppg, games_played


# -----------------------------
# ALL-TIME GAME RECORDS
# -----------------------------

def compute_total_extremes(games):
    if not games:
        return None, None

    with_totals = [
        (gnum, t1, s1, t2, s2, s1 + s2)
        for (gnum, _ts, t1, s1, t2, s2) in games
    ]
    return (
        max(with_totals, key=lambda x: x[5]),
        min(with_totals, key=lambda x: x[5]),
    )


def compute_biggest_blowout(games):
    if not games:
        return None

    candidates = []
    for (gnum, _ts, t1, s1, t2, s2) in games:
        if s1 == s2:
            continue
        if s1 > s2:
            candidates.append((gnum, t1, s1, t2, s2, s1 - s2))
        else:
            candidates.append((gnum, t2, s2, t1, s1, s2 - s1))

    return max(candidates, key=lambda x: x[5]) if candidates else None


def compute_highest_team_score(games):
    if not games:
        return None

    candidates = []
    for (gnum, _ts, t1, s1, t2, s2) in games:
        candidates.append((gnum, t1, s1, t2, s2))
        candidates.append((gnum, t2, s2, t1, s1))

    return max(candidates, key=lambda x: x[2])


# -----------------------------
# RANKING HELPERS
# -----------------------------

def rank_top_bottom(teams, key_fn, n=3):
    ranked = sorted(teams, key=key_fn, reverse=True)
    return ranked[:n], list(reversed(ranked[-n:]))


# -----------------------------
# TICKER FORMAT
# -----------------------------

def format_ticker(games):
    if not games:
        return "NBA 2K10 SIM — LIVE  |  Waiting for results...     "

    wins, losses, win_pct, ppg, games_played = compute_team_stats(games)
    teams = set(games_played.keys())

    # Last game
    gnum, ts, t1, s1, t2, s2 = games[-1]
    last_final = f"FINAL #{gnum}: {t1} {s1}, {t2} {s2}"

    # Records
    high_game, low_game = compute_total_extremes(games)
    blowout = compute_biggest_blowout(games)
    high_team = compute_highest_team_score(games)

    hg = f"HIGHEST TOTAL: #{high_game[0]} {high_game[1]} {high_game[2]}-{high_game[3]} {high_game[4]} ({high_game[5]})"
    lg = f"LOWEST TOTAL: #{low_game[0]} {low_game[1]} {low_game[2]}-{low_game[3]} {low_game[4]} ({low_game[5]})"

    if blowout:
        bg = f"BIGGEST BLOWOUT: #{blowout[0]} {blowout[1]} over {blowout[3]} by {blowout[5]} ({blowout[2]}-{blowout[4]})"
    else:
        bg = "BIGGEST BLOWOUT: N/A"

    if high_team:
        ht = f"HIGHEST TEAM SCORE: #{high_team[0]} {high_team[1]} {high_team[2]} (vs {high_team[3]} {high_team[4]})"
    else:
        ht = "HIGHEST TEAM SCORE: N/A"

    # Leaderboards
    def winpct_key(t):
        return (win_pct[t], wins[t], -losses[t], t)

    def ppg_key(t):
        return (ppg[t], games_played[t], t)

    top_w, bot_w = rank_top_bottom(teams, winpct_key)
    top_p, bot_p = rank_top_bottom(teams, ppg_key)

    top_win = "TOP WIN%: " + ", ".join(f"{t} {win_pct[t]:.3f} ({wins[t]}-{losses[t]})" for t in top_w)
    bot_win = "LOWEST WIN%: " + ", ".join(f"{t} {win_pct[t]:.3f} ({wins[t]}-{losses[t]})" for t in bot_w)
    top_ppg = "TOP PPG: " + ", ".join(f"{t} {ppg[t]:.1f}" for t in top_p)
    bot_ppg = "BOTTOM PPG: " + ", ".join(f"{t} {ppg[t]:.1f}" for t in bot_p)

    return "  |  ".join([
        "NBA 2K10 SIM — LIVE",
        last_final,
        top_win,
        bot_win,
        top_ppg,
        bot_ppg,
        hg,
        lg,
        bg,
        ht,
    ]) + "     "


# -----------------------------
# MAIN LOOP
# -----------------------------

def main():
    last_size = None
    print("[overlay] running...")

    while True:
        try:
            size = CSV_PATH.stat().st_size if CSV_PATH.exists() else 0
            if size != last_size:
                games = read_games()
                OUT_PATH.write_text(format_ticker(games), encoding="utf-8")
                print("[overlay] updated")
                last_size = size
        except Exception as e:
            print("[overlay] error:", repr(e))

        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
