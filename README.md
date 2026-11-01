# Wiz lamp — direct LAN control (no app)

Control a WiZ colour bulb straight over the LAN, by IP (`192.168.x.x`),
bypassing the flaky Wiz app and the cloud entirely. The bulb tested here
(MAC `cc4085:xx:xx:xx`, module `ESP25_SHRGB_01` = full colour, fw 1.38.0)
speaks the same protocol the official app uses: plain JSON over
**UDP port 38899**, no encryption, no framing.

## Files

| File              | What it is                                                    |
|-------------------|---------------------------------------------------------------|
| `wiz.py`          | The client: a CLI **and** an importable Python library       |
| `pair.py`         | Pair a bulb to Wi-Fi **without the app** (AP pairing mode)   |
| `ap-udp-test.ps1` | Host-side (Windows) UDP control test for the AP window       |
| `halloween.py`    | Example app: green "lightning" flicker, uses `wiz.py` as lib |

---

## 1. Using the CLI (standalone)

> **Note:** no default bulb IP is set — pass `--host <ip>` on every
> command, or edit `DEFAULT_HOST` in `wiz.py` to your bulb's address.

```bash
# Device info + current state (JSON on stdout)
python3 wiz.py --host 192.168.x.x info

# Power
python3 wiz.py --host 192.168.x.x on
python3 wiz.py --host 192.168.x.x off

# Brightness 10-100 (also turns the bulb on, keeps colour)
python3 wiz.py --host 192.168.x.x dim 50

# Colour: name or #rrggbb, optional dimming (also turns on)
python3 wiz.py --host 192.168.x.x color blue
python3 wiz.py --host 192.168.x.x color purple 60
python3 wiz.py --host 192.168.x.x color "#ff8800" 80
#   names: red orange yellow green cyan blue purple pink white

# Colour temperature in kelvin (2200-6500), optional dimming
python3 wiz.py --host 192.168.x.x temp 2700 80
```

Behaviour:

- `info` prints a JSON object (`{"device": {...}, "state": {...}}`).
- Every other command prints `ok (ip)` on success.
- Exit code `0` = success, `1` = error or bulb offline (message on stderr).
- `color` / `temp` / `dim` implicitly turn the bulb **on**.
- Brightness is clamped to the bulb's 10–100 range; kelvin to 2200–6500.

---

## 2. Using `wiz.py` as a library

```python
from wiz import Wiz, to_rgb

lamp = Wiz("192.168.x.x")          # your bulb's IP (no default is set)
lamp.state()                       # verify reachable + cache the bulb MAC
lamp.color_hex("#4a8c00", 60)      # colour by name or #rrggbb + dimming
lamp.color(74, 140, 0, 40)         # r, g, b (0-255) + dimming
lamp.white(2700, 80)               # colour temperature (kelvin) + dimming
lamp.dim(40)                       # brightness only, keep colour
lamp.on()                          # keep current settings
lamp.off()
lamp.close()                       # or use it as a context manager:
                                   #   with Wiz() as lamp: ...
```

### Methods

| Method | What it does |
|---|---|
| `Wiz(host=DEFAULT_HOST, timeout=3.0)` | Open a UDP socket to the bulb |
| `state()` | Current state dict (`state`, `dimming`, `temp` and/or `r,g,b,c,w`); also caches the MAC |
| `info()` | Device dict: `mac`, `moduleName`, `fwVersion`, `homeId`, `roomId`, `rssi`, … |
| `set(**kw)` | Raw `setPilot`, waits for response. Kwargs: `state, dimming, temp, r, g, b, c, w` |
| `on(**kw)` / `off()` | `set(state=True/False, …)` |
| `dim(level)` | Brightness 10–100, keep colour |
| `color(r, g, b, dimming=100)` | RGB colour, 0–255 per channel |
| `color_hex(spec, dimming=100)` | Colour name (see `NAMED_COLORS`) or `#rrggbb` |
| `white(kelvin=4000, dimming=100)` | Colour temperature, 2200–6500 K |
| `fire_set(**kw)` / `fire_off()` | Like `set`/`off` but **fire-and-forget** — for fast sequences |
| `fire(method, params)` / `call(method, params)` | Raw protocol send, without / with waiting |
| `drain()` | Discard pending responses (e.g. after a burst of `fire_set`) |
| `close()` | Close the socket (also called by the context manager) |

