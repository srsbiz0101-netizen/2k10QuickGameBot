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

OCR_REGION = {"top": 140, "left": 280, "width": 800, "height": 220}

CHECK_INTERVAL = 5
ENDSCREEN_CONFIRMATIONS = 3

INPUT_GAP = 0.30

# Menu timing (2a/2b need stick hold + confirm hold)
MENU_DIR_HOLD_BEFORE_CONFIRM = 0.80
MENU_CONFIRM_HOLD = 0.80
MENU_RELEASE_AFTER = 0.40

SETTLE_SHORT = 1.2
SETTLE_LONG = 2.5

GAME_LOCK_SECONDS = 120

# Quick Game cursor movement
FORCE_SIDE_HOLD = 0.95      # long hold to guarantee far-left/far-right
CENTER_STEP_TIME = 0.18     # short press = "one step" to center (tune 0.14–0.24 if needed)

# -----------------------------
# KEYBINDINGS
# -----------------------------

KEY_CONFIRM = "x"     # Cross
KEY_BACK = "c"        # Circle
KEY_START = "enter"   # Start/Enter

KEY_L2 = "r"
KEY_R2 = "t"

# LEFT STICK (WASD) — required for menus and Quick Game cursor here
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

def classify_screen(text: str):
    if any(k in text for k in ["GAMEREEL", "GMOMENTS", "PRESSBOOK", "GAMEWRAPUP"]):
        return "END_SCREEN"

    gameplay_signals = [
        "ARENA", "CENTER", "PARK", "ORACLE", "GARDEN", "STAPLES",
        "DEFENSE", "OFFENSE", "REBOUND", "FOUL", "SHOT",
        "ELLIS", "POR", "GS", "LAL", "BOS", "NYK", "MIA", "CHI"
    ]

    if any(k in text for k in gameplay_signals) or ":" in text:
        return "GAMEPLAY"

    return "UNKNOWN"

# -----------------------------
# INPUT HELPERS
# -----------------------------

def press_key(key, duration=0.6):
    keyboard.press(key)
    time.sleep(duration)
    keyboard.release(key)
    time.sleep(INPUT_GAP)

def press_and_hold_to_confirm(confirm_key, hold_keys):
    """
    NBA 2K10 menu behavior (2a/2b):
    Hold stick direction(s) -> wait -> hold X while directions held -> release X -> wait -> release directions
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

def stick_force(key, hold_time=FORCE_SIDE_HOLD, settle=0.50):
    """Long hold to guarantee reaching far-left/far-right."""
    keyboard.press(key)
    time.sleep(hold_time)
    keyboard.release(key)
    time.sleep(settle)

def stick_step(key, step_time=CENTER_STEP_TIME, settle=0.35):
    """Single crisp stick 'step' intended to move exactly one column."""
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
    time.sleep(0.45)

# -----------------------------
# ACTIONS
# -----------------------------

def action_open_postgame_menu():
    print("ACTION: End screen → Circle (C)")
    press_key(KEY_BACK, duration=0.7)

def action_postgame_a_quit():
    print("ACTION: Menu 2a → Quit (S + A held, X held)")
    press_and_hold_to_confirm(KEY_CONFIRM, [STICK_DOWN, STICK_LEFT])

def action_postgame_b_quick_game():
    print("ACTION: Menu 2b → Quick Game (S held, X held)")
    press_and_hold_to_confirm(KEY_CONFIRM, [STICK_DOWN])

def action_quickgame_setup_and_start():
    """
    Quick Game setup:
    1) Force LEFT -> randomize AWAY
    2) Force RIGHT -> randomize HOME
    3) One opposite step to CENTER (CPUvCPU)
    4) Start with Enter
    """
    print("ACTION: Quick Game → Randomize BOTH teams + center for CPUvCPU")

    time.sleep(1.2)

    # Force LEFT (Away)
    stick_force(STICK_LEFT)
    print(" - Randomizing AWAY (LEFT)")
    randomize_team()
    time.sleep(0.6)

    # Force RIGHT (Home)
    stick_force(STICK_RIGHT)
    print(" - Randomizing HOME (RIGHT)")
    randomize_team()
    time.sleep(0.6)

    # Return to CENTER: one opposite step from RIGHT -> CENTER
    print(" - Returning to CENTER (one LEFT step)")
    stick_step(STICK_LEFT, step_time=CENTER_STEP_TIME, settle=0.50)

    print("ACTION: Start Game from CENTER (CPUvCPU)")
    press_key(KEY_START, duration=0.9)

# -----------------------------
# MAIN LOOP
# -----------------------------

def main():
    global state, end_hits, game_lock_until

    print("NBA 2K10 CPUvCPU BOT RUNNING — Press ESC to stop")

    while True:
        if keyboard.is_pressed("esc"):
            print("ESC pressed — exiting")
            return

        now = time.time()

        if state == BotState.GAME_RUNNING:
            if now < game_lock_until:
                time.sleep(10)
                continue
            else:
                state = BotState.WAIT_FOR_END
                end_hits = 0

        if state == BotState.WAIT_FOR_END:
            text = ocr_text()
            screen = classify_screen(text)

            if screen == "GAMEPLAY":
                end_hits = 0
                time.sleep(CHECK_INTERVAL)
                continue

            if screen == "END_SCREEN":
                end_hits += 1
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

if __name__ == "__main__":
    main()
