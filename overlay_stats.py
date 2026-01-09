import csv
import time
from collections import defaultdict
from pathlib import Path

# -----------------------------
# FILE PATHS
# -----------------------------
CSV_PATH = Path("nba2k10_results.csv")
OUT_PATH = Path("overlay.txt")

# If you ever run from another folder, uncomment and set BASE
# BASE = Path(r"C:\Users\srsbi\Desktop\2KAllDay")
# CSV_PATH = BASE / "nba2k10_results.csv"
# OUT_PATH = BASE / "overlay.txt"

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
    start_idx = 0
    while start_idx < len(rows) and not rows[start_idx][0].strip():
        start_idx += 1

    for row in rows[start_idx + 1:]:
        try:
            gnum = int(row[0])
            ts = row[1].strip()
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
        win_pct[t] = (wins[t] / gp) if gp else 0.0
        ppg[t] = (points_for[t] / gp) if gp else 0.0

    return wins, losses, win_pct, ppg, games_played


# -----------------------------
# ALL-TIME GAME EXTREMES
# -----------------------------

def compute_total_extremes(games):
    """
    Returns (high, low) where each is:
      (gnum, t1, s1, t2, s2, total)
    or (None, None) if no games.
    """
    if not games:
        return None, None

    # total points in a game = s1 + s2
    with_totals = [
        (gnum, t1, s1, t2, s2, s1 + s2)
        for (gnum, _ts, t1, s1, t2, s2) in games
    ]

    high = max(with_totals, key=lambda x: x[5])
    low = min(with_totals, key=lambda x: x[5])
    return high, low


# -----------------------------
# RANKING HELPERS
# -----------------------------

def rank_top_bottom(teams, key_fn, n=3):
    if not teams:
        return [], []

    ranked = sorted(teams, key=key_fn, reverse=True)
    top = ranked[:n]
    bottom = list(reversed(ranked[-n:]))

    return top, bottom


# -----------------------------
# TICKER FORMAT
# -----------------------------

def format_ticker(games):
    if not games:
        return "NBA 2K10 SIM — LIVE  |  Waiting for results...     "

    wins, losses, win_pct, ppg, games_played = compute_team_stats(games)
    teams = set(games_played.keys())

    # Last game only
    gnum, ts, t1, s1, t2, s2 = games[-1]
    last_final = f"FINAL #{gnum}: {t1} {s1}, {t2} {s2}"

    # All-time high/low total scoring games
    high_game, low_game = compute_total_extremes(games)

    if high_game:
        hgnum, ht1, hs1, ht2, hs2, htotal = high_game
        high_total_text = f"HIGHEST TOTAL: #{hgnum} {ht1} {hs1}-{ht2} {hs2} ({htotal})"
    else:
        high_total_text = "HIGHEST TOTAL: N/A"

    if low_game:
        lgnum, lt1, ls1, lt2, ls2, ltotal = low_game
        low_total_text = f"LOWEST TOTAL: #{lgnum} {lt1} {ls1}-{lt2} {ls2} ({ltotal})"
    else:
        low_total_text = "LOWEST TOTAL: N/A"

    # Win % leaderboards
    def winpct_key(t):
        return (win_pct[t], wins[t], -losses[t], t)

    top_winpct, bottom_winpct = rank_top_bottom(teams, winpct_key, n=3)

    top_winpct_text = "TOP WIN%: " + ", ".join(
        f"{t} {win_pct[t]:.3f} ({wins[t]}-{losses[t]})"
        for t in top_winpct
    )

    bottom_winpct_text = "LOWEST WIN%: " + ", ".join(
        f"{t} {win_pct[t]:.3f} ({wins[t]}-{losses[t]})"
        for t in bottom_winpct
    )

    # PPG leaderboards
    def ppg_key(t):
        return (ppg[t], games_played[t], t)

    top_ppg, bottom_ppg = rank_top_bottom(teams, ppg_key, n=3)

    top_ppg_text = "TOP PPG: " + ", ".join(
        f"{t} {ppg[t]:.1f}" for t in top_ppg
    )

    bottom_ppg_text = "BOTTOM PPG: " + ", ".join(
        f"{t} {ppg[t]:.1f}" for t in bottom_ppg
    )

    return (
        "NBA 2K10 SIM — LIVE  |  "
        + last_final
        + "  |  "
        + top_winpct_text
        + "  |  "
        + bottom_winpct_text
        + "  |  "
        + top_ppg_text
        + "  |  "
        + bottom_ppg_text
        + "  |  "
        + high_total_text
        + "  |  "
        + low_total_text
        + "     "
    )


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
                ticker = format_ticker(games)
                OUT_PATH.write_text(ticker, encoding="utf-8")
                print("[overlay] updated")
                last_size = size
        except Exception as e:
            print("[overlay] error:", repr(e))

        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
