import time
import numpy as np
import cv2
import pytesseract
import mss
import keyboard
from enum import Enum

# -----------------------------
# CONFIG
# -----------------------------

# OCR region for 1360x768 (tweak if needed)
OCR_REGION = {"top": 140, "left": 280, "width": 800, "height": 220}

# OCR behavior
CHECK_INTERVAL = 5                 # seconds between OCR reads while waiting
ENDSCREEN_CONFIRMATIONS = 3        # require N consecutive hits before acting

# Input timing (press-style)
INPUT_GAP = 0.30                   # delay after each input/action
PRESS_DURATION = 0.35              # default press length for most keys
DIR_PRESS_DURATION = 0.30          # direction press length for left/right nudges

# Direction+Confirm behavior
PRE_HOLD = 0.35                    # hold direction(s) before confirming
POST_HOLD = 0.35                   # keep holding direction(s) after confirming

# Menu settle delays
SETTLE_SHORT = 1.0
SETTLE_MED = 1.5
SETTLE_LONG = 2.0

# Lockout after starting a game (prevents repeats while loading)
GAME_LOCK_SECONDS = 90

# -----------------------------
# KEYBINDINGS (from your RPCS3 screenshot)
# -----------------------------

KEY_CONFIRM = "x"       # Cross
KEY_BACK = "c"          # Circle
KEY_START = "enter"     # Start/Return
KEY_L2 = "r"            # L2
KEY_R2 = "t"            # R2

# D-pad uses arrow keys (as mapped in your screenshot)
KEY_UP = "up"
KEY_DOWN = "down"
KEY_LEFT = "left"
KEY_RIGHT = "right"

# -----------------------------
# STATE MACHINE
# -----------------------------

class BotState(Enum):
    WAIT_FOR_END = 1
    OPEN_POSTGAME_MENU = 2
    POSTGAME_A = 3
    POSTGAME_B = 4
    QUICKGAME_SETUP = 5
    GAME_RUNNING = 6

state = BotState.WAIT_FOR_END
end_hits = 0
game_lock_until = 0.0

sct = mss.mss()

# -----------------------------
# OCR
# -----------------------------

def ocr_text():
    img = np.array(sct.grab(OCR_REGION))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)[1]
    text = pytesseract.image_to_string(gray).upper().replace(" ", "")
    print("OCR:", text)
    return text

def classify_screen(text: str) -> str:
    """
    Returns one of:
      - "END_SCREEN"
      - "GAMEPLAY"
      - "UNKNOWN"
    """
    t = text.replace(" ", "")

    # End-of-game indicators (from your end screen)
    if any(k in t for k in ["GAMEREEL", "GMOMENTS", "PRESSBOOK", "GAMEWRAPUP"]):
        return "END_SCREEN"

    # Strong gameplay indicators (covers cutaways when clock/score isn't visible)
    gameplay_signals = [
        "ARENA", "CENTER", "PARK", "ORACLE", "GARDEN", "STAPLES",
        "DEFENSE", "OFFENSE", "REBOUND", "FOUL", "SHOT",
        "ELLIS",  # player name seen in your screenshot (ok to keep)
        "POR", "GS", "LAL", "BOS", "NYK", "MIA", "CHI"
    ]

    if any(sig in t for sig in gameplay_signals):
        return "GAMEPLAY"

    # Fallback: clock contains ":"
    if ":" in t:
        return "GAMEPLAY"

    return "UNKNOWN"

# -----------------------------
# INPUT HELPERS (PRESS-STYLE)
# -----------------------------

def press_key(key: str, duration: float = PRESS_DURATION):
    keyboard.press(key)
    time.sleep(duration)
    keyboard.release(key)
    time.sleep(INPUT_GAP)

def press_keys_together(keys: list[str], duration: float = PRESS_DURATION):
    for k in keys:
        keyboard.press(k)
    time.sleep(duration)
    for k in keys:
        keyboard.release(k)
    time.sleep(INPUT_GAP)

def press_direction(key: str, duration: float = DIR_PRESS_DURATION):
    keyboard.press(key)
    time.sleep(duration)
    keyboard.release(key)
    time.sleep(INPUT_GAP)

def press_while_holding(confirm_key: str,
                        hold_keys: list[str],
                        pre_hold: float = PRE_HOLD,
                        confirm_duration: float = PRESS_DURATION,
                        post_hold: float = POST_HOLD):
    """
    Hold directions, press confirm, keep holding, then release.
    This is critical for menus 2a / 2b (and any directional confirm).
    """
    for k in hold_keys:
        keyboard.press(k)

    time.sleep(pre_hold)

    keyboard.press(confirm_key)
    time.sleep(confirm_duration)
    keyboard.release(confirm_key)

    time.sleep(post_hold)

    for k in hold_keys:
        keyboard.release(k)

    time.sleep(INPUT_GAP)

def press_many_direction(key: str, n: int, duration: float = DIR_PRESS_DURATION):
    for _ in range(n):
        press_direction(key, duration=duration)

