#!/usr/bin/env python3
"""Pair a WiZ bulb to your Wi-Fi network — without the app.

Uses the bulb's AP pairing mode (WiZ calls it "manual pairing"):

  1. Put the bulb in pairing mode: wall switch OFF for 10 s, then
     ON-OFF-ON-OFF-ON (3 clicks, ~1-2 s between). It pulses blue.
     If it isn't paired within ~1 minute it double-pulses PURPLE and
     broadcasts a Wi-Fi network named "WiZConfig_xxxx" (last 4 chars
     relate to the bulb's MAC). That's the AP mode we need.
     (5 clicks = factory reset to a different home; 3 clicks is the
     normal re-pair.)
  2. Connect a Wi-Fi radio to "WiZConfig_xxxx" — a laptop, or your
     Windows host (this WSL2 VM reaches it through the host's NAT
     when the host is connected to the AP).
  3. Run:  pair.py send "<home-ssid>" "<home-password>"
     — sends setWifiConfig over UDP 38899 to the bulb's hotspot IP
     (192.168.4.1, the ESP soft-AP default). The exact param names are
     undocumented, so several variants are tried in order; the bulb
     stays in AP mode until pairing succeeds, so you can just re-run.
  4. The bulb leaves the hotspot and joins your network. Reconnect the
     machine to home Wi-Fi, then find the bulb's new DHCP address:
       pair.py find                    (from a machine on the home LAN)
       pair.py scan 192.168.0.0/24     (unicast sweep, works through
                                        WSL2's NAT)

Notes:
  - The bulb needs 2.4 GHz b/g/n Wi-Fi, WPA2/WPA3-Personal, with a
    password (WiZ requirement).
  - The app's *normal* pairing uses Bluetooth to hand over credentials;
    this script replaces the app's fallback (AP) path, which is the
    fully-UDP one.
"""
import argparse
import ipaddress
import json
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from wiz import PORT, Wiz

AP_IP_DEFAULT = "192.168.4.1"  # ESP8266/ESP32 soft-AP default


def _mask_secret(params):
    return {k: ("***" if k in ("password", "psk") else v) for k, v in params.items()}


def cmd_send(args):
    """Send home Wi-Fi credentials to a bulb sitting in AP mode."""
    lamp = Wiz(args.ap_ip, timeout=5)
    try:
        try:
            cfg = lamp.info()
        except (RuntimeError, OSError) as e:
            print(f"cannot reach a bulb at {args.ap_ip}:{PORT} ({e})", file=sys.stderr)
            print("hint: is this machine connected to the bulb's WiZConfig_* Wi-Fi?", file=sys.stderr)
            return 1
        mac = cfg["mac"]
        print(f"bulb on hotspot: mac={mac} model={cfg.get('moduleName')} fw={cfg.get('fwVersion')}")

        if args.raw_params:
            variants = [json.loads(args.raw_params)]
        else:
            variants = [
                # setPilot-style: mac + src + flat credentials (most likely)
                {"mac": mac, "src": "", "ssid": args.ssid, "password": args.password},
                # minimal flat
                {"ssid": args.ssid, "password": args.password},
                # flat, WPA2-key-style name
                {"mac": mac, "src": "", "ssid": args.ssid, "psk": args.password},
                # LIFX-style nested
                {"mac": mac, "src": "", "wifi": {"ssid": args.ssid, "password": args.password}},
            ]

        for i, params in enumerate(variants, 1):
            print(f"trying variant {i}/{len(variants)}: {json.dumps(_mask_secret(params))}", flush=True)
            try:
                result = lamp.call("setWifiConfig", params, retries=1)
                print(f"  -> bulb accepted it: {result}")
                print("the bulb should now be leaving the hotspot and joining your network.")
                return 0
            except RuntimeError as e:
                print(f"  -> bulb error: {e}")
            except OSError as e:
                print(f"  -> no response ({e})")
        print("no variant worked. The bulb should still be in AP mode —", file=sys.stderr)
        print("double-check SSID (2.4 GHz!) and password, then re-run.", file=sys.stderr)
        return 1
    finally:
        lamp.close()


def cmd_find(args):
    """Discover bulbs on THIS machine's LAN via UDP broadcast."""
    payload = json.dumps({"method": "getSystemConfig", "params": {}, "id": 1}).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.5)
    found = {}
    deadline = time.monotonic() + args.seconds
    last_send = 0.0
    try:
        while time.monotonic() < deadline:
            if time.monotonic() - last_send >= 1.0:
                sock.sendto(payload, ("255.255.255.255", PORT))
                last_send = time.monotonic()
            try:
                raw, addr = sock.recvfrom(4096)
                resp = json.loads(raw.decode("utf-8", "replace"))
                if resp.get("method") == "getSystemConfig" and "result" in resp:
                    r = resp["result"]
                    key = r.get("mac") or addr[0]
                    if key not in found:
                        found[key] = True
                        print(f"found: ip={addr[0]} mac={r.get('mac')} "
                              f"model={r.get('moduleName')} fw={r.get('fwVersion')}")
            except (BlockingIOError, TimeoutError):
                pass
    finally:
        sock.close()
    if not found:
        print("no bulbs found — is this machine on the same LAN as the bulb?")
        return 1
    print(f"{len(found)} bulb(s) found.")
    return 0


def cmd_scan(args):
    """Unicast-sweep a range for bulbs (works through WSL2's NAT)."""
    if "/" in args.range:
        addrs = [str(ip) for ip in ipaddress.ip_network(args.range, strict=False).hosts()]
    elif "-" in args.range:
        start, end = args.range.rsplit("-", 1)
        base = ".".join(start.split(".")[:3])
        addrs = [f"{base}.{i}" for i in range(int(start.split(".")[-1]),
                                               int(end.split(".")[-1]) + 1)]
    else:
        addrs = [args.range]
    print(f"scanning {len(addrs)} address(es) for {args.range} ...")

    def probe(ip):
        lamp = Wiz(ip, timeout=1.0)
        try:
            return ip, lamp.call("getSystemConfig", retries=1)
        except Exception:
            return None
        finally:
            lamp.close()

    found = 0
    with ThreadPoolExecutor(max_workers=32) as ex:
        for res in ex.map(probe, addrs):
            if res:
                ip, r = res
                found += 1
                print(f"found: ip={ip} mac={r.get('mac')} "
                      f"model={r.get('moduleName')} fw={r.get('fwVersion')}")
    if not found:
        print("no bulbs found in range.")
        return 1
    print(f"{found} bulb(s) found.")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Pair a WiZ bulb to Wi-Fi without the app (AP pairing mode).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("send", help="send home Wi-Fi credentials to a bulb in AP mode")
    s.add_argument("ssid", help="home 2.4 GHz Wi-Fi SSID")
    s.add_argument("password", help="home Wi-Fi password")
    s.add_argument("--ap-ip", default=AP_IP_DEFAULT,
                   help=f"bulb's hotspot IP (default {AP_IP_DEFAULT})")
    s.add_argument("--raw-params", help="raw JSON params to try instead of the built-in variants")
    s.set_defaults(func=cmd_send)

    f = sub.add_parser("find", help="discover bulbs on this machine's LAN (UDP broadcast)")
    f.add_argument("--seconds", type=float, default=10)
    f.set_defaults(func=cmd_find)

    c = sub.add_parser("scan", help="unicast-sweep a range for bulbs (through NAT)")
    c.add_argument("range", metavar="RANGE",
                   help="CIDR (192.168.0.0/24), a.b.c.d-e (192.168.0.100-150) or a single IP")
    c.set_defaults(func=cmd_scan)

    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
