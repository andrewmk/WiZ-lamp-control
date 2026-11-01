#!/usr/bin/env python3
"""Halloween mode for the Wiz bulb: dark green, like lightning.

Uses the wiz module as a library (same directory) and fires commands
without waiting for each response, so flashes are fast.

Modes:
  flicker (default)  green lightning — sits at a dim baseline, then random
                     bright flashes that decay fast, often in quick double
                     or triple strikes, with short irregular pauses
  pulse              slow sine-wave "breathing" between --min and --max

--min/--max:
  flicker: dim baseline % / flash ceiling %
  pulse:   oscillation floor % / ceiling %

Turns the bulb off on exit (Ctrl+C or --duration).

IP is the first argument — the bulb's address. DEFAULT_HOST in wiz.py is a
placeholder, so pass it (or edit wiz.py to set your own default).

Examples:
  ./halloween.py 192.168.x.x            # green lightning, until Ctrl+C
  ./halloween.py 192.168.x.x --duration 300
  ./halloween.py --min 10 --max 100     # full-blast flashes
  ./halloween.py --mode pulse --min 15 --max 35   # the old breathing mode
"""
import argparse
import math
import random
import sys
import time

from wiz import DEFAULT_HOST, Wiz, to_rgb


def main():
    # ------------------------------------------------------------------ #
    # 1. Command line                                                     #
    # ------------------------------------------------------------------ #
    ap = argparse.ArgumentParser(description="Halloween: dark green lightning (or slow pulse)")
    ap.add_argument("host", nargs="?", default=DEFAULT_HOST, metavar="IP",
                    help=f"bulb IP (required — default is the {DEFAULT_HOST} placeholder)")
    # #4a8c00 is tuned for this bulb: its "green" channel runs cyan-ish, so a
    # pure green (equal low red/blue) reads blue; a little red + zero blue
    # shifts the perceived hue to true dark green.
    ap.add_argument("--color", default="#4a8c00", help="hex colour (default #4a8c00, dark green)")
    ap.add_argument("--mode", choices=("flicker", "pulse"), default="flicker",
                    help="flicker = random lightning (default), pulse = slow breathing")
    ap.add_argument("--min", dest="lo", type=int, default=10,
                    help="flicker: dim baseline %% / pulse: floor %% (default 10)")
    ap.add_argument("--max", dest="hi", type=int, default=80,
                    help="flicker: flash ceiling %% / pulse: ceiling %% (default 80)")
    ap.add_argument("--period", type=float, default=4.0, help="pulse: seconds per full cycle (default 4)")
    ap.add_argument("--step", type=float, default=0.25, help="pulse: seconds between updates (default 0.25)")
    ap.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = run until Ctrl+C)")
    a = ap.parse_args()

    # 10 is the bulb's hardware floor; we never want min >= max.
    if not (10 <= a.lo < a.hi <= 100):
        sys.exit("--min/--max must satisfy 10 <= min < max <= 100")

    # ------------------------------------------------------------------ #
    # 2. Connect + helpers                                                #
    # ------------------------------------------------------------------ #
    try:
        lamp = Wiz(a.host)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    ok = False  # True once we've confirmed the bulb is alive (for the farewell)
    try:
        # Explicit "is it alive?" check before doing anything: raises
        # TimeoutError/OSError if the bulb is off/unreachable, and caches
        # the MAC that setPilot commands need.
        try:
            lamp.state()
        except (RuntimeError, OSError) as e:
            print(f"error: {e} — bulb off/unreachable?", file=sys.stderr)
            return
        ok = True
        # Resolve the colour once (name or #rrggbb -> r,g,b ints).
        r, g, b = to_rgb(a.color)

        # --duration support: an absolute deadline (None = run forever).
        start = time.monotonic()
        deadline = start + a.duration if a.duration else None

        def alive():
            return deadline is None or time.monotonic() < deadline

        def nap(sec):
            """Sleep in <=0.1s slices so Ctrl+C / --duration stay responsive
            (a single long sleep would make exits lag)."""
            end = time.monotonic() + sec
            while alive() and time.monotonic() < end:
                time.sleep(min(0.1, max(0.0, end - time.monotonic())))

        def set_level(level, wait=False):
            """Set the brightness, always in the chosen colour.

            wait=True  -> lamp.set(): waits for the bulb's reply. Used by
                          pulse mode (updates are slow, and the reply doubles
                          as a health check).
            wait=False -> lamp.fire_set(): fire-and-forget. Used by flicker
                          mode — waiting for each reply would cost a full
                          round trip (~100ms+) and cap the flash rate.
            """
            kw = dict(state=True, r=r, g=g, b=b, dimming=max(10, min(100, int(round(level)))))
            (lamp.set if wait else lamp.fire_set)(**kw)

        # ------------------------------------------------------------------ #
        # 3. The show                                                       #
        # ------------------------------------------------------------------ #
        if a.mode == "flicker":
            desc = f"green lightning: {a.color}, baseline {a.lo}%, flashes up to {a.hi}%"
        else:
            desc = f"pulse: {a.color}, {a.lo}-{a.hi}% every {a.period:g}s"
        print(f"halloween mode: {desc} — Ctrl+C to stop")

        try:
            if a.mode == "flicker":
                # ---------------- green lightning ---------------- #
                # Model one storm: mostly a dim baseline, punctuated by
                # sudden bright strikes that fade fast, sometimes
                # double/triple-striking, with irregular quiet gaps.
                set_level(a.lo)  # start at the dim baseline
                while alive():
                    # Quiet gap. Exponential distribution -> mostly short
                    # gaps with an occasional longer lull (mean ~0.6s,
                    # 0.1s floor, 2.5s cap) so the rhythm never feels metronomic.
                    nap(max(0.1, min(random.expovariate(1 / 0.6), 2.5)))
                    if not alive():
                        break

                    # How many strikes in this burst: 50% chance of a second,
                    # 20% chance of a third (real lightning often re-strikes).
                    strikes = 1
                    if random.random() < 0.5:
                        strikes += 1
                    if random.random() < 0.2:
                        strikes += 1

                    for i in range(strikes):
                        if not alive():
                            break
                        # The strike itself: jump to 75-100% of the flash
                        # ceiling and hold for a few tens of ms.
                        level = random.uniform(0.75, 1.0) * a.hi
                        set_level(level)
                        time.sleep(random.uniform(0.03, 0.06))
                        # Fast decay: 1-3 steps, each 40-75% of the previous
                        # level, floor at the baseline -> the lightning fade.
                        for _ in range(random.randint(1, 3)):
                            level = max(a.lo, level * random.uniform(0.25, 0.6))
                            set_level(level)
                            time.sleep(random.uniform(0.03, 0.06))
                        # Small gap between re-strikes within the same burst.
                        if i + 1 < strikes and alive():
                            time.sleep(random.uniform(0.03, 0.1))

                    # Settle back to the dim baseline until the next burst.
                    if alive():
                        set_level(a.lo)
            else:
                # ---------------- slow breathing pulse ---------------- #
                # Sine wave between --min and --max; one full cycle per
                # --period seconds. Waiting calls here (see set_level).
                mid, amp = (a.lo + a.hi) / 2, (a.hi - a.lo) / 2
                while alive():
                    t = time.monotonic() - start
                    set_level(mid + amp * math.sin(2 * math.pi * t / a.period), wait=True)
                    nap(a.step)
        except KeyboardInterrupt:
            pass  # Ctrl+C: fall through to the cleanup below
        except (RuntimeError, OSError) as e:
            # Bulb died mid-show (power cut, Wi-Fi hiccup): report and clean up.
            print(f"\nerror: {e} — bulb off/unreachable?", file=sys.stderr)
    finally:
        # Always end with the bulb OFF and the socket closed, no matter how
        # we got here (Ctrl+C, --duration, error).
        try:
            if ok:
                lamp.drain()  # flush stale replies from the fire-and-forget burst
                lamp.off()    # waiting call, so we know the off actually landed
        except Exception:
            pass  # best effort — the bulb may already be off
        lamp.close()
        if ok:
            print("light off — happy halloween 🎃")


if __name__ == "__main__":
    main()
