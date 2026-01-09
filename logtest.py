import time
import csv
import os
import re
import difflib
from enum import Enum
from datetime import datetime

import numpy as np
import cv2
import pytesseract
import mss
import keyboard

# -----------------------------
# CONFIG
# -----------------------------

# End-screen keyword OCR region (your working region)
OCR_REGION = {"top": 140, "left": 280, "width": 800, "height": 220}

# Box Score: TEAM / TOTAL table region (tuned from your screenshot on the prior machine)
# NOTE: If this machine has different scaling/window placement, this may need tuning.
BOX_SCORE_REGION = {"top": 60, "left": 420, "width": 520, "height": 120}
# Score sanity checks (5-minute quarters typical)
MIN_SCORE = 20        # minimum plausible points for a team
MIN_TOTAL = 50        # minimum plausible combined points
MAX_SCORE = 150       # hard ceiling guardrail
MAX_RECHECKS = 2      # extra OCR attempts if scores look wrong
RECHECK_DELAY = 0.6   # seconds between rechecks

# A slightly taller/wider fallback region to better capture both team rows.
# (Still focused on the top score table, not the player stats table.)
BOX_SCORE_REGION_FALLBACK = {
    "top": max(0, BOX_SCORE_REGION["top"] - 15),
    "left": max(0, BOX_SCORE_REGION["left"] - 60),
    "width": BOX_SCORE_REGION["width"] + 140,
    "height": BOX_SCORE_REGION["height"] + 80,
}


CHECK_INTERVAL = 5
ENDSCREEN_CONFIRMATIONS = 3
INPUT_GAP = 0.30

# Menu timing: hold direction(s) -> wait -> press X while held -> release X -> release direction(s)
MENU_DIR_HOLD_BEFORE_CONFIRM = 0.60
MENU_CONFIRM_HOLD = 0.55
MENU_RELEASE_AFTER = 0.25

SETTLE_SHORT = 1.0
SETTLE_LONG = 2.0

# Prevent spamming mid-game
GAME_LOCK_SECONDS = 120

# Quick Game cursor movement
FORCE_SIDE_HOLD = 0.95
CENTER_STEP_TIME = 0.18  # tune 0.14–0.24 if center step isn't perfect

# Logging
LOG_CSV = "nba2k10_results.csv"

# -----------------------------
# KEYBINDINGS (your RPCS3 mapping)
# -----------------------------

KEY_CONFIRM = "x"     # Cross
KEY_BACK = "c"        # Circle
KEY_START = "enter"   # Start/Enter

KEY_L2 = "r"
KEY_R2 = "t"

# Left Stick = WASD
STICK_UP = "w"
STICK_DOWN = "s"
STICK_LEFT = "a"
STICK_RIGHT = "d"

# -----------------------------
# STATE MACHINE
# -----------------------------

class BotState(Enum):
    WAIT_FOR_END = 1
    OPEN_POSTGAME_MENU = 2
    POSTGAME_A = 3            # Menu 2A hub
    POSTGAME_STATS = 4        # Menu 2A -> Game Stats -> Box Score -> OCR/log -> back
    POSTGAME_B = 5            # Menu 2B
    QUICKGAME_SETUP = 6
    GAME_RUNNING = 7

state = BotState.WAIT_FOR_END
end_hits = 0
game_lock_until = 0.0

# Per-game flags/counters
stats_logged_this_game = False
games_played = 0

sct = mss.mss()

# -----------------------------
# OCR / CAPTURE
# -----------------------------

def grab_region(region):
    return np.array(sct.grab(region))

def ocr_normalized(img_bgra: np.ndarray) -> str:
    """OCR tuned for menu keyword detection (spaces removed)."""
    gray = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2GRAY)
    gray = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)[1]
    txt = pytesseract.image_to_string(gray).upper()
    return txt.replace(" ", "").strip()

