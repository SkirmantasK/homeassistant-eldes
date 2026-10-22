"""Implementation for Eldes Cloud"""
import asyncio
import async_timeout
import json
import logging
import aiohttp
from datetime import datetime, timedelta

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelState
)

from ..const import API_URL, API_PATHS

_LOGGER = logging.getLogger(__name__)

WS_URL = "wss://cloud.eldesalarms.com:8083/eldes/websocket"
WS_RESPONSE_TOPIC = "/user/queue/device/response"
WS_FEEDBACK_TIMEOUT = 10

ALARM_STATES_MAP = {
    "DISARMED": AlarmControlPanelState.DISARMED,
    "ARMED": AlarmControlPanelState.ARMED_AWAY,
    "ARMSTAY": AlarmControlPanelState.ARMED_HOME
}


def _build_stomp_frame(command, headers=None, body=""):
    """Build a STOMP protocol frame."""
    frame = command + "\n"
    for key, value in (headers or {}).items():
        frame += f"{key}:{value}\n"
    frame += "\n" + body + "\x00"
    return frame


def _parse_stomp_frame(data):
    """Parse a STOMP frame into (command, headers, body)."""
    if "\n\n" not in data:
        return None, {}, ""
    header_part, body = data.split("\n\n", 1)
    body = body.rstrip("\x00")
    lines = header_part.split("\n")
    command = lines[0] if lines else ""
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k] = v
    return command, headers, body


