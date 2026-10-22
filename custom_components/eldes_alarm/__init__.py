"""Support for the Eldes API."""
from datetime import datetime, timedelta
import logging
import asyncio
from http import HTTPStatus

import voluptuous as vol
from aiohttp import ClientResponseError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, CONF_PIN, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed
)

from .const import (
    DEFAULT_NAME,
    DATA_CLIENT,
    DATA_COORDINATOR,
    DEFAULT_SCAN_INTERVAL,
    CONF_DEVICE_IMEI,
    CONF_EVENTS_LIST_SIZE,
    DEFAULT_EVENTS_LIST_SIZE,
    DOMAIN,
    ALARM_MODES,
    SERVICE_ARM_WITH_BYPASS,
    SERVICE_ARM_HOME_WITH_BYPASS,
    SERVICE_RETRY_ARM_WITH_BYPASS,
    ARM_FAILURE_CONTEXT_TTL_SECONDS,
    DATA_LISTENER,
    CONF_PUSH_ENABLED,
    CONF_PUSH_SCAN_INTERVAL,
    DEFAULT_PUSH_ENABLED,
    DEFAULT_PUSH_SCAN_INTERVAL,
    EVENT_CLOUD_PUSH,
    SIGNAL_PUSH_STATE,
)

from .core.eldes_cloud import EldesCloud
from .core.eldes_ws import EldesEventListener, extract_imei

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "switch", "alarm_control_panel", "event"]

DATA_SHARED_CLIENTS = "_clients"
DATA_LAST_ARM_FAILURE = "_last_arm_failure"

CONFIG_SCHEMA = cv.deprecated(DOMAIN)

SERVICE_BYPASS_SCHEMA = vol.Schema(
    {
        vol.Optional("bypass_zones"): list,
        vol.Optional("bypass_all", default=False): bool,
    }
)

