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
from threading import Thread
from multiprocessing import Process, Value
from pynput.keyboard import Controller, Listener, Key
import mss
import win32gui
import psutil
import sys
import ctypes

# --------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------
TARGET_COLOR = (255, 255, 255)
TARGETS = [
    {"x": 731,  "y": 880, "key": "e"},
    {"x": 881,  "y": 880, "key": "r"},
    {"x": 1031, "y": 880, "key": "t"},
    {"x": 1181, "y": 880, "key": "y"}
]

OFFSET             = 0.00   # seconds – fine‑tune if note timing drifts
BRIGHTNESS_THRESH   = 240    # >= treated as white
TOLERANCE           = 120    # per‑channel RGB delta vs TARGET_COLOR
INNER_SLEEP         = 0.0005 # loop throttle – keeps CPU < 100 %
FOCUS_CHECK_INTERVAL = 0.01  # seconds – throttle expensive focus checks

# Focus detection mode: 'title' (default), 'hwnd' (faster), 'none' (always focused)
FOCUS_MODE = "title"
ROBLOX_TITLE_SUBSTR = "Roblox"
ROBLOX_HWND = None  # will be detected at startup if possible

# Thread scheduling enhancements (Windows only)

# Key actuation strategy: hold only (press on white start, release on white end)
 
RUNNING = Value('b', True)   # shared run flag
keyboard = Controller()      # global for thread backend

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


def enable_1ms_timer():
    """Increase system timer resolution to ~1ms (system-wide while process alive)."""
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception as exc:
        print(f"[INFO] timeBeginPeriod failed: {exc}")


def disable_1ms_timer():
    """Revert system timer resolution change."""
    try:
        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass


def try_set_current_thread_priority(level: int):
    # Retained for future use; currently no per-thread priority changes are made.
    try:
        if sys.platform != 'win32':
            return
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), int(level))
    except Exception:
        pass

# --------------------------------------------------------------------
# Utility functions
# --------------------------------------------------------------------


def get_pixel_color_win32(x: int, y: int):
    hdc = win32gui.GetDC(0)
    try:
        color = win32gui.GetPixel(hdc, x, y)
        if color == -1:
            return (0, 0, 0)
        return (color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF)
    finally:
        win32gui.ReleaseDC(0, hdc)


def get_pixel_color_mss_from_bbox(sct: mss.mss, bbox: dict):
    shot = sct.grab(bbox)
    raw = shot.raw  # BGRA bytes
    # Avoid numpy for 1x1; parse bytes directly
    return (raw[2], raw[1], raw[0])


def is_roblox_focused():
    if FOCUS_MODE == "none":
        return True
    fg = win32gui.GetForegroundWindow()
    if FOCUS_MODE == "hwnd":
        return fg == ROBLOX_HWND if ROBLOX_HWND else (ROBLOX_TITLE_SUBSTR in win32gui.GetWindowText(fg))
    # title mode
    return ROBLOX_TITLE_SUBSTR in win32gui.GetWindowText(fg)


def is_white(pixel):
    r, g, b = pixel  # ints
    # Branchless fast path: average luminance check
    if (r + g + b) > (BRIGHTNESS_THRESH * 3):
        return True
    tr, tg, tb = TARGET_COLOR
    tol = TOLERANCE
    # Inline compares avoid tuple/zips
    return (abs(r - tr) <= tol and abs(g - tg) <= tol and abs(b - tb) <= tol)


def get_pixel_color_from_hdc(hdc, x: int, y: int):
    # Faster ctypes call avoids some pywin32 overhead
    color = ctypes.windll.gdi32.GetPixel(hdc, x, y)
    if (color & 0xFFFFFFFF) == 0xFFFFFFFF:
        return (0, 0, 0)
    return (color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF)


def find_roblox_hwnd():
    """Locate the Roblox window handle by title substring once at startup."""
    matches = []
    target = ROBLOX_TITLE_SUBSTR
    def enum_cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if target in title:
                matches.append(hwnd)
        except Exception:
            pass
    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        return None
    return matches[0] if matches else None


def busy_wait(duration_s: float):
    """Busy-wait with cooperative yields for sub-ms accuracy without blocking scheduler."""
    end = time.perf_counter() + duration_s
    while time.perf_counter() < end:
        # Yield to avoid 100% CPU monopolization
        time.sleep(0)


# Removed tap actuator; hold mode only

# --------------------------------------------------------------------
# Lane monitors – Threads (MSS)
# --------------------------------------------------------------------