def ocr_score_text(img_bgra: np.ndarray) -> str:
    """OCR tuned for scoreboard/table reading (box score TEAM/TOTAL)."""
    gray = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2GRAY)
    gray = cv2.threshold(gray, 185, 255, cv2.THRESH_BINARY)[1]

    # upscale to help small text
    gray = cv2.resize(gray, None, fx=2.2, fy=2.2, interpolation=cv2.INTER_CUBIC)

    config = (
        "--oem 3 --psm 6 "
        "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
    )
    txt = pytesseract.image_to_string(gray, config=config).upper()
    txt = txt.replace("\r", "")

    # small cleanup for common misses
    txt = txt.replace("OTAL", "TOTAL")  # missing 'T'
    return txt.strip()


def ocr_score_strip(img_bgra: np.ndarray) -> str:
    """OCR tuned for the small top score table (TEAM/quarters/TOTAL)."""
    gray = cv2.cvtColor(img_bgra, cv2.COLOR_BGRA2GRAY)
    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    gray = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)[1]

    config = "--oem 3 --psm 6"
    txt = pytesseract.image_to_string(gray, config=config).upper()
    txt = txt.replace("\r", "")

    # common OCR drops
    txt = txt.replace("OTAL", "TOTAL")

    # normalize whitespace BUT KEEP LINE BREAKS
    txt = "\n".join(re.sub(r"\s+", " ", ln).strip() for ln in txt.splitlines() if ln.strip())

    return txt.strip()



def scores_plausible(s1, s2) -> bool:
    if s1 is None or s2 is None:
        return False
    if not (0 <= s1 <= MAX_SCORE and 0 <= s2 <= MAX_SCORE):
        return False
    if s1 < MIN_SCORE or s2 < MIN_SCORE:
        return False
    if (s1 + s2) < MIN_TOTAL:
        return False
    return True

"""
NOTE: score parsing is done by locating the two TEAM rows and taking the LAST number on each row as TOTAL.
This avoids accidentally grabbing player stats numbers.
"""

def classify_screen(text_no_spaces: str):
    # End-of-game indicators (spaces removed)
    if any(k in text_no_spaces for k in ["GAMEREEL", "GMOMENTS", "PRESSBOOK", "GAMEWRAPUP", "WRAPUP", "GAMESTATS"]):
        return "END_SCREEN"

    # Gameplay-ish indicators (helps avoid false triggers)
    gameplay_signals = [
        "ARENA", "CENTER", "PARK", "ORACLE", "GARDEN", "STAPLES",
        "DEFENSE", "OFFENSE", "REBOUND", "FOUL", "SHOT"
    ]
    if any(k in text_no_spaces for k in gameplay_signals) or ":" in text_no_spaces:
        return "GAMEPLAY"

    return "UNKNOWN"

# -----------------------------
# LOGGING + TEAM NORMALIZATION
# -----------------------------

KNOWN_TEAMS = [
    "76ERS","CAVALIERS","BULLS","CELTICS","LAKERS","CLIPPERS","KINGS","KNICKS",
    "HEAT","MAGIC","MAVERICKS","NUGGETS","PACERS","PISTONS","RAPTORS","ROCKETS",
    "SPURS","SUNS","THUNDER","TIMBERWOLVES","TRAILBLAZERS","WARRIORS","WIZARDS",
    "HAWKS","HORNETS","JAZZ","NETS","BUCKS","GRIZZLIES","PELICANS","SIXERS",
    "EAST ALL-STARS","WEST ALL-STARS","EAST ALLSTARS","WEST ALLSTARS",
    "BOBCATS",
]

