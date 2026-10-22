"""Support for Eldes sensors."""
import logging
from datetime import datetime

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import PERCENTAGE, UnitOfTemperature

from .const import (
    DATA_CLIENT,
    DATA_COORDINATOR,
    DOMAIN,
    SIGNAL_STRENGTH_MAP,
    BATTERY_STATUS_MAP,
    ATTR_EVENTS,
    ATTR_ALARMS,
    ATTR_USER_ACTIONS,
    EVENT_CATEGORIES,
)
from . import EldesDeviceEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the Eldes sensor platform."""
    client = hass.data[DOMAIN][entry.entry_id][DATA_CLIENT]
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    entities = []

    for index in range(len(coordinator.data)):
        entities.append(EldesBatteryStatusSensor(client, coordinator, index))
        entities.append(EldesGSMStrengthSensor(client, coordinator, index))
        entities.append(EldesPhoneNumberSensor(client, coordinator, index))
        entities.append(EventsSensor(client, coordinator, index))
        entities.append(EldesSystemFaultsSensor(client, coordinator, index))
        for temp_index in range(len(coordinator.data[index]["temp"])):
            entities.append(EldesTemperatureSensor(client, coordinator, index, temp_index))

    async_add_entities(entities)


class EldesBatteryStatusSensor(EldesDeviceEntity, SensorEntity):
    """Class for the battery status sensor."""

    @property
    def unique_id(self):
        return f"{self.imei}_battery_status"

    @property
    def name(self):
        return f"{self.data['info']['model']} Battery Status"

    @property
    def icon(self):
        return "mdi:battery" if self.data["info"].get("batteryStatus") else "mdi:battery-alert-variant-outline"

    @property
    def native_value(self):
        return BATTERY_STATUS_MAP[self.data["info"].get("batteryStatus", False)]


class EldesGSMStrengthSensor(EldesDeviceEntity, SensorEntity):
    """Class for the GSM strength sensor."""

    @property
    def unique_id(self):
        return f"{self.imei}_gsm_strength"

    @property
    def name(self):
        return f"{self.data['info']['model']} GSM Strength"

    @property
    def icon(self):
        return "mdi:signal" if self.data["info"].get("gsmStrength", 0) > 0 else "mdi:signal-off"

    @property
    def native_unit_of_measurement(self):
        return PERCENTAGE

    @property
    def native_value(self):
        return SIGNAL_STRENGTH_MAP[self.data["info"].get("gsmStrength", 0)]


class EldesPhoneNumberSensor(EldesDeviceEntity, SensorEntity):
    """Class for the phone number sensor."""

    @property
    def unique_id(self):
        return f"{self.imei}_phone_number"

    @property
    def name(self):
        return f"{self.data['info']['model']} Phone Number"

    @property
    def icon(self):
        return "mdi:cellphone"

    @property
    def native_value(self):
        return self.data["info"].get("phoneNumber", "")


class EldesTemperatureSensor(EldesDeviceEntity, SensorEntity):
    """Class for the temperature sensor."""

    @property
    def temp(self):
        return self.data["temp"][self.entity_index]

    @property
    def unique_id(self):
        return f"{self.imei}_{self.temp['sensorName']}_{self.temp['sensorId']}_temperature"

    @property
    def name(self):
        return f"{self.temp['sensorName']} Temperature"

    @property
    def device_class(self):
        return SensorDeviceClass.TEMPERATURE

    @property
    def native_unit_of_measurement(self):
        return UnitOfTemperature.CELSIUS

    @property
    def native_value(self):
        return self.temp.get("temperature", 0.0)


class EventsSensor(EldesDeviceEntity, SensorEntity):
    """Class for the events sensor with categorization."""

    @property
    def unique_id(self):
        return f"{self.imei}_events"

    @property
    def name(self):
        return "Events"

    @property
    def native_value(self):
        return len(self.data.get("events", []))

    @property
    def extra_state_attributes(self):
        events = []
        alarms = []
        user_actions = []
        troubles = []

        for event in self.data.get("events", []):
            event_type = event.get("type", "")
            category = EVENT_CATEGORIES.get(event_type, "other")
            enriched = self._enrich_event(event)

            if category == "alarm":
                alarms.append(enriched)
            elif category == "user_action":
                user_actions.append(self._add_name(enriched))
            elif category == "trouble":
                troubles.append(enriched)
            else:
                events.append(enriched)

        return {
            ATTR_EVENTS: events,
            ATTR_ALARMS: alarms,
            ATTR_USER_ACTIONS: user_actions,
            "troubles": troubles,
        }

    @property
    def icon(self):
        return "mdi:calendar"

    def _add_name(self, event):
        new_event = event.copy()
        message = new_event.get("message", "")
        name = message.split(" ")[0] if message else ""
        new_event["name"] = name
        return new_event

    def _enrich_event(self, event):
        new_event = event.copy()
        new_event["event_time"] = self._parse_device_time(
            new_event.get("deviceTime", [])
        )
        new_event["category"] = EVENT_CATEGORIES.get(
            new_event.get("type", ""), "other"
        )
        return new_event

    @staticmethod
    def _parse_device_time(device_time):
        if not device_time or not isinstance(device_time, list):
            return None
        try:
            return datetime(
                device_time[0] if len(device_time) > 0 else 2000,
                device_time[1] if len(device_time) > 1 else 1,
                device_time[2] if len(device_time) > 2 else 1,
                device_time[3] if len(device_time) > 3 else 0,
                device_time[4] if len(device_time) > 4 else 0,
                device_time[5] if len(device_time) > 5 else 0,
            )
        except (ValueError, TypeError):
            return None


class EldesSystemFaultsSensor(EldesDeviceEntity, SensorEntity):
    """Sensor showing count and details of active system faults."""

    @property
    def unique_id(self):
        return f"{self.imei}_system_faults"

    @property
    def name(self):
        return f"{self.data['info']['model']} System Faults"

    @property
    def native_value(self):
        return len(self.data.get("system_faults", []))

    @property
    def extra_state_attributes(self):
        return {"faults": self.data.get("system_faults", [])}

    @property
    def icon(self):
        faults = self.data.get("system_faults", [])
        return "mdi:alert-circle" if faults else "mdi:check-circle"
