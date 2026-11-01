#!/usr/bin/env python3
"""Direct LAN client + library for WiZ (Philips WiZ Connected) bulbs — no app, no cloud.

Protocol (reverse-engineered, same as the official app):
  - UDP, port 38899, plain JSON, no encryption, no framing.
  - Request:  {"method": "<name>", "params": {...}, "id": <n>}
  - Response: {"method": ..., "id": <n>, "env": "pro", "result": {...}}  (or "error")

Methods:
  getSystemConfig -> mac, model, firmware, ...
  getPilot        -> current state (on/off, dimming, temp or r/g/b/c/w)
  setPilot        -> set state: mac, src:"", state, dimming (10-100),
                     temp (kelvin) OR r,g,b,c,w (0-255; c=cool white, w=warm white)

As a program:
  python3 wiz.py info
  python3 wiz.py on | off
  python3 wiz.py dim 50
  python3 wiz.py color blue            # or #rrggbb, optional dimming
  python3 wiz.py temp 2700 80

As a library:
  from wiz import Wiz, to_rgb
  lamp = Wiz("192.168.x.x")   # your bulb's IP
  lamp.state()                 # verify reachable (raises if off), caches MAC
  lamp.color(74, 140, 0, 60)   # r, g, b, dimming
  lamp.color_hex("#4a8c00")
  lamp.white(2700, 80)         # kelvin, dimming
  lamp.dim(40)
  lamp.fire_set(state=True, r=74, g=140, b=0, dimming=50)   # fire-and-forget
  lamp.off()
  lamp.close()                 # (or use `with Wiz(...) as lamp:`)

`call()` waits for the matching response (matched by JSON-RPC id, so stale
responses from earlier `fire()` calls are skipped). `fire()` sends without
waiting — use it for fast sequences; the bulb applies commands in order.

Raises RuntimeError on bulb errors, OSError (timeout) if the bulb is
unreachable. Works from WSL2 (UDP passes the NAT transparently). The bulb
only needs the official app for first-time Wi-Fi setup.
"""
import argparse
import ipaddress
import json
import socket
import sys

# Placeholder default — pass your bulb's IP via --host (CLI) or the
# positional arg (halloween.py), or edit this to your bulb's address.
DEFAULT_HOST = "192.168.x.x"
PORT = 38899
MIN_DIM, MAX_DIM = 10, 100

__all__ = ["Wiz", "to_rgb", "NAMED_COLORS", "DEFAULT_HOST", "PORT"]


def _clamp_dim(level) -> int:
    return max(MIN_DIM, min(MAX_DIM, int(round(level))))