def normalize_team_name(raw_team: str):
    if not raw_team:
        return None

    t = raw_team.upper()

    # Keep only letters/numbers/spaces
    t = re.sub(r"[^A-Z0-9 ]", "", t)
    t = re.sub(r"\s+", " ", t).strip()

    # Drop single-letter trailing tokens that OCR often invents (e.g., "... Z")
    parts = t.split()
    if len(parts) >= 2 and len(parts[-1]) == 1:
        parts = parts[:-1]
    t = " ".join(parts)

    # Common digit->letter OCR fixes (team names are letters)
    # 1/I -> H is common in your logs (HAWKS -> 1AVYKS)
    # 0 -> O, 5 -> S, 8 -> B can help too.
    trans = str.maketrans({
        "0": "O",
        "1": "H",
        "5": "S",
        "8": "B",
    })
    t = t.translate(trans)

    # Your previous quick fixes
    t = t.replace("6ERS", "76ERS")
    t = t.replace("AVALIERS", "CAVALIERS")
    t = t.replace("CAVALIER", "CAVALIERS")
    t = t.replace("SOBCATS", "BOBCATS")
    t = t.replace("VIZARDS", "WIZARDS")
    t = t.replace("SRIZZLIES", "GRIZZLIES")

    # All-Star variants
    t = t.replace("ALLSTARS", "ALL-STARS").replace("ALL STARS", "ALL-STARS")

    # Extra: common Hawks OCR weirdness
    t = t.replace("HAVYKS", "HAWKS")
    t = t.replace("HA VKS", "HAWKS").replace("HAWK S", "HAWKS")

    # Exact known-team hit
    if t in KNOWN_TEAMS:
        return t

    # Fuzzy match to closest known team
    match = difflib.get_close_matches(t, KNOWN_TEAMS, n=1, cutoff=0.55)
    return match[0] if match else t

def ensure_csv_header():
    if not os.path.exists(LOG_CSV):
        with open(LOG_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["game_number", "timestamp", "team1", "score1", "team2", "score2", "raw_boxscore_ocr"])

