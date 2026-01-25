import argparse
import time
from threading import Event

from pynput.keyboard import Controller, Listener, Key


def parse_key(value: str):
    value = value.strip().lower()
    if len(value) == 1:
        return value
    if value in ("esc", "escape"):
        return Key.esc
    if value == "space":
        return Key.space
    if value == "enter":
        return Key.enter
    if value == "tab":
        return Key.tab
    raise ValueError(f"Unsupported key: {value}")


def main():
    parser = argparse.ArgumentParser(
        description="Spam a key using pynput to probe max injection rate."
    )
    parser.add_argument("--key", default="f", help="Key to spam (e.g. f, space).")
    parser.add_argument("--duration", type=float, default=5.0, help="Seconds to run.")
    parser.add_argument("--hold", type=float, default=0.001, help="Seconds to hold key.")
    parser.add_argument("--gap", type=float, default=0.0, help="Seconds between presses.")
    parser.add_argument(
        "--warmup", type=float, default=1.0, help="Delay before spamming starts."
    )
    args = parser.parse_args()

    try:
        key = parse_key(args.key)
    except ValueError as exc:
        raise SystemExit(str(exc))

    stop_event = Event()

    def on_press(k):
        if k == Key.esc:
            stop_event.set()
            return False
        return True

    listener = Listener(on_press=on_press)
    listener.start()

    print("Focus the target window. Press ESC to stop early.")
    if args.warmup > 0:
        time.sleep(args.warmup)

    kb = Controller()
    count = 0
    start = time.perf_counter()
    while not stop_event.is_set():
        now = time.perf_counter()
        if args.duration > 0 and now - start >= args.duration:
            break

        kb.press(key)
        if args.hold > 0:
            time.sleep(args.hold)
        kb.release(key)
        count += 1
        if args.gap > 0:
            time.sleep(args.gap)

    elapsed = time.perf_counter() - start
    kps = count / elapsed if elapsed > 0 else 0.0
    print(f"Sent {count} presses in {elapsed:.3f}s -> {kps:.1f} KPS")

    stop_event.set()
    listener.stop()
    listener.join(timeout=1)


if __name__ == "__main__":
    main()