DEFAULT_DEVICE_INFO = {
    "model": "Unknown",
    "firmware": "Unknown",
    "online": False,
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eldes from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    pin = entry.data[CONF_PIN]
    selected_imei = entry.data[CONF_DEVICE_IMEI]
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    hass.data.setdefault(DOMAIN, {DATA_SHARED_CLIENTS: {}})

    client_key = (username, password, pin)

    if client_key in hass.data[DOMAIN][DATA_SHARED_CLIENTS]:
        eldes_client = hass.data[DOMAIN][DATA_SHARED_CLIENTS][client_key]
        _LOGGER.debug("Reusing shared Eldes client for %s", selected_imei)
    else:
        session = async_get_clientsession(hass)
        eldes_client = EldesCloud(session, username, password, pin)

        try:
            await eldes_client.login()
        except (asyncio.TimeoutError, ClientResponseError) as ex:
            if isinstance(ex, ClientResponseError) and ex.status == HTTPStatus.UNAUTHORIZED:
                raise ConfigEntryAuthFailed from ex
            raise ConfigEntryNotReady from ex
        except Exception as ex:
            _LOGGER.error("Failed to login to Eldes: %s", ex)
            return False

        hass.data[DOMAIN][DATA_SHARED_CLIENTS][client_key] = eldes_client

    async def async_update_data():
        """Fetch data for selected Eldes device."""
        try:
            await eldes_client.renew_token()
            previous = coordinator.data[0] if coordinator.data else None
            return [await async_fetch_device_data(eldes_client, selected_imei, entry, previous)]
        except Exception as ex:
            _LOGGER.warning("Failed to update Eldes device data: %s", ex)
            raise UpdateFailed(ex) from ex

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"Eldes {selected_imei}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=scan_interval),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_CLIENT: eldes_client,
        DATA_COORDINATOR: coordinator,
        DATA_LISTENER: None,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if entry.options.get(CONF_PUSH_ENABLED, DEFAULT_PUSH_ENABLED):
        _start_push_listener(hass, entry, eldes_client, coordinator, selected_imei, scan_interval)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    async def handle_arm_with_bypass(call: ServiceCall) -> None:
        """Handle arm with bypass service call."""
        bypass_zones = call.data.get("bypass_zones", [])
        bypass_all = call.data.get("bypass_all", False)

        for eid, data in hass.data[DOMAIN].items():
            if eid in (DATA_SHARED_CLIENTS, DATA_LAST_ARM_FAILURE) or not isinstance(data, dict):
                continue
            client = data[DATA_CLIENT]
            coord = data[DATA_COORDINATOR]
            for device in coord.data:
                for partition in device.get("partitions", []):
                    zones = bypass_zones if not bypass_all else _all_zone_ids(device)
                    await client.set_alarm(
                        ALARM_MODES["ARM_AWAY"],
                        device["imei"],
                        partition["internalId"],
                        zones_to_bypass=zones or None,
                    )

    async def handle_arm_home_with_bypass(call: ServiceCall) -> None:
        """Handle arm home with bypass service call."""
        bypass_zones = call.data.get("bypass_zones", [])
        bypass_all = call.data.get("bypass_all", False)

        for eid, data in hass.data[DOMAIN].items():
            if eid in (DATA_SHARED_CLIENTS, DATA_LAST_ARM_FAILURE) or not isinstance(data, dict):
                continue
            client = data[DATA_CLIENT]
            coord = data[DATA_COORDINATOR]
            for device in coord.data:
                for partition in device.get("partitions", []):
                    zones = bypass_zones if not bypass_all else _all_zone_ids(device)
                    await client.set_alarm(
                        ALARM_MODES["ARM_HOME"],
                        device["imei"],
                        partition["internalId"],
                        zones_to_bypass=zones or None,
                    )

    async def handle_retry_arm_with_bypass(call: ServiceCall) -> None:
        """Re-arm using the stored arm failure context (partition + violated zones)."""
        failure = hass.data[DOMAIN].get(DATA_LAST_ARM_FAILURE)
        if not failure:
            _LOGGER.warning("retry_arm_with_bypass: no recent arm failure context found")
            return

        age = (datetime.now() - failure["timestamp"]).total_seconds()
        if age > ARM_FAILURE_CONTEXT_TTL_SECONDS:
            _LOGGER.warning(
                "retry_arm_with_bypass: arm failure context expired (%.0fs old, max %ds)",
                age, ARM_FAILURE_CONTEXT_TTL_SECONDS,
            )
            hass.data[DOMAIN].pop(DATA_LAST_ARM_FAILURE, None)
            return

        imei = failure["imei"]
        partition_id = failure["partition_id"]
        mode = failure["mode"]
        bypass_zones = failure["bypass_zones"]

        _LOGGER.info(
            "retry_arm_with_bypass: re-arming %s partition %s with bypass zones %s",
            mode, partition_id, bypass_zones,
        )

        client = None
        for eid, data in hass.data[DOMAIN].items():
            if eid in (DATA_SHARED_CLIENTS, DATA_LAST_ARM_FAILURE):
                continue
            if not isinstance(data, dict):
                continue
            coord = data.get(DATA_COORDINATOR)
            if coord and coord.data:
                for device in coord.data:
                    if device.get("imei") == imei:
                        client = data[DATA_CLIENT]
                        break
            if client:
                break

        if not client:
            _LOGGER.error("retry_arm_with_bypass: could not find client for IMEI %s", imei)
            return

        await client.set_alarm(mode, imei, partition_id, zones_to_bypass=bypass_zones)
        hass.data[DOMAIN].pop(DATA_LAST_ARM_FAILURE, None)

        _LOGGER.info("retry_arm_with_bypass: arm command sent successfully")

    hass.services.async_register(
        DOMAIN, SERVICE_ARM_WITH_BYPASS, handle_arm_with_bypass,
        schema=SERVICE_BYPASS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ARM_HOME_WITH_BYPASS, handle_arm_home_with_bypass,
        schema=SERVICE_BYPASS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RETRY_ARM_WITH_BYPASS, handle_retry_arm_with_bypass,
    )

    return True


def _start_push_listener(hass, entry, eldes_client, coordinator, imei, scan_interval):
    """Open the persistent cloud WebSocket for this device."""
    push_interval = entry.options.get(CONF_PUSH_SCAN_INTERVAL, DEFAULT_PUSH_SCAN_INTERVAL)
    signal = SIGNAL_PUSH_STATE.format(entry.entry_id)

    async def token_provider() -> str:
        await eldes_client.renew_token()
        return eldes_client._get_token()

    async def on_message(topic, payload):
        payload_imei = extract_imei(payload)
        if payload_imei and payload_imei != imei:
            return
        hass.bus.async_fire(EVENT_CLOUD_PUSH, {
            "imei": imei,
            "topic": topic,
            "payload": payload,
        })
        await coordinator.async_request_refresh()

    def on_connection_change(connected: bool):
        # While push works, polling is only a safety net and can be slow.
        seconds = max(push_interval, scan_interval) if connected else scan_interval
        coordinator.update_interval = timedelta(seconds=seconds)
        async_dispatcher_send(hass, signal, connected)
        if connected:
            # Catch anything that happened while we were disconnected.
            hass.async_create_task(coordinator.async_request_refresh())

    listener = EldesEventListener(
        async_get_clientsession(hass),
        token_provider,
        on_message,
        on_connection_change,
    )
    listener.start(
        lambda coro: entry.async_create_background_task(hass, coro, f"eldes_push_{imei}")
    )
    hass.data[DOMAIN][entry.entry_id][DATA_LISTENER] = listener


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload so new options (interval, push, arm modes) take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


def _all_zone_ids(device: dict) -> list:
    """Extract all zone IDs from device data for bypass_all."""
    return [z.get("zoneId") for z in device.get("zones", []) if not z.get("disabled", False)]


async def async_fetch_device_data(
    eldes_client: EldesCloud,
    imei: str,
    entry: ConfigEntry,
    previous: dict | None = None,
) -> dict:
    """Fetch full data for a single Eldes device.

    Each call is resilient to transient failures: when an individual API call
    fails, the previous coordinator value for that key is reused so entities
    that index into ``zones``/``partitions``/``outputs``/``temp`` keep working
    instead of crashing with IndexError. Only when there is no previous value
    (first refresh) do we fall back to an empty list / default info.
    """
    events_list_size = entry.options.get(CONF_EVENTS_LIST_SIZE, DEFAULT_EVENTS_LIST_SIZE)

    device = {"imei": imei, "active_zones": []}
    if previous:
        device["active_zones"] = previous.get("active_zones", [])

    for key, coro in [
        ("info", eldes_client.get_device_info(imei)),
        ("partitions", eldes_client.get_device_partitions(imei)),
        ("outputs", eldes_client.get_device_outputs(imei)),
        ("temp", eldes_client.get_temperatures(imei)),
        ("events", eldes_client.get_events(imei, events_list_size)),
        ("zones", eldes_client.get_zones(imei)),
        ("system_faults", eldes_client.get_system_faults(imei)),
    ]:
        try:
            device[key] = await coro
        except Exception as ex:
            if previous and key in previous:
                device[key] = previous[key]
                _LOGGER.warning(
                    "Failed to fetch %s for %s (%s); reusing last-known-good value",
                    key, imei, ex,
                )
            else:
                _LOGGER.warning("Failed to fetch %s for %s: %s", key, imei, ex)
                device[key] = dict(DEFAULT_DEVICE_INFO) if key == "info" else []

    return device


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload Eldes config entry."""
    listener = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get(DATA_LISTENER)
    if listener:
        await listener.stop()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

        username = entry.data[CONF_USERNAME]
        password = entry.data[CONF_PASSWORD]
        pin = entry.data[CONF_PIN]
        client_key = (username, password, pin)

        still_in_use = any(
            eid != DATA_SHARED_CLIENTS
            and eid != entry.entry_id
            and isinstance(data, dict)
            and data.get(DATA_CLIENT) is hass.data[DOMAIN].get(DATA_SHARED_CLIENTS, {}).get(client_key)
            for eid, data in hass.data[DOMAIN].items()
        )
        if not still_in_use:
            hass.data[DOMAIN].get(DATA_SHARED_CLIENTS, {}).pop(client_key, None)

    return unload_ok


class EldesDeviceEntity(CoordinatorEntity):
    """Defines a base Eldes device entity."""

    def __init__(self, client, coordinator, device_index, entity_index=None):
        """Initialize the Eldes entity."""
        super().__init__(coordinator)
        self.client = client
        self.device_index = device_index
        self.entity_index = entity_index
        self.imei = self.coordinator.data[self.device_index]["imei"]

    @property
    def data(self):
        """Shortcut to access this device's data."""
        return self.coordinator.data[self.device_index]

    @property
    def device_info(self):
        """Return device info for the Eldes entity."""
        info = self.data.get("info", {})
        return {
            "identifiers": {(DOMAIN, self.imei)},
            "name": info.get("model", "Eldes"),
            "manufacturer": DEFAULT_NAME,
            "sw_version": info.get("firmware", "Unknown"),
            "model": info.get("model", "Unknown"),
        }
