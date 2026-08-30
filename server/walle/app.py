# SPDX-License-Identifier: MIT
"""FastAPI application: one WebSocket endpoint, plus a little diagnostics.

Binding notes, because they are a security decision and not a detail:

* The server binds ``0.0.0.0`` so the robot can reach it across the LAN.
* It must **never** be port-forwarded to the internet. Nothing here is designed
  to survive hostile traffic, and it can drive motors and read a microphone.
  If you want access from outside, use a VPN (WireGuard/Tailscale) - see
  docs/06-smart-home-security.md.
* Every connection presents a shared token before it can do anything.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from . import protocol as proto
from .config import Config
from .llm import build_llm
from .logging_setup import setup_logging
from .memory import MemoryStore
from .session import Brain, Session
from .smarthome import build_smarthome
from .stt import build_stt
from .tools import DeviceRegistry
from .tts import build_tts
from .vision import build_vision

log = logging.getLogger(__name__)


def _authorised(config: Config, presented: str | None) -> bool:
    """Constant-time token check.

    ``secrets.compare_digest`` rather than ``==`` so the comparison does not
    leak the token's length or prefix through timing. Overkill on a LAN;
    free to do correctly.
    """
    expected = config.server.auth_token
    if not expected:
        return config.server.allow_no_auth
    if not presented:
        return False
    return secrets.compare_digest(presented, expected)


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(config.server.log_level)
        log.info("Wall-E server starting")

        for problem in config.validate():
            log.warning("config: %s", problem)

        data_dir = config.server.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)

        devices_path = data_dir / "devices.json"
        if not devices_path.exists():
            DeviceRegistry.write_example(devices_path)
            log.info("wrote an example device list to %s", devices_path)

        memory = MemoryStore(data_dir / "walle.db")
        await memory.open()

        brain = Brain(
            config=config,
            stt=build_stt(config.stt),
            tts=build_tts(config.tts),
            llm=build_llm(config.llm),
            vision=build_vision(config.vision),
            memory=memory,
            devices=DeviceRegistry.load(devices_path),
            smarthome=build_smarthome(config.smarthome),
        )
        app.state.brain = brain
        app.state.sessions = {}

        # Warm the models up in the background so the server starts accepting
        # connections immediately, and the first question is still fast.
        async def warmup() -> None:
            await asyncio.gather(
                brain.stt.warmup(), brain.tts.warmup(), brain.llm.warmup(),
                return_exceptions=True,
            )
            log.info("models warm, ready for the first question")

        warm_task = asyncio.create_task(warmup())

        log.info(
            "listening on ws://%s:%s%s  (stt=%s tts=%s llm=%s vision=%s smarthome=%s)",
            config.server.host, config.server.port, "/ws",
            brain.stt.name, brain.tts.name, brain.llm.name,
            brain.vision.name, brain.smarthome.name if brain.smarthome else "off",
        )

        try:
            yield
        finally:
            warm_task.cancel()
            for session in list(app.state.sessions.values()):
                await session.close()
            for closer in (brain.stt, brain.tts, brain.llm, brain.smarthome, memory):
                if closer is not None and hasattr(closer, "close"):
                    try:
                        await closer.close()
                    except Exception:  # pragma: no cover - shutdown must not raise
                        log.debug("error closing %s", type(closer).__name__, exc_info=True)
            log.info("Wall-E server stopped")

    app = FastAPI(title="Wall-E brain", version="1.0.0", lifespan=lifespan)

    # ------------------------------------------------------------------
    # Diagnostics. Read-only, and deliberately boring.
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> JSONResponse:
        brain: Brain = app.state.brain
        return JSONResponse(
            {
                "ok": True,
                "protocol": proto.PROTO_VERSION,
                "engines": {
                    "stt": brain.stt.name,
                    "tts": brain.tts.name,
                    "llm": brain.llm.name,
                    "vision": brain.vision.name,
                    "smarthome": brain.smarthome.name if brain.smarthome else None,
                },
                "devices_online": sorted(app.state.sessions),
                "known_device_names": brain.devices.known_names,
            }
        )

    @app.get("/events")
    async def events(limit: int = Query(50, ge=1, le=500)) -> JSONResponse:
        """Audit log: every smart home action the robot has taken."""
        brain: Brain = app.state.brain
        return JSONResponse({"events": await brain.memory.recent_events(limit)})

    @app.get("/history")
    async def history(device: str = Query("walle-01"),
                      limit: int = Query(20, ge=1, le=200)) -> JSONResponse:
        brain: Brain = app.state.brain
        turns = await brain.memory.recent_turns(device, limit)
        return JSONResponse(
            {"device": device,
             "turns": [{"user": t.user_text, "robot": t.robot_text, "ts": t.ts} for t in turns]}
        )

    # ------------------------------------------------------------------
    # The robot's socket
    # ------------------------------------------------------------------

    @app.websocket("/ws")
    async def robot_socket(websocket: WebSocket, token: str = Query(default="")) -> None:
        presented = websocket.headers.get("x-walle-token") or token or None
        device_id = websocket.headers.get("x-walle-device") or "walle-unknown"

        if not _authorised(config, presented):
            log.warning("rejected connection from %s: bad or missing token",
                        websocket.client.host if websocket.client else "?")
            # 1008 = policy violation. Close before accepting so an
            # unauthenticated peer never gets to send us a single frame.
            await websocket.close(code=1008, reason="unauthorised")
            return

        await websocket.accept()
        brain: Brain = app.state.brain

        session = Session(
            brain=brain,
            device_id=device_id,
            send_text=websocket.send_text,
            send_bytes=websocket.send_bytes,
        )
        # A robot that dropped off without a clean close can reconnect while
        # its old handler is still winding down. Close the stale session before
        # replacing it, so its in-flight turn does not keep talking.
        previous = app.state.sessions.get(device_id)
        if previous is not None and previous is not session:
            log.info("[%s] replacing an existing session", device_id)
            await previous.close()
        app.state.sessions[device_id] = session
        log.info("[%s] websocket open from %s", device_id,
                 websocket.client.host if websocket.client else "?")

        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if (text := message.get("text")) is not None:
                    try:
                        await session.on_control(proto.decode_json(text))
                    except proto.ProtocolError as exc:
                        log.warning("[%s] bad control frame: %s", device_id, exc)
                elif (data := message.get("bytes")) is not None:
                    await session.on_binary(data)
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("[%s] websocket error", device_id)
        finally:
            await session.close()
            # Only remove the mapping if it still points at *this* handler's
            # session. An unconditional pop would delete a newer session that
            # reconnected under the same device id, leaving the live robot
            # missing from /health and unclosed at shutdown.
            if app.state.sessions.get(device_id) is session:
                app.state.sessions.pop(device_id, None)
            log.info("[%s] websocket closed", device_id)

    return app