### Exceptions & reliability

- `RuntimeError` — the bulb answered with an error (bad method/params).
- `TimeoutError` (subclass of `OSError`) — no response in time. `call()`
  automatically **retries 3×** (1.5 s per attempt): reply datagrams are
  occasionally lost in transit (seen over WSL2's NAT) even though the
  command *was* applied. All commands are full-state sets, so re-sending
  is safe and idempotent.
- The bulb's MAC is fetched lazily (first `state()`/`set()`); call
  `state()` up front if you want a fast, explicit "is it alive?" check.

### Fast sequences (fire-and-forget)

Waiting for a response on every update costs a full round trip
(~100 ms+ each). For animations, fire commands without waiting — the
bulb applies them in the order received:

```python
import time
from wiz import Wiz

with Wiz() as lamp:
    lamp.state()                      # once: alive check + cache MAC
    for level in range(10, 101, 5):   # slow ramp up
        lamp.fire_set(state=True, r=74, g=140, b=0, dimming=level)
        time.sleep(0.05)
    lamp.off()                        # final waiting call (id-matched)
```

Stale replies from the fire-and-forget calls pile up in the socket
buffer, but responses are matched by the JSON-RPC `id` field the bulb
echoes back, so the next waiting call (`off()` here) is never fooled by
an old reply. `drain()` clears the queue if you want.

---

## 3. Example: `halloween.py`

Green "lightning": the bulb sits at a 10 % baseline, then random flashes
up to 80 % — 50 % chance of a double strike, 20 % chance of a triple —
each decaying fast, with irregular gaps (exponential, mean ~0.6 s).
Uses `fire_set` for speed; always ends with the bulb **off**.

```bash
./halloween.py 192.168.x.x            # until Ctrl+C
./halloween.py 192.168.x.x --duration 300   # 5 minutes, then off
./halloween.py 192.168.x.x --min 10 --max 100   # brighter, more violent flashes
./halloween.py 192.168.x.x --mode pulse --min 15 --max 35   # old "breathing"
./halloween.py 192.168.x.x --color "#0a3d0a"      # darker green
```

`IP` (first positional) = the bulb's address — required, since the
built-in default is the `192.168.x.x` placeholder (edit `DEFAULT_HOST`
in `wiz.py` to make it your own). Other options: `--color NAME|#hex`,
`--mode flicker|pulse`, `--min/--max` (flicker: baseline/flash ceiling;
pulse: floor/ceiling), `--period` (pulse cycle, s), `--step` (pulse
update rate, s), `--duration` (seconds; 0 = run until Ctrl+C).

---

## 4. Pairing a bulb (no app) — **WORK IN PROGRESS**

> **Status (tested 2026-11-01):** not yet proven end-to-end. The bulb
> entered AP mode fine and its AP IP is confirmed (**192.168.56.1**,
> the gateway on the `WiZConfig_*` network; the host's Wi-Fi joins it
> and gets `192.168.56.100` from the bulb's DHCP). Traffic from the
> WSL2 VM reaches the AP (ping works), and — importantly — the bulb's
> AP mode **does not answer UDP probes on any of 8 candidate ports
> from either the VM or the Windows host directly** (no NAT involved).
> So the WSL2-NAT hypothesis is ruled out: the AP-mode control server
> is either on a port we haven't guessed, or it isn't listening for
> plain UDP at all. Next: the full sweep in "Next steps". **The bulb
> is left unpaired until one of the recovery paths is done.**

The app's normal pairing hands Wi-Fi credentials to the bulb over
**Bluetooth** — but WiZ also has a fully-UDP fallback: **AP pairing mode**
(official name: "manual pairing"). `pair.py` automates that path.

How AP mode works (from WiZ's own docs):

- A bulb in pairing mode that isn't paired in time **double-pulses
  purple** (RGB models) and broadcasts a Wi-Fi network named
  **`WiZConfig_xxxx`** (the suffix relates to the bulb's MAC).
- Anything that connects to that network can reach the bulb at its
  soft-AP address and send it the home credentials via UDP
  `setWifiConfig`. Observed on this bulb: network **192.168.56.0/24**,
  bulb at **192.168.56.1** (the gateway clients see on that network).
- On success the hotspot disappears and the bulb joins your network on
  its own. (WiZ's support docs: "this step seldom fails".)

### Procedure

1. **Pairing mode**: wall switch **OFF 10 s**, then **ON-OFF-ON-OFF-ON**
   (3 clicks, 1–2 s apart). Bulb pulses blue (ready to pair).
   Wait up to a minute — if unpaired it drops to AP mode
   (**double purple pulse**, `WiZConfig_xxxx` appears in your Wi-Fi list).
2. **Connect a Wi-Fi radio to `WiZConfig_xxxx`**: a Linux laptop is
   the cleanest path; the Windows host works too, and the WSL2 VM on
   it reaches the AP fine as well (ICMP and outbound UDP both get
   through — see "Where it stopped").
3. **Send the credentials** (from the VM, or from the laptop):
   ```bash
   python3 pair.py send "MyHomeWifi" "MyPassword"
   ```
   The exact `setWifiConfig` parameter names are undocumented, so the
   script tries a few variants in order. The bulb stays in AP mode until
   pairing succeeds, so just re-run if a variant is rejected.
4. **Back to home Wi-Fi, then find the bulb's new DHCP address**:
   ```bash
   python3 pair.py scan 192.168.0.0/24    # unicast sweep (works from WSL2)
   python3 pair.py find                   # L2 broadcast (laptop on the LAN)
   ```
   (Best long-term: give the bulb's MAC a DHCP reservation on the router.)

### `pair.py` reference

```bash
python3 pair.py send <ssid> <password> [--ap-ip auto|IP] [--port 38899] [--raw-params '{...}']
python3 pair.py find [--seconds 10]
python3 pair.py scan <CIDR|a.b.c.d-e|IP>
python3 pair.py diag [--ap-ip auto|IP]
python3 ap-sweep.py [--ip 192.168.56.1] [--no-mdns]
```

- `send` — run while connected to the bulb's `WiZConfig_*` network.
  `--ap-ip auto` (default) probes `192.168.56.1` then `192.168.4.1`;
  `--port` overrides the UDP port (default 38899) if `ap-sweep.py`
  finds the AP-mode server elsewhere. Prints the bulb's
  MAC/model/firmware from the hotspot first, then tries credential
  variants (`ssid`+`password`, `ssid`+`psk`, nested `wifi:{...}`) until
  one is accepted.
- `diag` — quick probe of 2 IPs × 8 candidate ports (~20 s); reports
  any response. Superseded by `ap-sweep.py` for the deep dive.
- `ap-sweep.py` — the deep dive: a full 1–65535 UDP sweep with 5
  read-only probe payloads (~35 s). Any port that answers is reported
  (replies are attributed by source port). No `setWifiConfig` is ever
  sent, so it can't disturb the bulb's state. Runs on system
  `python3` (no dependencies). The optional mDNS browse is **unproven**
  (needs a venv with `zeroconf`); use `--no-mdns` — the sweep is the
  main event.
- `find` — UDP broadcast of `getSystemConfig` on this machine's LAN.
- `scan` — parallel unicast `getSystemConfig` sweep of an address range
  (this is what finds the bulb's new IP from WSL2).

Caveats: 2.4 GHz b/g/n, WPA2/WPA3-Personal with a password (WiZ
requirement); the app remains the fallback if `setWifiConfig` turns out
not to be implemented on a particular firmware.

### Where it stopped (2026-11-01)

| Step | Result |
|------|--------|
| Bulb → AP mode (double purple pulse, `WiZConfig_*` SSID) | ✅ worked |
| Windows host joined the `WiZConfig_*` AP | ✅ worked (got `192.168.56.100`, gateway `192.168.56.1`) |
| Bulb's AP IP identified | ✅ `192.168.56.1` |
| WSL2 VM → `192.168.56.1` (ICMP ping) | ✅ works — WSL2 NAT *does* forward the VM's traffic to the AP |
| `pair.py diag` (VM): 2 IPs × 8 candidate ports | ❌ no response anywhere |
| `ap-udp-test.ps1` (host, **direct L2, no NAT**): 8 candidate ports | ❌ no response on any port |
| `pair.py send` (setWifiConfig) | ⬜ never reached a live bulb |

**Key deduction:** the host sits directly on the bulb's Wi-Fi (same L2
segment, gateway = the bulb). It gets *no* UDP replies either, so the
WSL2-NAT hypothesis is **ruled out**. The AP-mode firmware simply does
not answer our probes on the known IoT ports.

Open questions, in order of suspicion:

1. **AP mode listens on a port we haven't guessed** → `ap-sweep.py`
   settles it in one ~50 s run (full 1–65535 sweep + mDNS).
2. **The AP-mode UDP server only answers after a handshake** our
   probes don't match (e.g. a `registration` exchange first — the
   sweep includes a registration probe, but a full handshake may be
   needed).
3. **AP mode has no UDP server at all** and the credential hand-off
   happens over another channel (e.g. the phone still uses Bluetooth
   even while sitting on the AP) → in that case a pure-script pair
   needs the BLE side, or one app-assisted pairing.

### Next steps to resume (one AP window, one command)

1. **Bulb in AP mode** (double purple pulse), **host on `WiZConfig_*`**.
2. **VM:** `python3 ap-sweep.py --no-mdns` — ~35 s: full 1–65535 UDP
   sweep on `192.168.56.1` with 5 read-only probes per port
   (getSystemConfig, getPilot, ping, registration, malformed frame).
   No dependencies; system `python3` is fine.
3. **Interpret:**
   - A port answers → in the same window run
     `python3 pair.py send "<home-ssid>" "<home-password>" --ap-ip 192.168.56.1 --port <port>`
     (the bulb stays in AP mode until pairing succeeds, so you have the
     full minute).
   - mDNS announces a service → its type/ports go straight into
     `--port`.
   - Nothing answers at all → hypothesis 2/3 above: the hand-off isn't
     a plain UDP server. Then either capture the official app's traffic
     on a phone while it pairs on the AP, or do the one-time app
     pairing and move on — everything after that is `wiz.py`.
4. Once `send` is accepted, the bulb leaves the AP: reconnect to home
   Wi-Fi and run `pair.py scan` over the home range (from the VM) to get
   its new DHCP address.

### Recovering the bulb (it's currently unpaired)

The bulb is not on the home network until pairing completes:
`wiz.py` / `halloween.py` can't reach it. Options:

- Retry the pairing steps above from a machine that *can* reach the AP,
  or
- Use the official app once as a fallback (it's flaky, but it works),
  after which everything is again controlled via `wiz.py` locally.

---

## Protocol (how it works)

JSON over **UDP port 38899**, one request datagram → one reply datagram:

```
→ {"method":"getPilot","params":{},"id":42}
← {"method":"getPilot","id":42,"env":"pro",
   "result":{"mac":"a1b2c3d4e5f6","rssi":-52,"state":true,"temp":4200,"dimming":100}}
```

- `getSystemConfig` → mac, model, firmware, home/room ids
- `getPilot` → current state (on/off, dimming, `temp` and/or `r,g,b,c,w`)
- `setPilot` → change state; params: `mac`, `src:""`, `state`,
  `dimming` (10–100), `temp` (kelvin) **or** `r,g,b,c,w`
  (0–255; `c` = cool white, `w` = warm white)
- Errors come back JSON-RPC style: `{"method":…,"error":{"code":-32601,"message":"Method not found"}}`
- `wiz.py` adds a monotonically increasing `id` per request; the bulb
  echoes it, which is how replies are matched (and stale ones skipped).

## Gotchas

- **WSL2**: works as-is — UDP passes through the NAT transparently,
  no mirrored networking needed.
- **Power**: an unpowered bulb drops off the network entirely
  (ping *and* UDP go silent). Check the wall switch before debugging.
- **DHCP**: if the bulb's IP changes, pass `--host` / `Wiz("<ip>")`;
  a DHCP reservation on the router makes this permanent.
- **Setup**: the official app is only needed for first-time Wi-Fi
  pairing; everything after that is local.
- **Signal**: ~−52 dBm at the bulb — healthy.
