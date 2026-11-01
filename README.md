# Wiz lamp — direct LAN control (no app)

Control a WiZ colour bulb straight over the LAN, by IP (`192.168.x.x`),
bypassing the flaky Wiz app and the cloud entirely. The bulb tested here
(MAC `cc4085:xx:xx:xx`, module `ESP25_SHRGB_01` = full colour, fw 1.38.0)
speaks the same protocol the official app uses: plain JSON over
**UDP port 38899**, no encryption, no framing.

## Files

| File           | What it is                                                    |
|----------------|---------------------------------------------------------------|
| `wiz.py`       | The client: a CLI **and** an importable Python library       |
| `pair.py`      | Pair a bulb to Wi-Fi **without the app** (AP pairing mode)   |
| `halloween.py` | Example app: green "lightning" flicker, uses `wiz.py` as lib |

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
> entered AP mode fine, but the first `send` attempt failed: from this
> WSL2 VM, the bulb's hotspot IP was **unreachable** (even a read-only
> `getSystemConfig` to `192.168.4.1:38899` timed out while the Windows
> host was connected to the `WiZConfig_*` AP). So the `setWifiConfig`
> step was never actually exercised against a real bulb. See
> "Where it stopped" and "Next steps" at the end of this section.
> **The bulb is left unpaired until one of the recovery paths is done.**

The app's normal pairing hands Wi-Fi credentials to the bulb over
**Bluetooth** — but WiZ also has a fully-UDP fallback: **AP pairing mode**
(official name: "manual pairing"). `pair.py` automates that path.

How AP mode works (from WiZ's own docs):

- A bulb in pairing mode that isn't paired in time **double-pulses
  purple** (RGB models) and broadcasts a Wi-Fi network named
  **`WiZConfig_xxxx`** (the suffix relates to the bulb's MAC).
- Anything that connects to that network can reach the bulb at its
  soft-AP address **192.168.4.1** (ESP default) and send it the home
  credentials via UDP `setWifiConfig`.
- On success the hotspot disappears and the bulb joins your network on
  its own. (WiZ's support docs: "this step seldom fails".)

### Procedure

1. **Pairing mode**: wall switch **OFF 10 s**, then **ON-OFF-ON-OFF-ON**
   (3 clicks, 1–2 s apart). Bulb pulses blue (ready to pair).
   Wait up to a minute — if unpaired it drops to AP mode
   (**double purple pulse**, `WiZConfig_xxxx` appears in your Wi-Fi list).
2. **Connect a Wi-Fi radio to `WiZConfig_xxxx`**: ideally a Linux laptop
   (cleanest path). A Windows host also works *if* you then run the script
   on the host itself — note the WSL2 VM did **not** reach the AP through
   the host's NAT in testing (see "Where it stopped").
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
python3 pair.py send <ssid> <password> [--ap-ip 192.168.4.1] [--raw-params '{...}']
python3 pair.py find [--seconds 10]
python3 pair.py scan <CIDR|a.b.c.d-e|IP>
```

- `send` — run while connected to the bulb's `WiZConfig_*` network.
  Prints the bulb's MAC/model/firmware from the hotspot first, then tries
  credential variants (`ssid`+`password`, `ssid`+`psk`, nested `wifi:{...}`)
  until one is accepted.
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
| Windows host joined the `WiZConfig_*` AP | ✅ worked |
| WSL2 VM → bulb hotspot IP `192.168.4.1:38899` | ❌ timeout (read-only probe) |
| `pair.py send` (setWifiConfig) | ⬜ never reached a live bulb |

Open questions, in order of suspicion:

1. **Can the Windows host itself reach `192.168.4.1`?** If yes, the
   blocker is the WSL2 NAT / Windows firewall, not the protocol.
2. **Is `192.168.4.1` actually the bulb's AP IP?** It's the ESP soft-AP
   default but unverified — check `ipconfig` on the host while connected
   to `WiZConfig_*` (the host gets `192.168.4.x`, the bulb is likely `.1`).
3. **`setWifiConfig` param names** — undocumented; `pair.py send` tries
   4 variants. Untested against a live bulb.

### Next steps to resume (cheapest first)

1. **From the Windows host** (PowerShell, while on the `WiZConfig_*` AP):
   ```powershell
   Test-NetConnection 192.168.4.1 -Port 38899
   ipconfig   # confirm the host's address on that network
   ```
   - If reachable from the host: the WSL2 VM is the problem → use WSL2
     **mirrored networking** (Windows 11: ` networkingMode=mirrored ` in
     `%UserProfile%\.wslconfig`), or add a Windows Firewall rule allowing
     UDP from the WSL vNIC, or just run `pair.py send` **on the host**
     (needs Python installed there).
2. **Cleanest option:** run `pair.py send` from a **Linux laptop**
   connected to the `WiZConfig_*` AP — no NAT in the path at all.
3. Once `send` is accepted, the bulb leaves the AP: reconnect to home
   Wi-Fi and run `pair.py scan 192.168.0.0/24` (from the VM) to get its
   new DHCP address.

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
