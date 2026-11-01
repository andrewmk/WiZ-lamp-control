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
     — sends setWifiConfig over UDP to the bulb's hotspot IP
     (default port 38899, override with --port). --ap-ip auto
     (default) probes 192.168.56.1 (observed WiZ AP IP — the
     client's gateway on that network) then 192.168.4.1 (ESP
     soft-AP default). The exact param names are undocumented, so
     several variants are tried in order; the bulb stays in AP mode
     until pairing succeeds, so you can just re-run.
     If nothing gets a reply, run ap-sweep.py (full 1-65535 UDP
     sweep + mDNS browse) to find out what the AP is actually
     listening on.
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

AP_IP_CANDIDATES = ["192.168.56.1", "192.168.4.1"]
# Observed WiZ AP IP (client gateway on the WiZConfig_* network) first,
# then the generic ESP soft-AP default.
AP_IP_DEFAULT = "auto"

# Ports probed by `diag`: the known Wiz LAN ports first, then other
# suspects, in case the AP-mode firmware listens elsewhere.
AP_PORTS = [38899, 38900, 3889, 8909, 56700, 4444, 80, 55443]


def _mask_secret(params):
    return {k: ("***" if k in ("password", "psk") else v) for k, v in params.items()}


def _find_ap(args, port):
    """Return (ip, cfg) of a reachable bulb on a known AP network, else (None, None)."""
    candidates = AP_IP_CANDIDATES if args.ap_ip == "auto" else [args.ap_ip]
    for ip in candidates:
        probe = Wiz(ip, port=port, timeout=2)
        try:
            return ip, probe.info()
        except (RuntimeError, OSError):
            pass
        finally:
            probe.close()
    return None, None


def cmd_send(args):
    """Send home Wi-Fi credentials to a bulb sitting in AP mode."""
    ip, cfg = _find_ap(args, args.port)
    if ip is None:
        tried = ", ".join(AP_IP_CANDIDATES) if args.ap_ip == "auto" else args.ap_ip
        print(f"cannot reach a bulb on the AP (tried: {tried} port {args.port})", file=sys.stderr)
        print("hint: is the Windows host still connected to the bulb's WiZConfig_* Wi-Fi?", file=sys.stderr)
        print("      if ping 192.168.56.1 works but this stays silent, the AP-mode", file=sys.stderr)
        print("      server is elsewhere: run  python3 ap-sweep.py  (full sweep + mDNS)", file=sys.stderr)
        return 1
    print(f"bulb on AP at {ip}:{args.port}: mac={cfg['mac']} model={cfg.get('moduleName')} fw={cfg.get('fwVersion')}")
    lamp = Wiz(ip, port=args.port, timeout=5)
    try:
        mac = cfg["mac"]

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


def cmd_diag(args):
    """One-shot evidence collector for an AP pairing window (run while the
    host is connected to WiZConfig_*): probes candidate AP IPs x ports
    with a getSystemConfig payload and reports every response."""
    ips = AP_IP_CANDIDATES if args.ap_ip == "auto" else [args.ap_ip]
    payload = json.dumps({"method": "getSystemConfig", "params": {}, "id": 1}).encode()
    print(f"probing {len(ips)} ip(s) x {len(AP_PORTS)} ports "
          f"({len(ips) * len(AP_PORTS) * 1.2:.0f}s worst case) ...")
    found = []
    for ip in ips:
        for port in AP_PORTS:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.2)
            s.sendto(payload, (ip, port))
            try:
                raw, _ = s.recvfrom(4096)
                print(f"RESPONSE {ip}:{port} <- {raw.decode('utf-8', 'replace')[:200]}")
                found.append((ip, port))
            except (BlockingIOError, TimeoutError):
                pass
            finally:
                s.close()
    if found:
        print(f"\nreachable: {found}")
        print(f"next: python3 pair.py send <ssid> <password> --ap-ip {found[0][0]}")
        return 0
    print("\nno UDP responses on any candidate port.")
    print("next: run  python3 ap-sweep.py  (full 1-65535 sweep + mDNS browse)")
    print("to see whether the AP answers anything at all, and on which port.")
    return 1


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
                   help="bulb's hotspot IP, or 'auto' to probe "
                        + "/".join(AP_IP_CANDIDATES) + " (default auto)")
    s.add_argument("--port", type=int, default=PORT, help=f"bulb's UDP port (default {PORT})")
    s.add_argument("--raw-params", help="raw JSON params to try instead of the built-in variants")
    s.set_defaults(func=cmd_send)

    f = sub.add_parser("find", help="discover bulbs on this machine's LAN (UDP broadcast)")
    f.add_argument("--seconds", type=float, default=10)
    f.set_defaults(func=cmd_find)

    c = sub.add_parser("scan", help="unicast-sweep a range for bulbs (through NAT)")
    c.add_argument("range", metavar="RANGE",
                   help="CIDR (192.168.0.0/24), a.b.c.d-e (192.168.0.100-150) or a single IP")
    c.set_defaults(func=cmd_scan)

    d = sub.add_parser("diag", help="one-shot AP diagnostics: probe AP IPs x ports (run while on WiZConfig_*)")
    d.add_argument("--ap-ip", default=AP_IP_DEFAULT,
                   help="AP IP to probe, or 'auto' for the known candidates (default auto)")
    d.set_defaults(func=cmd_diag)

    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
