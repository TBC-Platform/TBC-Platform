# Smart home control without exposing your home network

A voice assistant that can control your house is a device that turns *sounds in
a room* into *actions in your home*. Treat it accordingly.

This document is the reasoning; the enforcement is in
[`server/walle/smarthome/allowlist.py`](../server/walle/smarthome/allowlist.py)
and the tests in
[`server/tests/test_allowlist.py`](../server/tests/test_allowlist.py).

Smart home control is **off by default**. You have to turn it on deliberately.

---

## The threat model

Being specific about what you are defending against, in rough order of how
likely each one is:

| # | Threat | Likelihood | This design's answer |
|---|---|---|---|
| 1 | **Speech recognition mishears you** | Certain — it will happen | Hard deny list; nothing dangerous is voice-controllable at all |
| 2 | **Someone else in the room says something** | Likely | Same; plus the allowlist keeps the scope small |
| 3 | **The TV says something** | Likely | Wake word confidence, and the same limits |
| 4 | **A guest deliberately messes with it** | Plausible | Physical presence already implies a lot of access; limits still apply |
| 5 | **Something on your LAN probes the server** | Plausible | Shared-secret auth on the WebSocket |
| 6 | **The server itself is compromised** | Unlikely | Least-privilege HA token: the blast radius is the lamps |
| 7 | **Attack from the internet** | **Structurally impossible** | Nothing listens on the internet. No port is forwarded. |

Threat 1 is the one people underestimate and the one that actually happens. Most
of the design is about it.

---

## Rule 1 — Never open a port

Every connection in this system is **outbound**:

```
robot  ──connects to──►  server  ──connects to──►  Home Assistant
                                 ──connects to──►  MQTT broker
                                 ──connects to──►  Ollama (localhost)
```

Nothing accepts a connection from the internet. There is no port forward, no
UPnP mapping, no dynamic DNS entry, no cloud relay. An attacker on the internet
has nothing to connect *to*.

The server binds `0.0.0.0` so the robot can reach it across your LAN. That is
the intended exposure and the boundary of it.

### If you want to reach the robot from outside

**Use a VPN.** WireGuard or Tailscale, ten minutes to set up, and your phone is
on your home network with one modern, audited protocol as the attack surface.

**Do not port-forward.** Not the server, not Home Assistant, not "just for
testing". A forwarded port is found by internet-wide scanners within hours —
that is not a hypothetical, it is what those scanners exist to do. Everything
in this repo is written for a LAN and is not hardened against hostile traffic.

**Do not use a reverse proxy to expose it either**, unless you genuinely
understand what you are putting in front of it. A reverse proxy with basic auth
in front of a service that drives motors and streams a microphone is a thinner
defence than it feels like.

---

## Rule 2 — Allowlist, and fail closed

```bash
WALLE_ALLOWED_ENTITIES=light.desk_lamp,light.office_ceiling,switch.coffee
```

Only these entities can ever be touched. Rules that matter:

- **An empty allowlist refuses everything.** Not "allows everything" — the
  common and much worse default. A misconfigured server controls nothing.
- **Exact entity ids are strongly preferred.** Globs (`light.office_*`) work but
  widen the blast radius by however many entities match now *and in future*.
- **Malformed ids are refused**, including anything containing a path
  separator, a brace, a quote or a space. That closes off attempts to smuggle a
  template or an injection through an entity name.
- **The allowlist is checked at the last moment before the network call**, in
  the backend client itself. There is exactly one place to audit and no way to
  reach the network by a different code path.
- **Reads are gated too.** Whether the bedroom light is on is information about
  whether someone is home.

---

## Rule 3 — Some things are never voice-controllable

These domains are refused **unconditionally**. Not configurable, not overridable
by the allowlist, not overridable by `WALLE_ALLOWED_DOMAINS`:

```python
HARD_DENIED_DOMAINS = {
    "lock",                 # front doors
    "cover",                # garage doors (and blinds, same domain)
    "alarm_control_panel",
    "valve",                # water shutoffs
    "water_heater",
    "camera",               # no remote enabling of cameras
    "device_tracker",       # no "where is everyone"
    "person",
    "vacuum",               # can leave its dock and flood a room unattended
}
```