def load_games_played_from_csv():
    if not os.path.exists(LOG_CSV):
        return 0
    try:
        with open(LOG_CSV, "r", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if len(rows) <= 1:
            return 0
        return int(rows[-1][0])
    except:
        return 0
def normalize_team(name: str):
    if not name:
        return None

    name = name.upper()
    name = re.sub(r"[^A-Z0-9 ]", "", name)
    name = re.sub(r"\s+", " ", name).strip()

    # Common OCR confusions
    name = name.replace("0", "O").replace("1", "I").replace("5", "S")

    TEAM_MAP = {
        "SIXERS": "76ERS",
        "76ERS": "76ERS",
        "SEVENTYSIXERS": "76ERS",

        "BOBCAT": "BOBCATS",
        "BOBCATS": "BOBCATS",

        "WARRIOR": "WARRIORS",
        "WARRIORS": "WARRIORS",

        "SPUR": "SPURS",
        "SPURS": "SPURS",

        "CAV": "CAVALIERS",
        "CAVS": "CAVALIERS",
        "CAVALIERS": "CAVALIERS",

        "BLAZER": "BLAZERS",
        "BLAZERS": "BLAZERS",
        "TRAILBLAZERS": "BLAZERS",

        "WIZ": "WIZARDS",
        "WIZARDS": "WIZARDS",

        "GRIZZ": "GRIZZLIES",
        "GRIZZLIES": "GRIZZLIES",

        "TIMBER": "TIMBERWOLVES",
        "WOLVES": "TIMBERWOLVES",
        "TIMBERWOLVES": "TIMBERWOLVES",

        "KNICK": "KNICKS",
        "KNICKS": "KNICKS",

        "HAWK": "HAWKS",
        "HAWKS": "HAWKS",

        "BUCK": "BUCKS",
        "BUCKS": "BUCKS",

        "KING": "KINGS",
        "KINGS": "KINGS",
    }

    # Exact map
    if name in TEAM_MAP:
        return TEAM_MAP[name]

    # If your file already has KNOWN_TEAMS, use it when available
    try:
        if "KNOWN_TEAMS" in globals():
            if name in KNOWN_TEAMS:
                return name
            # fuzzy match
            m = difflib.get_close_matches(name, KNOWN_TEAMS, n=1, cutoff=0.60)
            if m:
                return m[0]
    except Exception:
        pass

    # Partial map (safe)
    for k, v in TEAM_MAP.items():
        if k in name:
            return v

    return name

# -----------------------------
# FIX 2: STRICT PARSING (reject headers/garbage)
# -----------------------------

def parse_boxscore(raw: str):
    """
    Stricter parser with TOTAL auto-correction:
    - Reject header-like lines containing TEAM/1ST/2ND/3RD/4TH/OT/TOTAL
    - Require >=2 numbers on a row (quarters + total)
    - TOTAL is last number, but if OCR messes it up (often 10->0), compute TOTAL from quarters
    - Normalize team name
    - Pick best two candidates (prefer known teams)
    """
    lines = [ln.strip() for ln in raw.upper().split("\n") if ln.strip()]
    results = []

    for ln in lines:
        clean = re.sub(r"[^A-Z0-9 ]", " ", ln)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            continue

        # HARD FILTER: skip header-like rows
        header_words = {"TEAM", "1ST", "2ND", "3RD", "4TH", "OT", "TOTAL"}
        tokens = set(clean.split())
        if tokens & header_words:
            continue

        nums = [int(n) for n in re.findall(r"\b\d{1,3}\b", clean)]
        if len(nums) < 2:
            # Need quarters + total at minimum
            continue

        # OCR total (last number) and computed total (sum of prior numbers)
        ocr_total = nums[-1]
        qsum = sum(nums[:-1])

        # --- TOTAL FIX ---
        # If OCR total looks wrong, trust the quarter sum.
        # This specifically fixes 10/20/30/... being read as 0, but also other big mismatches.
        total = ocr_total
        if qsum > 0:
            if ocr_total == 0 and qsum >= 10:
                total = qsum
            elif abs(ocr_total - qsum) >= 2:
                # If it's not close, OCR probably missed a digit.
                total = qsum

        team_part = re.sub(r"\b\d{1,3}\b", " ", clean)
        team_part = re.sub(r"\s+", " ", team_part).strip()
        team_norm = normalize_team_name(team_part)

        if not team_norm or len(team_norm) < 4:
            continue

        results.append((team_norm, total))

    if len(results) < 2:
        return None, None, None, None

    def score_candidate(item):
        team, total = item
        exact = 1 if team in KNOWN_TEAMS else 0
        return (exact, len(team), total)

    results.sort(key=score_candidate, reverse=True)
    (team1, score1), (team2, score2) = results[0], results[1]
    return team1, score1, team2, score2



def parse_totals_by_team_lines(text: str):
    """
    Find the two team rows and take the LAST number in each row as TOTAL.
    Skips header rows like: TEAM 1ST 2ND 3RD 4TH OT TOTAL
    """
    if not text:
        return None, None, None, None

    header_words = {"TEAM", "1ST", "2ND", "3RD", "4TH", "OT", "TOTAL"}

    lines = []
    for ln in text.upper().splitlines():
        ln = re.sub(r"[^A-Z0-9 ]", " ", ln)
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            lines.append(ln)

    candidates = []
    for ln in lines:
        tokens = set(ln.split())
        if tokens & header_words:
            # skip header-like lines
            continue

        nums = [int(x) for x in re.findall(r"\b\d+\b", ln)]
        if len(nums) < 2:
            continue

        # team text = line with numbers removed
        team_part = re.sub(r"\b\d+\b", " ", ln)
        team_part = re.sub(r"\s+", " ", team_part).strip()

        team = normalize_team_name(team_part)  # <-- IMPORTANT: use the better normalizer
        total = nums[-1]

        if team and 0 <= total <= MAX_SCORE:
            candidates.append((team, total))

    # de-dupe teams, keep last seen
    dedup = {}
    for team, total in candidates:
        dedup[team] = total

    if len(dedup) < 2:
        return None, None, None, None

    (t1, s1), (t2, s2) = list(dedup.items())[-2:]
    return t1, s1, t2, s2


def scores_plausible(s1, s2) -> bool:
    if s1 is None or s2 is None:
        return False
    if s1 < MIN_SCORE or s2 < MIN_SCORE:
        return False
    if (s1 + s2) < MIN_TOTAL:
        return False
    if s1 > MAX_SCORE or s2 > MAX_SCORE:
        return False
    return True


def scores_plausible(s1, s2) -> bool:
    if s1 is None or s2 is None:
        return False
    if s1 < MIN_SCORE or s2 < MIN_SCORE:
        return False
    if (s1 + s2) < MIN_TOTAL:
        return False
    if s1 > MAX_SCORE or s2 > MAX_SCORE:
        return False
    return True

def log_box_score(game_number: int):
    """Log final score to CSV with safe re-checks.

    Primary: OCR the top score table region (BOX_SCORE_REGION) and parse totals
    by team lines. If that fails, try a slightly larger region (fallback).
    If scores still look implausible, retry OCR a few times before logging.
    """
    ensure_csv_header()

    best = (None, None, None, None, "")
    raw_combined = ""

    for attempt in range(MAX_RECHECKS + 1):
        # 1) Primary strip
        img = grab_region(BOX_SCORE_REGION)
        raw = ocr_score_strip(img)
        t1, s1, t2, s2 = parse_totals_by_team_lines(raw)

        # 2) Fallback strip (taller/wider)
        if t1 is None or s1 is None or t2 is None or s2 is None:
            img2 = grab_region(BOX_SCORE_REGION_FALLBACK)
            raw2 = ocr_score_strip(img2)
            t1, s1, t2, s2 = parse_totals_by_team_lines(raw2)
            raw = raw + " || FALLBACK_STRIP || " + raw2

        raw_combined = raw

        # Keep the last non-empty parse attempt as best
        if t1 and t2 and s1 is not None and s2 is not None:
            best = (t1, s1, t2, s2, raw_combined)

        # If plausible, stop early
        if scores_plausible(s1, s2):
            break

        print(
            f"[WARN] Implausible/missing score ({t1} {s1} - {t2} {s2}). "
            f"Rechecking OCR ({attempt+1}/{MAX_RECHECKS})..."
        )
        time.sleep(RECHECK_DELAY)

    team1, score1, team2, score2, raw_final = best
    if raw_final == "":
        raw_final = raw_combined

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([game_number, ts, team1, score1, team2, score2, raw_final.replace("\n", " ")])

    print(f"[LOG] Game #{game_number}: {team1} {score1} - {team2} {score2}")
    print(f"[LOG RAW] {raw_final}")


# -----------------------------
# INPUT HELPERS
# -----------------------------

def press_key(key, duration=0.55):
    keyboard.press(key)
    time.sleep(duration)
    keyboard.release(key)
    time.sleep(INPUT_GAP)

def press_and_hold_to_confirm(confirm_key, hold_keys):
    """
    Hold direction key(s) -> wait -> press X while held -> release X -> release direction(s)
    """
    for k in hold_keys:
        keyboard.press(k)

    time.sleep(MENU_DIR_HOLD_BEFORE_CONFIRM)

    keyboard.press(confirm_key)
    time.sleep(MENU_CONFIRM_HOLD)
    keyboard.release(confirm_key)

    time.sleep(MENU_RELEASE_AFTER)

    for k in hold_keys:
        keyboard.release(k)

    time.sleep(INPUT_GAP)

def stick_force(key, hold_time=FORCE_SIDE_HOLD, settle=0.45):
    keyboard.press(key)
    time.sleep(hold_time)
    keyboard.release(key)
    time.sleep(settle)

def stick_step(key, step_time=CENTER_STEP_TIME, settle=0.65):
    keyboard.press(key)
    time.sleep(step_time)
    keyboard.release(key)
    time.sleep(settle)

def randomize_team():
    keyboard.press(KEY_L2)
    keyboard.press(KEY_R2)
    time.sleep(0.55)
    keyboard.release(KEY_L2)
    keyboard.release(KEY_R2)
    time.sleep(0.40)

# -----------------------------
# ACTIONS
# -----------------------------

def action_open_postgame_menu():
    print("ACTION: End screen → open Menu 2A with C")
    press_key(KEY_BACK, duration=0.65)

def action_menu2a_open_gamestats():
    print("ACTION: Menu 2A → Game Stats (hold S + X)")
    press_and_hold_to_confirm(KEY_CONFIRM, [STICK_DOWN])

def action_statsmenu_open_boxscore():
    print("ACTION: Stats Menu → Box Score (hold W + X)")
    press_and_hold_to_confirm(KEY_CONFIRM, [STICK_UP])

def action_back_one_screen():
    press_key(KEY_BACK, duration=0.45)

def action_menu2a_quit():
    print("ACTION: Menu 2A → Quit (hold S + A + X)")
    press_and_hold_to_confirm(KEY_CONFIRM, [STICK_DOWN, STICK_LEFT])

def action_menu2b_quickgame():
    print("ACTION: Menu 2B → Quick Game (hold S + X)")
    press_and_hold_to_confirm(KEY_CONFIRM, [STICK_DOWN])

def action_quickgame_setup_and_start():
    """
    Quick Game setup:
    1) Force LEFT -> randomize AWAY
    2) Force RIGHT -> randomize HOME
    3) One LEFT step -> CENTER (CPUvCPU)
    4) Start with Enter
    """
    print("ACTION: Quick Game → Randomize both + center + start (CPUvCPU)")
    time.sleep(1.2)

    stick_force(STICK_LEFT)
    print(" - Randomizing AWAY (LEFT)")
    randomize_team()
    time.sleep(0.55)

    stick_force(STICK_RIGHT)
    print(" - Randomizing HOME (RIGHT)")
    randomize_team()
    time.sleep(0.55)

    print(" - Returning to CENTER (one LEFT step)")
    stick_step(STICK_LEFT, step_time=CENTER_STEP_TIME, settle=0.75)

    print(" - Starting game (Enter)")
    press_key(KEY_START, duration=0.85)

# -----------------------------
# MAIN LOOP
# -----------------------------

def main():
    global state, end_hits, game_lock_until, stats_logged_this_game, games_played

    ensure_csv_header()
    games_played = load_games_played_from_csv()
    print(f"Loaded games played: {games_played}")
    print("BOT RUNNING — Press ESC to stop (may require admin).")

    while True:
        if keyboard.is_pressed("esc"):
            print("ESC pressed — exiting")
            return

        now = time.time()

        # Lockout during gameplay so we don't react to random OCR noise mid-game
        if state == BotState.GAME_RUNNING:
            if now < game_lock_until:
                time.sleep(10)
                continue
            else:
                state = BotState.WAIT_FOR_END
                end_hits = 0

        if state == BotState.WAIT_FOR_END:
            img = grab_region(OCR_REGION)
            text = ocr_normalized(img)
            print("OCR:", text)

            screen = classify_screen(text)

            if screen == "GAMEPLAY":
                end_hits = 0
                time.sleep(CHECK_INTERVAL)
                continue

            if screen == "END_SCREEN":
                end_hits += 1
                print(f"End screen hit {end_hits}/{ENDSCREEN_CONFIRMATIONS}")
                if end_hits >= ENDSCREEN_CONFIRMATIONS:
                    end_hits = 0
                    stats_logged_this_game = False
                    state = BotState.OPEN_POSTGAME_MENU
            else:
                end_hits = 0

            time.sleep(CHECK_INTERVAL)

        elif state == BotState.OPEN_POSTGAME_MENU:
            action_open_postgame_menu()
            time.sleep(SETTLE_SHORT)
            state = BotState.POSTGAME_A

        elif state == BotState.POSTGAME_A:
            if not stats_logged_this_game:
                state = BotState.POSTGAME_STATS
            else:
                action_menu2a_quit()
                time.sleep(SETTLE_LONG)
                state = BotState.POSTGAME_B

        elif state == BotState.POSTGAME_STATS:
            action_menu2a_open_gamestats()
            time.sleep(SETTLE_SHORT)

            action_statsmenu_open_boxscore()
            time.sleep(SETTLE_SHORT)

            games_played += 1
            try:
                log_box_score(games_played)
            except Exception as e:
                print("[LOG] Failed to log box score:", repr(e))

            print("ACTION: Back out to Menu 2A (C once)")
            action_back_one_screen()
            time.sleep(SETTLE_SHORT)

            stats_logged_this_game = True
            state = BotState.POSTGAME_A

        elif state == BotState.POSTGAME_B:
            action_menu2b_quickgame()
            time.sleep(SETTLE_LONG)
            state = BotState.QUICKGAME_SETUP

        elif state == BotState.QUICKGAME_SETUP:
            action_quickgame_setup_and_start()
            game_lock_until = time.time() + GAME_LOCK_SECONDS
            state = BotState.GAME_RUNNING

if __name__ == "__main__":
    main()