def monitor_lane_thread(target):
    pressed = False
    white_frames = non_white = 0
    # Removed tap mode; hold-only behavior

    x, y, key = target['x'], target['y'], target['key']
    debounce = 1
    inactivity_limit = 30

    # Local bindings for speed
    is_white_local = is_white
    perf = time.perf_counter
    sleep = time.sleep
    kb_press = keyboard.press
    kb_release = keyboard.release
    focus_check_interval = FOCUS_CHECK_INTERVAL
    last_focus_check = 0.0
    focused = True

    with mss.mss() as sct:
        bbox = {"left": x, "top": y, "width": 1, "height": 1}
        get_pixel = get_pixel_color_mss_from_bbox
        while RUNNING.value:
            now = perf()
            if now - last_focus_check >= focus_check_interval:
                focused = is_roblox_focused()
                last_focus_check = now
            if not focused:
                if pressed:
                    kb_release(key)
                    pressed = False
                sleep(0.1)
                continue

            pixel = get_pixel(sct, bbox)
            if is_white_local(pixel):
                white_frames += 1; non_white = 0
                if white_frames >= debounce:
                    if not pressed:
                        if OFFSET > 0:
                            busy_wait(OFFSET)
                        kb_press(key); pressed = True
            else:
                non_white += 1; white_frames = 0
                if non_white >= debounce and pressed:
                    kb_release(key); pressed = False

            if non_white > inactivity_limit:
                if pressed:
                    kb_release(key); pressed = False
                non_white = 0
            sleep(INNER_SLEEP)

# --------------------------------------------------------------------
# Lane monitors – Processes (Win32GUI)
# --------------------------------------------------------------------

def monitor_lane_process(target, run_flag):
    set_high_priority()  # elevate child
    enable_1ms_timer()
    kb = Controller()
    pressed = False
    white_frames = non_white = 0
    # Removed tap mode; hold-only behavior
    x, y, key = target['x'], target['y'], target['key']
    debounce = 1
    inactivity_limit = 30

    # Local bindings for speed
    is_white_local = is_white
    perf = time.perf_counter
    sleep = time.sleep
    kb_press = kb.press
    kb_release = kb.release
    get_from_hdc = get_pixel_color_from_hdc
    focus_check_interval = FOCUS_CHECK_INTERVAL
    last_focus_check = 0.0
    focused = True

    hdc = win32gui.GetDC(0)
    try:
        while run_flag.value:
            now = perf()
            if now - last_focus_check >= focus_check_interval:
                focused = is_roblox_focused()
                last_focus_check = now
            if not focused:
                if pressed:
                    kb_release(key); pressed = False
                sleep(0.1)
                continue

            pixel = get_from_hdc(hdc, x, y)
            if is_white_local(pixel):
                white_frames += 1; non_white = 0
                if white_frames >= debounce:
                    if not pressed:
                        if OFFSET > 0:
                            busy_wait(OFFSET)
                        kb_press(key); pressed = True
            else:
                non_white += 1; white_frames = 0
                if non_white >= debounce and pressed:
                    kb_release(key); pressed = False

            if non_white > inactivity_limit:
                if pressed:
                    kb_release(key); pressed = False
                non_white = 0
            sleep(INNER_SLEEP)
    finally:
        win32gui.ReleaseDC(0, hdc)
        disable_1ms_timer()

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
    enable_1ms_timer()
    global ROBLOX_HWND, FOCUS_MODE
    try:
        ROBLOX_HWND = find_roblox_hwnd()
        if ROBLOX_HWND:
            FOCUS_MODE = "hwnd"
    except Exception:
        ROBLOX_HWND = None
    print("Choose pixel detection method:\n  1. MSS (threads) – faster\n  2. Win32GUI (processes) – core affinity")
    method = None
    while method not in ("1", "2"):
        method = input("Enter your choice (1 or 2): ").strip()

    if method == "1":
        threads = [Thread(target=monitor_lane_thread, args=(t,), daemon=True) for t in TARGETS]
        for th in threads: th.start()
        with Listener(on_press=on_press) as listener: listener.join()
        for th in threads: th.join(timeout=1)
    else:
        # Use the set of logical CPUs available to this process; prefer even indices first
        available = psutil.Process().cpu_affinity() or [0]
        even = [c for c in available if c % 2 == 0]
        odd = [c for c in available if c % 2 == 1]
        cores = (even + odd) or available
        procs = []
        for idx, tgt in enumerate(TARGETS):
            p = Process(target=monitor_lane_process, args=(tgt, RUNNING))
            p.start()
            try: psutil.Process(p.pid).cpu_affinity([cores[idx % len(cores)]])
            except Exception as exc: print(f"[INFO] affinity set failed: {exc}")
            try: psutil.Process(p.pid).nice(psutil.HIGH_PRIORITY_CLASS)
            except Exception: pass  # already set inside child
            procs.append(p)
        with Listener(on_press=on_press) as listener: listener.join()
        RUNNING.value = False
        cleanup(procs)
    disable_1ms_timer()

if __name__ == "__main__":
    main()