The reasoning is Threat 1. A speech recogniser is a machine that occasionally
mishears. "Unlock the front door" must never be one homophone away from
something you might plausibly say to a toy robot on your desk.

Blinds are collateral damage of Home Assistant putting them in the same domain
as garage doors. That is the right trade.

Beyond domains, the **service** is checked too: `ALLOWED_SERVICES` lists exactly
which services each domain permits. `light.turn_on` is allowed;
`homeassistant.restart` is not reachable at all.

To change any of this you have to edit the source. That is deliberate — at that
point you have made a decision rather than mistyped an environment variable.

---

## Rule 4 — Least-privilege credentials

Do **not** use your own Home Assistant admin token.

1. Settings → People → **Add person**, call it "walle", give it a password, mark
   it **not an administrator**.
2. Log in as that user once, then profile → **Long-lived access tokens** →
   create one.
3. Put that token in `WALLE_HA_TOKEN`.
4. Restrict what that user can see (Home Assistant's entity/area permissions, or
   a filtered dashboard) so it covers only the allowlisted entities.

Now the worst case — someone gets the token — costs you your lamps, not your
whole house.

**MQTT:** create a dedicated broker user with publish rights only on
`walle/#`. Mosquitto ACLs do this in three lines. MQTT has no per-topic
permission model by default in most home setups, which is exactly why the
server-side allowlist is enforced identically for the MQTT backend.

---

## The rest of the setup

### Authenticate the robot

`WALLE_AUTH_TOKEN` is a shared secret presented in the `X-Walle-Token` header on
every connection, compared with `secrets.compare_digest`. Generate it properly:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put the same value in `firmware/include/secrets.h`. The server refuses to start
without one unless you explicitly set `WALLE_ALLOW_NO_AUTH=1`.

This is not strong authentication — it is a bearer token on a LAN. It stops
casual probing and a device on your guest VLAN from driving your robot. It is
not a substitute for Rules 1–4.

### Put it on a separate VLAN if you can

If your router supports VLANs or a guest network with client isolation, the
robot belongs there, with a firewall rule permitting only the server's port. IoT
devices are the least trustworthy things on a home network and this one has a
microphone.

### Encrypt if the LAN is not yours

On a shared or student network, set `WALLE_SERVER_USE_TLS=1` and put the server
behind a TLS terminator with a self-signed certificate. On your own home
network, plain `ws://` is a reasonable trade — the token is the control, and
anyone already inside your LAN has bigger opportunities than your robot.

### Audit everything

Every attempted home action — allowed *and* refused — is written to the local
SQLite database:

```bash
curl -s http://127.0.0.1:8765/events | python3 -m json.tool
```

```json
{"device": "walle-01", "kind": "smarthome",
 "detail": "refused: lock.front_door turn_on (domain 'lock' can never be controlled by voice)"}
```

"What did the robot actually do at 2 a.m." should be an answerable question.

---

## What about privacy?

Separate from security, and worth being explicit about, because a
microphone-equipped robot deserves a straight answer:

| Data | Where it goes |
|---|---|
| Audio from the microphone | **Never leaves your LAN.** Device → your server, transcribed there, discarded. |
| Camera frames | **Never leave your LAN.** Object detection runs on your server. |
| Conversation history | Local SQLite file (`server/data/walle.db`). Delete it whenever you like. |
| Remembered facts | Same file. "Wall-E, forget everything" wipes them. |
| Transcribed text | Stays local with `WALLE_LLM_BACKEND=ollama`. **Sent to the provider** with `WALLE_LLM_BACKEND=openai` — text only, never audio, never images. |

The default configuration sends nothing anywhere. Choosing a cloud LLM is the
one point where that changes, it is a single environment variable, and it is
worth knowing that is what you are choosing.

---

## Checklist before you enable it

- [ ] `WALLE_AUTH_TOKEN` set to something from a random generator, matching the firmware
- [ ] No port forwarded to the server or to Home Assistant
- [ ] `WALLE_ALLOWED_ENTITIES` lists exact entity ids, not `*`
- [ ] Home Assistant token belongs to a dedicated non-admin user
- [ ] Nothing in the allowlist that would be bad to trigger by accident
- [ ] You have looked at `GET /events` once and know where the audit log lives
- [ ] Robot on an IoT VLAN, if your router can do it