class Wiz:
    """One WiZ bulb, controlled over UDP. See the module docstring for examples."""

    def __init__(self, host: str = DEFAULT_HOST, timeout: float = 3.0):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise ValueError(
                f"{host!r} is not a valid IP address — pass your bulb's IP "
                f"(DEFAULT_HOST in wiz.py is a placeholder)"
            )
        self.host = host
        self.timeout = timeout
        self.mac: str | None = None
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._id = 0

    # -- low level ----------------------------------------------------------
    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send(self, method: str, params: dict | None, rid: int):
        self._sock.sendto(
            json.dumps({"method": method, "params": params or {}, "id": rid}).encode(),
            (self.host, PORT),
        )

    def _recv(self, method: str, rid: int, timeout: float | None = None) -> dict:
        if timeout is not None:
            self._sock.settimeout(timeout)
        while True:
            raw, _ = self._sock.recvfrom(4096)
            try:
                resp = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if resp.get("method") != method:
                continue
            if "id" in resp and resp["id"] != rid:
                continue  # stale response from an earlier fire()
            if "error" in resp:
                raise RuntimeError(f"{method}: {resp['error']['message']}")
            return resp.get("result", {})

    def call(self, method: str, params: dict | None = None, retries: int = 3) -> dict:
        """Send a command and wait for its result.

        Retries on timeout: reply datagrams are occasionally lost over UDP
        (the command still gets applied). All our commands are idempotent
        full-state sets, so re-sending is safe.
        """
        attempt_timeout = min(self.timeout, 1.5)
        last_err: TimeoutError | None = None
        for _ in range(max(1, retries)):
            rid = self._next_id()
            self._send(method, params, rid)
            try:
                return self._recv(method, rid, timeout=attempt_timeout)
            except TimeoutError as e:
                last_err = e
        assert last_err is not None
        raise last_err

    def fire(self, method: str, params: dict | None = None):
        """Send a command without waiting for a response (fast sequences)."""
        self._send(method, params, self._next_id())

    def drain(self):
        """Discard any pending responses (e.g. from fire() calls)."""
        self._sock.settimeout(0)
        try:
            while True:
                try:
                    self._sock.recvfrom(4096)
                except (BlockingIOError, TimeoutError):
                    break
        finally:
            self._sock.settimeout(self.timeout)

    # -- device info ---------------------------------------------------------
    def info(self) -> dict:
        """Device info: mac, model, firmware, homeId, ..."""
        return self.call("getSystemConfig")

    def state(self) -> dict:
        """Current pilot state: state, dimming, temp and/or r,g,b,c,w. Also caches MAC."""
        result = self.call("getPilot")
        self.mac = self.mac or result.get("mac")
        return result

    # -- control (wait for response) ------------------------------------------
    def set(self, **kw):
        """Raw setPilot. Kwargs: state, dimming, temp, r, g, b, c, w."""
        if self.mac is None:
            self.mac = self.state().get("mac") or self.info()["mac"]
        self.call("setPilot", {"mac": self.mac, "src": "", **kw})

    def on(self, **kw):
        self.set(state=True, **kw)

    def off(self):
        self.set(state=False)

    def dim(self, level: int):
        """Turn on at a brightness (10-100), keep colour."""
        self.on(dimming=_clamp_dim(level))

    def color(self, r: int, g: int, b: int, dimming: int = 100):
        """Set an RGB colour (0-255 each) and brightness."""
        self.on(r=r, g=g, b=b, c=0, w=0, dimming=_clamp_dim(dimming))

    def color_hex(self, spec: str, dimming: int = 100):
        """Set a colour by name (see NAMED_COLORS) or #rrggbb."""
        self.color(*to_rgb(spec), dimming=dimming)

    def white(self, kelvin: int = 4000, dimming: int = 100):
        """Set a colour temperature in kelvin (2200-6500)."""
        self.on(temp=max(2200, min(6500, kelvin)), dimming=_clamp_dim(dimming))

    # -- control (fire-and-forget, for fast sequences) --------------------------
    def fire_set(self, **kw):
        """Raw setPilot without waiting — the bulb applies commands in order."""
        if self.mac is None:
            self.mac = self.state().get("mac") or self.info()["mac"]
        self.fire("setPilot", {"mac": self.mac, "src": "", **kw})

    def fire_off(self):
        self.fire_set(state=False)

    # -- lifecycle ---------------------------------------------------------------
    def close(self):
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


NAMED_COLORS = {
    "red": (255, 0, 0), "orange": (255, 128, 0), "yellow": (255, 255, 0),
    "green": (0, 255, 0), "cyan": (0, 255, 255), "blue": (0, 0, 255),
    "purple": (128, 0, 255), "pink": (255, 105, 180), "white": (255, 255, 255),
}


def to_rgb(spec: str):
    """Colour name or #rrggbb -> (r, g, b)."""
    s = spec.lower()
    if s in NAMED_COLORS:
        return NAMED_COLORS[s]
    if s.startswith("#") and len(s) == 7:
        return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))
    raise SystemExit(f"unknown color '{spec}' — use one of {', '.join(NAMED_COLORS)} or #rrggbb")


def main():
    p = argparse.ArgumentParser(description="Control a WiZ bulb over UDP (no app needed).")
    p.add_argument("--host", default=DEFAULT_HOST)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="device info + current state")
    sub.add_parser("on", help="turn on")
    sub.add_parser("off", help="turn off")
    d = sub.add_parser("dim", help="set brightness 10-100")
    d.add_argument("level", type=int)
    c = sub.add_parser("color", help="set RGB color: name or #rrggbb")
    c.add_argument("color")
    c.add_argument("dimming", type=int, nargs="?")
    t = sub.add_parser("temp", help="set color temperature in kelvin (2200-6500)")
    t.add_argument("kelvin", type=int)
    t.add_argument("dimming", type=int, nargs="?")
    args = p.parse_args()

    try:
        w = Wiz(args.host)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    with w:
        try:
            if args.cmd == "info":
                print(json.dumps({"device": w.info(), "state": w.state()}, indent=2))
            elif args.cmd == "on":
                w.on()
            elif args.cmd == "off":
                w.off()
            elif args.cmd == "dim":
                w.dim(args.level)
            elif args.cmd == "color":
                w.color_hex(args.color, args.dimming or 100)
            elif args.cmd == "temp":
                w.white(args.kelvin, args.dimming or 100)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        except OSError as e:
            print(f"no response from {args.host}:{PORT} — bulb offline? ({e})", file=sys.stderr)
            sys.exit(1)
    print(f"ok ({args.host})")


if __name__ == "__main__":
    main()
