"""Support for Eldes binary sensors."""
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    DATA_CLIENT,
    DATA_COORDINATOR,
    DOMAIN,
    ZONE_NAME_DEVICE_CLASS_MAP,
    CONF_PUSH_ENABLED,
    DEFAULT_PUSH_ENABLED,
    DATA_LISTENER,
    SIGNAL_PUSH_STATE,
)
from . import EldesDeviceEntity

_LOGGER = logging.getLogger(__name__)

DEVICE_CLASS_LOOKUP = {
    "door": BinarySensorDeviceClass.DOOR,
    "window": BinarySensorDeviceClass.WINDOW,
    "motion": BinarySensorDeviceClass.MOTION,
    "smoke": BinarySensorDeviceClass.SMOKE,
    "gas": BinarySensorDeviceClass.GAS,
    "moisture": BinarySensorDeviceClass.MOISTURE,
    "tamper": BinarySensorDeviceClass.TAMPER,
    "vibration": BinarySensorDeviceClass.VIBRATION,
}


def _detect_device_class(zone_name: str) -> BinarySensorDeviceClass | None:
    """Auto-detect device class from zone name keywords."""
    name_lower = zone_name.lower()
    for keyword, class_key in ZONE_NAME_DEVICE_CLASS_MAP.items():
        if keyword in name_lower:
            return DEVICE_CLASS_LOOKUP.get(class_key)
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the Eldes binary sensor platform."""
    client = hass.data[DOMAIN][entry.entry_id][DATA_CLIENT]
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    entities = []

    for index in range(len(coordinator.data)):
        entities.append(EldesConnectionStatusBinarySensor(client, coordinator, index))
        if entry.options.get(CONF_PUSH_ENABLED, DEFAULT_PUSH_ENABLED):
            entities.append(EldesPushStatusBinarySensor(client, coordinator, index, entry))

        for zone_index, zone in enumerate(coordinator.data[index].get("zones", [])):
            if not zone.get("disabled", False):
                entities.append(EldesZoneSensor(client, coordinator, index, zone_index))

    async_add_entities(entities)


class EldesConnectionStatusBinarySensor(EldesDeviceEntity, BinarySensorEntity):
    """Class for the Eldes connection status sensor."""

    @property
    def unique_id(self):
        return f"{self.imei}_connection_status"

    @property
    def name(self):
        return f"{self.data['info']['model']} Connection Status"

    @property
    def is_on(self):
        return self.data["info"].get("online", False)

    @property
    def device_class(self):
        return BinarySensorDeviceClass.CONNECTIVITY


class EldesPushStatusBinarySensor(EldesDeviceEntity, BinarySensorEntity):
    """Shows whether the persistent cloud WebSocket is connected."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-sync"

    def __init__(self, client, coordinator, device_index, entry):
        super().__init__(client, coordinator, device_index)
        self._entry = entry
        self._push_connected = False

    @property
    def unique_id(self):
        return f"{self.imei}_cloud_push"

    @property
    def name(self):
        return f"{self.data['info'].get('model', 'Eldes')} Cloud Push"

    @property
    def is_on(self):
        return self._push_connected

    @property
    def extra_state_attributes(self):
        listener = self._listener
        if not listener:
            return {}
        return {
            "messages_received": listener.messages_received,
            "last_error": listener.last_error,
        }

    @property
    def _listener(self):
        return self.hass.data[DOMAIN].get(self._entry.entry_id, {}).get(DATA_LISTENER)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        listener = self._listener
        self._push_connected = bool(listener and listener.connected)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_PUSH_STATE.format(self._entry.entry_id),
                self._handle_push_state,
            )
        )

    @callback
    def _handle_push_state(self, connected):
        self._push_connected = connected
        self.async_write_ha_state()


class EldesZoneSensor(EldesDeviceEntity, BinarySensorEntity):
    """Binary sensor for an individual Eldes alarm zone."""

    def __init__(self, client, coordinator, device_index, zone_index):
        super().__init__(client, coordinator, device_index, zone_index)
        zone = self.data["zones"][zone_index]
        self._zone_id = zone.get("zoneId")
        self._zone_name = zone.get("zoneName", f"Zone {self._zone_id}")
        self._detected_class = _detect_device_class(self._zone_name)

    @property
    def zone(self):
        return self.data["zones"][self.entity_index]

    @property
    def unique_id(self):
        return f"{self.imei}_zone_{self._zone_id}"

    @property
    def name(self):
        return self._zone_name

    @property
    def is_on(self):
        """Zone is 'on' when violated (open door, detected motion, etc.)."""
        active_zones = self.data.get("active_zones", [])
        return any(
            az.get("internalId") == self._zone_id
            for az in active_zones
        )

    @property
    def device_class(self):
        return self._detected_class

    @property
    def extra_state_attributes(self):
        return {
            "zone_id": self._zone_id,
            "disabled": self.zone.get("disabled", False),
            "selected": self.zone.get("selected", True),
        }

    @property
    def icon(self):
        if self._detected_class:
            return None
        return "mdi:shield-alert-outline" if self.is_on else "mdi:shield-check-outline"