def randomize_team_under_cursor():
    # Hold L2+R2 together (R+T) twice for reliability
    press_keys_together([KEY_L2, KEY_R2], duration=0.35)
    press_keys_together([KEY_L2, KEY_R2], duration=0.35)

# -----------------------------
# ACTIONS (based on your screenshots)
# -----------------------------

def action_open_postgame_menu():
    # Screenshot 1: Circle (C) opens menu 2a
    print("ACTION: End screen -> press C (Circle) to open menu 2a")
    press_key(KEY_BACK, duration=0.45)

def action_postgame_a_quit():
    # Screenshot 2a: hold Down+Left, press X while held
    print("ACTION: Menu 2a -> QUIT (hold Down+Left, press X)")
    press_while_holding(KEY_CONFIRM, [KEY_DOWN, KEY_LEFT])

def action_postgame_b_quick_game():
    # Screenshot 2b: hold Down, press X while held
    print("ACTION: Menu 2b -> QUICK GAME (hold Down, press X)")
    press_while_holding(KEY_CONFIRM, [KEY_DOWN])

def action_quickgame_setup_and_start():
    """
    Screenshot 3 rules:
    - Force cursor LEFT by pressing LEFT twice (cursor not looped)
    - Randomize AWAY on LEFT (hold R+T)
    - Move to RIGHT by pressing RIGHT twice
    - Randomize HOME on RIGHT (hold R+T)
    - Return to CENTER by pressing LEFT once
    - Start game ONLY from CENTER using Start/Enter
    """
    print("ACTION: Quick Game setup -> randomize both teams and start CPUvCPU")

    time.sleep(SETTLE_MED)

    # Force cursor LEFT (safe regardless of current cursor position)
    press_many_direction(KEY_LEFT, 2, duration=0.35)
    time.sleep(0.4)

    # Randomize AWAY
    print(" - Randomizing AWAY (LEFT)")
    randomize_team_under_cursor()
    time.sleep(0.5)

    # Move to RIGHT from LEFT
    press_many_direction(KEY_RIGHT, 2, duration=0.35)
    time.sleep(0.4)

    # Randomize HOME
    print(" - Randomizing HOME (RIGHT)")
    randomize_team_under_cursor()
    time.sleep(0.5)

    # Return to CENTER (one left from right)
    press_direction(KEY_LEFT, duration=0.35)
    time.sleep(0.5)

    # Start game from CENTER
    print(" - Starting game from CENTER (Start/Enter)")
    time.sleep(2.0)  # settle before start
    press_key(KEY_START, duration=0.55)

# -----------------------------
# MAIN LOOP
# -----------------------------

def main():
    global state, end_hits, game_lock_until

    print("NBA 2K10 CPUvCPU Loop Bot running (keyboard press-style + OCR + state machine).")
    print("Press ESC at any time to stop.")

    while True:
        # Emergency stop
        if keyboard.is_pressed("esc"):
            print("ESC pressed — stopping bot.")
            return

        now = time.time()

        # Hard lock while loading / early game to prevent repeats
        if state == BotState.GAME_RUNNING:
            if now < game_lock_until:
                print("STATE: GAME_RUNNING (locked)...")
                time.sleep(10)
                continue
            else:
                print("STATE: GAME_RUNNING lock expired -> WAIT_FOR_END")
                state = BotState.WAIT_FOR_END
                end_hits = 0

        if state == BotState.WAIT_FOR_END:
            text = ocr_text()
            screen_type = classify_screen(text)

            if screen_type == "GAMEPLAY":
                end_hits = 0
                print("Gameplay detected. Ignoring.")
                time.sleep(CHECK_INTERVAL)
                continue

            if screen_type == "END_SCREEN":
                end_hits += 1
                print(f"End screen hit {end_hits}/{ENDSCREEN_CONFIRMATIONS}")
                if end_hits >= ENDSCREEN_CONFIRMATIONS:
                    end_hits = 0
                    state = BotState.OPEN_POSTGAME_MENU
            else:
                end_hits = 0

            time.sleep(CHECK_INTERVAL)

        elif state == BotState.OPEN_POSTGAME_MENU:
            action_open_postgame_menu()
            time.sleep(SETTLE_SHORT)
            state = BotState.POSTGAME_A

        elif state == BotState.POSTGAME_A:
            action_postgame_a_quit()
            time.sleep(SETTLE_LONG)
            state = BotState.POSTGAME_B

        elif state == BotState.POSTGAME_B:
            action_postgame_b_quick_game()
            time.sleep(SETTLE_LONG)
            state = BotState.QUICKGAME_SETUP

        elif state == BotState.QUICKGAME_SETUP:
            action_quickgame_setup_and_start()
            game_lock_until = time.time() + GAME_LOCK_SECONDS
            state = BotState.GAME_RUNNING
            print(f"STATE: GAME_RUNNING (lock for {GAME_LOCK_SECONDS}s)")

        else:
            # Safety fallback
            state = BotState.WAIT_FOR_END
            time.sleep(2)

if __name__ == "__main__":
    main()
