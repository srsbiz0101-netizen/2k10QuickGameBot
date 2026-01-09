import csv
import time
from collections import defaultdict, deque
from pathlib import Path

CSV_PATH = Path("nba2k10_results.csv")
OUT_PATH = Path("overlay.txt")

RECENT_N = 10          # show last 10 games
REFRESH_SECONDS = 5    # update overlay every 5s

def read_games():
    if not CSV_PATH.exists():
        return []

    games = []
    with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            # Skip incomplete rows
            try:
                gnum = int(row["game_number"])
                t1 = (row["team1"] or "").strip()
                t2 = (row["team2"] or "").strip()
                s1 = int(row["score1"]) if row["score1"] not in (None, "", "None") else None
                s2 = int(row["score2"]) if row["score2"] not in (None, "", "None") else None
                ts = (row["timestamp"] or "").strip()
            except Exception:
                continue

            if not t1 or not t2 or s1 is None or s2 is None:
                continue

            games.append((gnum, ts, t1, s1, t2, s2))

    # sort by game_number
    games.sort(key=lambda x: x[0])
    return games

def compute_standings(games):
    wins = defaultdict(int)
    losses = defaultdict(int)

    for _, _, t1, s1, t2, s2 in games:
        if s1 > s2:
            wins[t1] += 1
            losses[t2] += 1
        elif s2 > s1:
            wins[t2] += 1
            losses[t1] += 1
        # ties unlikely; ignore

    return wins, losses

def format_overlay(games):
    wins, losses = compute_standings(games)

    # Top 3 by wins (ties broken by win% then name)
    teams = set(list(wins.keys()) + list(losses.keys()))
    def key_fn(t):
        w = wins[t]
        l = losses[t]
        gp = w + l
        wpct = (w / gp) if gp else 0
        return (w, wpct, t)

    top3 = sorted(teams, key=key_fn, reverse=True)[:3]

    # Recent games
    recent = deque(games, maxlen=RECENT_N)

    lines = []
    lines.append("NBA 2K10 SIM — LIVE")
    lines.append("")
    lines.append("TOP 3 WINS")
    for i, t in enumerate(top3, 1):
        w, l = wins[t], losses[t]
        lines.append(f"{i}. {t}: {w}-{l}")

    lines.append("")
    lines.append(f"RECENT {len(recent)} GAMES")
    for gnum, ts, t1, s1, t2, s2 in reversed(recent):
        winner = t1 if s1 > s2 else t2
        lines.append(f"#{gnum}  {t1} {s1} - {t2} {s2}  (W: {winner})")

    return "\n".join(lines)

def main():
    last_size = None

    while True:
        try:
            size = CSV_PATH.stat().st_size if CSV_PATH.exists() else 0
            # Only recompute when file changes
            if size != last_size:
                games = read_games()
                overlay = format_overlay(games)
                OUT_PATH.write_text(overlay, encoding="utf-8")
                print("[overlay] updated")
                last_size = size
        except Exception as e:
            print("[overlay] error:", repr(e))

        time.sleep(REFRESH_SECONDS)

if __name__ == "__main__":
    main()
