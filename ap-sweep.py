#!/usr/bin/env python3
"""One-shot AP-mode discovery: full UDP sweep + mDNS browse.

Run while the Windows host is connected to the bulb's WiZConfig_* AP
(same window as the pairing attempt). Sends a battery of READ-ONLY
probes to every port 1-65535 on the bulb's AP IP, in gentle chunks,
and reports any port that answers (replies are attributed by source
port). Also browses mDNS/DNS-SD for service announcements.

Probe payloads (all read-only — nothing that changes bulb state):
  1. getSystemConfig   2. getPilot   3. ping
  4. registration      5. a malformed binary frame (Wiz JSON-RPC ports
     (declare-only)     answer it with a "Parse error")

~50 seconds total. No network access needed (runs fine on the AP,
which has no internet).

Usage:
  python3 ap-sweep.py                 # sweep 192.168.56.1 + mDNS
  python3 ap-sweep.py --ip 192.168.4.1 --no-mdns
"""
import argparse
import json
import socket
import sys
import time

# All read-only. If a port runs the Wiz JSON-RPC server it will answer
# at least one of these (with a result or an error).
PAYLOADS = [
    json.dumps({"method": "getSystemConfig", "params": {}, "id": 1}).encode(),
    json.dumps({"method": "getPilot", "params": {}, "id": 2}).encode(),
    json.dumps({"method": "ping", "params": {}, "id": 3}).encode(),
    json.dumps({"method": "registration", "params": {
        "phoneIp": "192.168.56.100", "phoneMac": "aabbccddee11", "register": True}}).encode(),
    bytes([0xF8, 0x1A, 0x00, 0x00, 0x15, 0x00]) + b'{"method":"get_state"}',
]


def mdns_browse(seconds):
    try:
        import zeroconf
    except ImportError:
        print("mDNS: zeroconf not importable in this interpreter — skipping")
        print("      (install: python3 -m venv venv && ./venv/bin/pip install zeroconf,)")
        print("       then run this script with ./venv/bin/python)")
        return

    found_types = set()        # e.g. "_wiz._udp.local"
    found_instances = {}       # (type, name) -> True

    # Classic ServiceBrowser path: actively QUERIES the network (a passive
    # listener would only catch spontaneous announcements, which the bulb
    # is unlikely to make). Works on zeroconf 0.153 and older.
    class Collector(zeroconf.ServiceListener):
        def add_service(self, zc, type_, name):
            if type_.startswith("_services"):
                # name is a service type, e.g. "_wiz._udp.local" — browse it too
                found_types.add(name)
                try:
                    zc.add_service_listener(name + ("." if not name.endswith(".") else ""), self)
                except Exception:
                    pass
            else:
                found_instances[(type_, name)] = True

        def remove_service(self, zc, type_, name):
            pass

    zc = zeroconf.Zeroconf()
    zc.add_service_listener("_services._udp.local.", Collector())
    zc.add_service_listener("_services._tcp.local.", Collector())

    print(f"mDNS: browsing for {seconds}s ...")
    time.sleep(seconds)
    try:
        print(f"mDNS: service types seen: {sorted(found_types) or 'none'}")
        for stype, iname in sorted(found_instances):
            print(f"mDNS: instance {iname} (type {stype})")
            try:
                info = zc.get_service_info(stype, iname, timeout=1500)
                if info:
                    print(f"   server={info.server} "
                          f"ports={info.port if info.port else '?'} "
                          f"addresses={info.parsed_addresses()}")
                    if info.properties:
                        print(f"   properties={dict(info.properties)}")
            except Exception as e:
                print(f"   (info lookup failed: {e})")
        if not found_types and not found_instances:
            print("mDNS: no services announced")
    finally:
        zc.close()


def sweep(ip, chunk=1024, pause=0.35, max_port=65535):
    print(f"UDP sweep: {ip} ports 1-{max_port}, {len(PAYLOADS)} probes/port, "
          f"chunks of {chunk} with {pause}s settle ...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    sock.settimeout(0.1)
    found = {}
    t_start = time.monotonic()
    try:
        for start in range(1, max_port + 1, chunk):
            end = min(start + chunk, max_port + 1)
            for port in range(start, end):
                for p in PAYLOADS:
                    sock.sendto(p, (ip, port))
            # settle: collect any replies; reply source port = the port we hit
            t0 = time.monotonic()
            while time.monotonic() - t0 < pause:
                try:
                    raw, addr = sock.recvfrom(4096)
                    port = addr[1]
                    if port not in found:
                        found[port] = raw[:200]
                        print(f"RESPONSE port {port} <- {raw[:200]!r}", flush=True)
                except (BlockingIOError, TimeoutError):
                    time.sleep(0.005)
            done = end - 1
            elapsed = time.monotonic() - t_start
            eta = elapsed / done * max_port - elapsed
            print(f"  ...{done}/{max_port} ({elapsed:.0f}s elapsed, ~{eta:.0f}s to go)", flush=True)
    finally:
        sock.close()

    if found:
        print(f"\nresponding ports: {sorted(found)}")
        for port, raw in sorted(found.items()):
            print(f"  port {port}: {raw!r}")
        print(f"\nnext: try  python3 pair.py send <ssid> <pw> --ap-ip {ip} --port <port>")
        return 0
    print(f"\nno port on 1-{max_port} answered any probe.")
    print("If the official app CAN pair while on this AP, the hand-off")
    print("happens over a channel we're not probing (e.g. Bluetooth),")
    print("or only after a handshake none of our probes match.")
    return 1


def main():
    ap = argparse.ArgumentParser(description="One-shot AP-mode discovery (sweep + mDNS).")
    ap.add_argument("--ip", default="192.168.56.1", help="bulb AP IP (default 192.168.56.1)")
    ap.add_argument("--no-mdns", action="store_true", help="skip the mDNS browse")
    ap.add_argument("--mdns-seconds", type=float, default=10)
    ap.add_argument("--max-port", type=int, default=65535, help="sweep ports 1-N (default 65535)")
    args = ap.parse_args()

    rc = 1
    if not args.no_mdns:
        mdns_browse(args.mdns_seconds)
    rc = min(rc, sweep(args.ip, max_port=args.max_port))
    sys.exit(rc)


if __name__ == "__main__":
    main()
