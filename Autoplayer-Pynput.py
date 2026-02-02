# Autoplayer-Pynput (high‑priority, micro‑optimised) – 2025‑05‑21
# --------------------------------------------------------------------
#  Goals (unchanged):
#    • Independent tracker per lane (no grouping)
#    • Selectable pixel‑sampling backend (MSS threads or Win32GUI procs)
#    • Works with Roblox & pynput only (input libraries fixed)
#    • Micro‑freeze and overflow issues resolved
#    • **Run at HIGH priority** (below REALTIME) rather than default
# --------------------------------------------------------------------

import time
from multiprocessing import Process, RawValue
from pynput.keyboard import Controller, Listener, Key
import win32gui
import psutil
import sys

# --------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------
TARGET_COLOR = (255, 255, 255)
TARGETS = [
    {"x": 731,  "key": "e"},
    {"x": 881,  "key": "r"},
    {"x": 1031, "key": "t"},
    {"x": 1181, "key": "y"}
]

# Hit zone configuration
HIT_ZONE_Y = 880  # Y position where notes should be pressed

BRIGHTNESS_THRESH  = 240  # >= treated as white
TOLERANCE          = 120  # per-channel RGB delta vs TARGET_COLOR
BRIGHTNESS_THRESH3 = BRIGHTNESS_THRESH * 3  # precomputed for is_white
WHITE_MIN          = 255 - TOLERANCE  # lower bound for near-white channels
MIN_HOLD_SEC = 0.02  # minimum key down time to avoid same-tick release
RELEASE_DEBOUNCE_SEC = 0.012  # require sustained non-white before release
FOCUS_POLL_SEC = 0.05  # throttle foreground window checks
NOT_FOCUSED_SLEEP_SEC = 0.1  # reduce CPU when app not focused

# Shared run flag for multiprocess mode (no lock to avoid per-iteration IPC overhead).
RUNNING = RawValue('b', True)

# --------------------------------------------------------------------
# Priority helpers
# --------------------------------------------------------------------

def set_high_priority(proc: psutil.Process | None = None):
    """Raise process priority to HIGH without hitting REALTIME."""
    try:
        p = proc or psutil.Process()
        if sys.platform == 'win32':
            p.nice(psutil.HIGH_PRIORITY_CLASS)  # BELOW_REALTIME on Windows
        else:
            p.nice(-10)  # "high" on Unix – lower is higher priority
    except Exception as exc:
        print(f"[INFO] Priority elevate failed: {exc}")

# --------------------------------------------------------------------
# Utility functions
# --------------------------------------------------------------------

def find_roblox_hwnd() -> int | None:
    """Best-effort lookup of the Roblox window handle to avoid title string checks."""
    try:
        matches: list[int] = []

        def _enum_cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if "Roblox" in title:
                matches.append(hwnd)

        win32gui.EnumWindows(_enum_cb, None)
        return matches[0] if matches else None
    except Exception:
        return None


def is_roblox_focused(roblox_hwnd: int | None) -> bool:
    fg = win32gui.GetForegroundWindow()
    if roblox_hwnd:
        return fg == roblox_hwnd
    return "Roblox" in win32gui.GetWindowText(fg)


def is_white(r: int, g: int, b: int) -> bool:
    """Optimized white detection with precomputed threshold."""
    if r + g + b > BRIGHTNESS_THRESH3:
        return True
    return (r >= WHITE_MIN and g >= WHITE_MIN and b >= WHITE_MIN)

# --------------------------------------------------------------------
# Lane monitors – Processes (Win32GUI)
# --------------------------------------------------------------------

def monitor_lane_process(target, run_flag, roblox_hwnd: int | None):
    set_high_priority()  # elevate child
    kb = Controller()
    key_pressed = False
    x, key = target['x'], target['key']
    y = HIT_ZONE_Y  # cache locally
    last_white_time = 0.0
    press_time = 0.0
    focused = True
    next_focus_check = 0.0
    
    # Cache HDC for the lifetime of this process
    hdc = win32gui.GetDC(0)
    try:
        while run_flag.value:
            now = time.perf_counter()
            if now >= next_focus_check:
                focused = is_roblox_focused(roblox_hwnd)
                next_focus_check = now + FOCUS_POLL_SEC
                if not focused and key_pressed:
                    kb.release(key)
                    key_pressed = False
                    last_white_time = 0.0
                    press_time = 0.0
            if not focused:
                time.sleep(NOT_FOCUSED_SLEEP_SEC)
                continue

            # Inline pixel read with cached HDC
            color = win32gui.GetPixel(hdc, x, y)
            if color == -1:
                r = g = b = 0
            else:
                r, g, b = color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF
            is_white_now = is_white(r, g, b)
            
            if is_white_now:
                last_white_time = now
                if not key_pressed:
                    kb.press(key)
                    key_pressed = True
                    press_time = now
            elif key_pressed:
                if (now - last_white_time >= RELEASE_DEBOUNCE_SEC and
                        now - press_time >= MIN_HOLD_SEC):
                    kb.release(key)
                    key_pressed = False
    finally:
        win32gui.ReleaseDC(0, hdc)

# --------------------------------------------------------------------
# Listener & cleanup
# --------------------------------------------------------------------

def on_press(key):
    if key == Key.esc:
        print("[EXIT] ESC pressed – shutting down…")
        RUNNING.value = False
        return False  # stop listener


def cleanup(proc_list):
    for p in proc_list:
        try:
            p.terminate(); p.join(timeout=1)
            if p.is_alive():
                p.kill()
        except Exception as exc:
            print(f"[WARN] cleanup error: {exc}")

# --------------------------------------------------------------------
# Main
# --------------------------------------------------------------------

def main():
    set_high_priority()  # elevate parent immediately
    roblox_hwnd = find_roblox_hwnd()
    if roblox_hwnd:
        print(f"[INFO] Roblox HWND: {roblox_hwnd}")
    else:
        print("[INFO] Roblox HWND not found; falling back to title substring focus checks.")

    print("Starting Win32GUI (processes) backend. Press ESC to stop.")
    cores = list(range(psutil.cpu_count(logical=False))) or [0]
    procs = []
    for idx, tgt in enumerate(TARGETS):
        p = Process(target=monitor_lane_process, args=(tgt, RUNNING, roblox_hwnd))
        p.start()
        try:
            child = psutil.Process(p.pid)
            try: child.cpu_affinity([cores[idx % len(cores)]])
            except Exception as exc: print(f"[INFO] affinity set failed: {exc}")
            try: child.nice(psutil.HIGH_PRIORITY_CLASS)
            except Exception: pass  # already set inside child
        except Exception as exc:
            print(f"[INFO] child psutil attach failed: {exc}")
        procs.append(p)
    with Listener(on_press=on_press) as listener: listener.join()
    RUNNING.value = False
    cleanup(procs)

if __name__ == "__main__":
    main()