class EldesCloud:
    """Interacts with Eldes via public API."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str, pin: str):
        self.timeout = 15
        self.headers = {
            "X-Requested-With": "XMLHttpRequest",
            "x-whitelable": "eldes"
        }
        self._refresh_token = ""
        self._token_expires_at = None

        self._http_session = session
        self._username = username
        self._password = password
        self._pin = pin

    def _get_token(self):
        """Extract current bearer token from headers."""
        auth = self.headers.get("Authorization", "")
        return auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""

    async def _setOAuthHeader(self, data):
        if "refreshToken" in data:
            self._refresh_token = data["refreshToken"]

        if "token" in data:
            self.headers["Authorization"] = f"Bearer {data['token']}"
            self._token_expires_at = datetime.utcnow() + timedelta(minutes=4)

        return data

    async def _api_call(self, url, method, data=None):
        try:
            _LOGGER.debug("API Call -> %s %s | Headers: %s | Data: %s", method, url, self.headers, data)

            async with async_timeout.timeout(self.timeout):
                req = await self._http_session.request(
                    method,
                    url,
                    json=data,
                    headers=self.headers
                )

            req.raise_for_status()
            return req

        except aiohttp.ClientResponseError as err:
            _LOGGER.error("Client response error on API %s request: %s", url, err)
            raise

        except aiohttp.ClientError as err:
            _LOGGER.error("Client error on API %s request: %s", url, err)
            raise

        except asyncio.TimeoutError:
            _LOGGER.error("Timeout error on API request: %s", url)
            raise

    async def _safe_api_call(self, url, method, data=None):
        try:
            return await self._api_call(url, method, data)

        except aiohttp.ClientResponseError as err:
            if err.status in (401, 403):
                _LOGGER.warning("Auth error (%s) on %s - attempting to re-authenticate.", err.status, url)
                await self.login()
                try:
                    return await self._api_call(url, method, data)
                except Exception as retry_err:
                    _LOGGER.error("Retry failed for %s: %s", url, retry_err)
                    raise
            raise

    async def login(self):
        data = {
            "email": self._username,
            "password": self._password,
            "hostDeviceId": "HomeAssistant"
        }

        url = f"{API_URL}{API_PATHS['AUTH']}login"
        resp = await self._api_call(url, "POST", data)
        result = await resp.json()

        _LOGGER.debug("login result: %s", result)
        return await self._setOAuthHeader(result)

    async def renew_token(self):
        if not self._token_expires_at or datetime.utcnow() < self._token_expires_at:
            _LOGGER.debug("Token is still valid; skipping token refresh.")
            return

        self.headers["Authorization"] = f"Bearer {self._refresh_token}"
        url = f"{API_URL}{API_PATHS['AUTH']}token"

        try:
            async with async_timeout.timeout(self.timeout):
                response = await self._http_session.get(url, headers=self.headers)

            response.raise_for_status()
            result = await response.json()

            _LOGGER.debug("Token successfully refreshed: %s", result)
            return await self._setOAuthHeader(result)

        except Exception:
            _LOGGER.debug("Token refresh failed, falling back to full re-login")
            await self.login()

    async def get_devices(self):
        url = f"{API_URL}{API_PATHS['DEVICE']}list"
        response = await self._safe_api_call(url, "GET")
        result = await response.json()
        return result.get("deviceListEntries", [])

    async def get_device_info(self, imei):
        url = f"{API_URL}{API_PATHS['DEVICE']}info?imei={imei}"
        response = await self._safe_api_call(url, "GET")
        return await response.json()

    async def get_device_partitions(self, imei):
        data = {"imei": imei, "pin": self._pin}
        url = f"{API_URL}{API_PATHS['DEVICE']}partition/list?imei={imei}"
        response = await self._safe_api_call(url, "POST", data)
        result = await response.json()
        partitions = result.get("partitions", [])

        for partition in partitions:
            state = partition.get("state", AlarmControlPanelState.DISARMED)
            partition["state"] = ALARM_STATES_MAP.get(state, AlarmControlPanelState.DISARMED)

        return partitions

    async def get_device_outputs(self, imei):
        data = {"imei": imei, "pin": self._pin}
        url = f"{API_URL}{API_PATHS['DEVICE']}list-outputs/{imei}"
        response = await self._safe_api_call(url, "POST", data)
        result = await response.json()
        return result.get("deviceOutputs", [])

    async def get_zones(self, imei):
        """Fetch all zones for a device. Returns list of zone objects."""
        url = f"{API_URL}{API_PATHS['DEVICE']}list-zones/{imei}"
        try:
            response = await self._safe_api_call(url, "GET")
            result = await response.json()
            _LOGGER.debug("Zones response for %s: %s", imei, result)
            if isinstance(result, list):
                return result
            return result.get("zones", result.get("zoneList", []))
        except Exception as ex:
            _LOGGER.warning("Failed to fetch zones (non-fatal): %s", ex)
            return []

    async def get_system_faults(self, imei):
        """Fetch active system faults for a device."""
        data = {"pin": self._pin}
        url = f"{API_URL}{API_PATHS['DEVICE']}system-fault/list/{imei}"
        try:
            response = await self._safe_api_call(url, "POST", data)
            result = await response.json()
            _LOGGER.debug("System faults for %s: %s", imei, result)
            if isinstance(result, list):
                return result
            return result.get("faults", result.get("systemFaults", []))
        except Exception as ex:
            _LOGGER.warning("Failed to fetch system faults (non-fatal): %s", ex)
            return []

    async def set_alarm(self, mode, imei, zone_id, zones_to_bypass=None):
        """Arm/disarm with optional zone bypass."""
        data = {"imei": imei, "partitionIndex": zone_id, "pin": self._pin}
        if zones_to_bypass:
            data["zonesToBypass"] = zones_to_bypass
        url = f"{API_URL}{API_PATHS['DEVICE']}action/{mode}"
        response = await self._safe_api_call(url, "POST", data)
        return await response.text()

    async def set_alarm_with_feedback(self, mode, imei, zone_id, zones_to_bypass=None):
        """Arm/disarm and listen on WebSocket for active zones feedback.

        Returns dict with:
          - active_zones: list of {"internalId": int, "name": str} if zones are violated
          - command_type: the command type from the response
          - partition_status: partition status string from response
        If WebSocket fails, falls back to REST-only (no active zones info).
        """
        result = {
            "active_zones": [],
            "command_type": None,
            "partition_status": None,
        }

        ws = None
        try:
            token = self._get_token()
            ws = await self._http_session.ws_connect(
                WS_URL,
                protocols=["v10.stomp", "v11.stomp", "v12.stomp"],
                timeout=aiohttp.ClientWSTimeout(ws_close=5.0),
            )

            connect_frame = _build_stomp_frame("CONNECT", {
                "accept-version": "1.1,1.2",
                "heart-beat": "0,0",
                "Authorization": f"Bearer {token}",
            })
            await ws.send_str(connect_frame)

            connected = False
            async with async_timeout.timeout(5):
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        cmd, _, _ = _parse_stomp_frame(msg.data)
                        if cmd == "CONNECTED":
                            connected = True
                            break
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

            if not connected:
                _LOGGER.warning("STOMP CONNECT failed, falling back to REST-only")
                await self.set_alarm(mode, imei, zone_id, zones_to_bypass)
                return result

            sub_frame = _build_stomp_frame("SUBSCRIBE", {
                "id": "sub-0",
                "destination": WS_RESPONSE_TOPIC,
                "ack": "auto",
            })
            await ws.send_str(sub_frame)

            await self.set_alarm(mode, imei, zone_id, zones_to_bypass)

            async with async_timeout.timeout(WS_FEEDBACK_TIMEOUT):
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        cmd, headers, body = _parse_stomp_frame(msg.data)
                        if cmd == "MESSAGE" and body:
                            try:
                                payload = json.loads(body)
                                result["active_zones"] = payload.get("activeZones", [])
                                result["command_type"] = payload.get("commandType")
                                ps = payload.get("partitionStatus")
                                if ps and isinstance(ps, dict):
                                    result["partition_status"] = ps.get("status")
                                elif isinstance(ps, str):
                                    result["partition_status"] = ps
                                _LOGGER.debug("WebSocket arm feedback: %s", payload)
                            except (json.JSONDecodeError, TypeError) as ex:
                                _LOGGER.warning("Failed to parse WS message: %s", ex)
                            break
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break

        except asyncio.TimeoutError:
            _LOGGER.debug("WebSocket feedback timed out (no active zones returned)")
        except Exception as ex:
            _LOGGER.warning("WebSocket feedback failed, arm command was still sent via REST: %s", ex)
            if ws is None:
                await self.set_alarm(mode, imei, zone_id, zones_to_bypass)
        finally:
            if ws and not ws.closed:
                try:
                    disconnect = _build_stomp_frame("DISCONNECT", {"receipt": "disc-0"})
                    await ws.send_str(disconnect)
                except Exception:
                    pass
                await ws.close()

        return result

    async def turn_on_output(self, imei, output_id):
        data = {"pin": self._pin}
        url = f"{API_URL}{API_PATHS['DEVICE']}control/enable/{imei}/{output_id}"
        response = await self._safe_api_call(url, "PUT", data)
        return response

    async def turn_off_output(self, imei, output_id):
        data = {"pin": self._pin}
        url = f"{API_URL}{API_PATHS['DEVICE']}control/disable/{imei}/{output_id}"
        response = await self._safe_api_call(url, "PUT", data)
        return response

    async def get_temperatures(self, imei):
        data = {"pin": self._pin}
        url = f"{API_URL}{API_PATHS['DEVICE']}temperatures?imei={imei}"
        response = await self._safe_api_call(url, "POST", data)
        result = await response.json()
        return result.get("temperatureDetailsList", [])

    async def get_events(self, imei, size):
        # IMEI must be a query parameter (cloud started enforcing this on
        # 2026-05-07; sending it in the body returns HTTP 400
        # `error.unexpected`).
        data = {"size": size, "start": 0, "pin": self._pin}
        url = f"{API_URL}{API_PATHS['DEVICE']}event/list?imei={imei}"
        try:
            response = await self._safe_api_call(url, "POST", data)
            result = await response.json()
            _LOGGER.debug("Events raw response: %s", result)
            events = result.get("eventDetails", [])
            if events is None:
                _LOGGER.warning("Events endpoint returned null eventDetails")
                return []
            return events
        except Exception as ex:
            _LOGGER.warning("Failed to fetch events (non-fatal): %s", ex)
            return []
