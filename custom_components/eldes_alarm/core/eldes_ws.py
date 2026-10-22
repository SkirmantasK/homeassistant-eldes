"""Persistent STOMP-over-WebSocket listener for Eldes Cloud push events.

The Eldes mobile app keeps a STOMP session open to the cloud and receives
pushes on ``/user/queue/device/event`` (alarms, arm/disarm, troubles) and
``/user/queue/device/response`` (command results). This module keeps the same
kind of session open from Home Assistant so state changes show up within a
second or two instead of waiting for the next REST poll.

The payload format of the event topic is not documented, so the listener does
not try to interpret it beyond extracting an IMEI when one is present. Each
message is handed to a callback, which in this integration triggers a
coordinator refresh and fires a Home Assistant event with the raw payload.

This module depends only on aiohttp so it can be tested without Home Assistant.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, Awaitable, Callable

import aiohttp

_LOGGER = logging.getLogger(__name__)

WS_URL = "wss://cloud.eldesalarms.com:8083/eldes/websocket"
WS_PROTOCOLS = ["v10.stomp", "v11.stomp", "v12.stomp"]

TOPIC_EVENT = "/user/queue/device/event"
TOPIC_RESPONSE = "/user/queue/device/response"
DEFAULT_TOPICS = (TOPIC_EVENT, TOPIC_RESPONSE)

CONNECT_TIMEOUT = 10
# STOMP heart-beat we ask for (ms). The server may answer 0,0, in which case
# we rely on WebSocket-level pings from aiohttp instead.
STOMP_HEARTBEAT_MS = 20000
WS_PING_INTERVAL = 30
# If nothing at all (frame, heart-beat or pong) arrives for this long the
# connection is treated as dead and reopened.
IDLE_TIMEOUT = 120
# Safety net: reopen the session with a fresh token once an hour. Eldes tokens
# expire after about five minutes; the server is expected to check the token
# only at CONNECT, and if it does drop sessions earlier the normal reconnect
# path takes over.
MAX_SESSION_SECONDS = 60 * 60

BACKOFF_MIN = 5
BACKOFF_MAX = 300


def build_frame(command: str, headers: dict[str, str] | None = None, body: str = "") -> str:
    """Build a STOMP frame."""
    frame = command + "\n"
    for key, value in (headers or {}).items():
        frame += f"{key}:{value}\n"
    return frame + "\n" + body + "\x00"


def parse_frames(data: str) -> list[tuple[str, dict[str, str], str]]:
    """Parse one WebSocket text message into STOMP frames.

    A message can hold several NUL-terminated frames, and a bare newline is a
    STOMP heart-beat, which yields no frame.
    """
    frames = []
    for chunk in data.split("\x00"):
        chunk = chunk.lstrip("\r\n")
        if not chunk:
            continue
        if "\n\n" in chunk:
            head, body = chunk.split("\n\n", 1)
        else:
            head, body = chunk, ""
        lines = head.replace("\r\n", "\n").split("\n")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers.setdefault(key, value)
        frames.append((lines[0].strip(), headers, body))
    return frames


def extract_imei(payload: Any) -> str | None:
    """Find an IMEI anywhere near the top of a payload, if there is one."""
    if isinstance(payload, dict):
        for key in ("imei", "deviceImei", "IMEI"):
            value = payload.get(key)
            if value:
                return str(value)
        for key in ("device", "deviceInfo", "data"):
            found = extract_imei(payload.get(key))
            if found:
                return found
    return None


EventCallback = Callable[[str, Any], Awaitable[None] | None]
TokenProvider = Callable[[], Awaitable[str]]
StateCallback = Callable[[bool], None]


class EldesEventListener:
    """Keeps one STOMP session open and reconnects when it drops."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_provider: TokenProvider,
        on_message: EventCallback,
        on_connection_change: StateCallback | None = None,
        topics: tuple[str, ...] = DEFAULT_TOPICS,
        url: str = WS_URL,
        max_session_seconds: float = MAX_SESSION_SECONDS,
    ) -> None:
        self._session = session
        self._token_provider = token_provider
        self._on_message = on_message
        self._on_connection_change = on_connection_change
        self._topics = topics
        self._url = url
        self._max_session_seconds = max_session_seconds

        self._task: asyncio.Task | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._stopping = False
        self.connected = False
        self.messages_received = 0
        self.last_error: str | None = None

    # ----------------------------------------------------------- lifecycle
    def start(self, create_task: Callable[[Awaitable], asyncio.Task] | None = None) -> None:
        """Start the background loop."""
        if self._task and not self._task.done():
            return
        self._stopping = False
        coro = self._run()
        self._task = create_task(coro) if create_task else asyncio.ensure_future(coro)

    async def stop(self) -> None:
        """Stop the loop and close the socket."""
        self._stopping = True
        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.send_str(build_frame("DISCONNECT"))
            except Exception:  # noqa: BLE001 - best effort on shutdown
                pass
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._set_connected(False)

    # ----------------------------------------------------------- internals
    def _set_connected(self, value: bool) -> None:
        if self.connected == value:
            return
        self.connected = value
        if self._on_connection_change:
            try:
                self._on_connection_change(value)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Eldes push: connection callback failed")

    async def _run(self) -> None:
        backoff = BACKOFF_MIN
        while not self._stopping:
            try:
                planned = await self._session_once()
                self.last_error = None
                if planned:
                    backoff = BACKOFF_MIN
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep the loop alive
                self.last_error = f"{type(err).__name__}: {err}"
                _LOGGER.warning("Eldes push connection lost: %s", self.last_error)
            finally:
                self._set_connected(False)

            if self._stopping:
                break
            delay = backoff + random.uniform(0, backoff / 4)
            _LOGGER.debug("Eldes push: reconnecting in %.0fs", delay)
            await asyncio.sleep(delay)
            backoff = min(backoff * 2, BACKOFF_MAX)

    async def _session_once(self) -> bool:
        """Run one session. Returns True if it ended by plan (token rotation)."""
        token = await self._token_provider()
        if not token:
            raise ConnectionError("no access token")

        async with self._session.ws_connect(
            self._url,
            protocols=WS_PROTOCOLS,
            heartbeat=WS_PING_INTERVAL,
            timeout=aiohttp.ClientWSTimeout(ws_close=5.0),
        ) as ws:
            self._ws = ws
            await ws.send_str(build_frame("CONNECT", {
                "accept-version": "1.1,1.2",
                "heart-beat": f"{STOMP_HEARTBEAT_MS},{STOMP_HEARTBEAT_MS}",
                "Authorization": f"Bearer {token}",
            }))

            server_hb_ms = await self._wait_connected(ws)
            for index, topic in enumerate(self._topics):
                await ws.send_str(build_frame("SUBSCRIBE", {
                    "id": f"ha-sub-{index}",
                    "destination": topic,
                    "ack": "auto",
                }))

            _LOGGER.info("Eldes push connected, listening on %s", ", ".join(self._topics))
            self._set_connected(True)

            heartbeat_task = None
            if server_hb_ms:
                heartbeat_task = asyncio.ensure_future(self._send_heartbeats(ws, server_hb_ms / 1000))
            try:
                return await self._read_loop(ws)
            finally:
                if heartbeat_task:
                    heartbeat_task.cancel()
                self._ws = None

    async def _wait_connected(self, ws) -> int:
        """Wait for CONNECTED and return how often we must send heart-beats (ms)."""
        async with asyncio.timeout(CONNECT_TIMEOUT):
            while True:
                msg = await ws.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    for command, headers, body in parse_frames(msg.data):
                        if command == "CONNECTED":
                            return _negotiated_send_interval(headers.get("heart-beat", "0,0"))
                        if command == "ERROR":
                            raise ConnectionError(
                                f"STOMP ERROR: {headers.get('message', '')} {body}".strip()
                            )
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                                  aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                    raise ConnectionError(f"socket closed during STOMP handshake ({msg.type})")

    async def _send_heartbeats(self, ws, interval: float) -> None:
        while not ws.closed:
            await asyncio.sleep(interval)
            try:
                await ws.send_str("\n")
            except Exception:  # noqa: BLE001
                return

    async def _read_loop(self, ws) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._max_session_seconds
        while not self._stopping:
            remaining = deadline - loop.time()
            if remaining <= 0:
                _LOGGER.debug("Eldes push: rotating session to refresh token")
                try:
                    await ws.send_str(build_frame("DISCONNECT"))
                except Exception:  # noqa: BLE001
                    pass
                return True
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=min(IDLE_TIMEOUT, remaining))
            except asyncio.TimeoutError:
                if loop.time() >= deadline:
                    continue
                raise ConnectionError(f"no data from server for {IDLE_TIMEOUT}s")

            if msg.type == aiohttp.WSMsgType.TEXT:
                for command, headers, body in parse_frames(msg.data):
                    if command == "MESSAGE":
                        await self._dispatch(headers.get("destination", ""), body)
                    elif command == "ERROR":
                        raise ConnectionError(
                            f"STOMP ERROR: {headers.get('message', '')} {body}".strip()
                        )
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED,
                              aiohttp.WSMsgType.CLOSING):
                raise ConnectionError("server closed the socket")
            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise ConnectionError(f"socket error: {ws.exception()}")
        return True

    async def _dispatch(self, destination: str, body: str) -> None:
        self.messages_received += 1
        try:
            payload: Any = json.loads(body) if body else None
        except (json.JSONDecodeError, TypeError):
            payload = body
        _LOGGER.debug("Eldes push message on %s: %s", destination, payload)
        try:
            result = self._on_message(destination, payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 - one bad message must not kill the loop
            _LOGGER.exception("Eldes push: message handler failed")


def _negotiated_send_interval(server_header: str) -> int:
    """STOMP heart-beat negotiation: how often (ms) the client must send."""
    try:
        _, server_wants = (int(x) for x in server_header.split(",", 1))
    except ValueError:
        return 0
    if server_wants == 0:
        return 0
    return max(STOMP_HEARTBEAT_MS, server_wants)
