"""Support for Eldes event entities."""
import logging

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    DATA_CLIENT,
    DATA_COORDINATOR,
    DOMAIN,
    EVENT_CATEGORIES,
)
from . import EldesDeviceEntity

_LOGGER = logging.getLogger(__name__)

EVENT_TYPES = list(set(EVENT_CATEGORIES.values())) + ["other"]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Set up Eldes event entities."""
    client = hass.data[DOMAIN][entry.entry_id][DATA_CLIENT]
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    entities = []

    for index in range(len(coordinator.data)):
        entities.append(EldesEventEntity(client, coordinator, index))

    async_add_entities(entities)


class EldesEventEntity(EldesDeviceEntity, EventEntity):
    """Fires HA events for each new Eldes alarm event."""

    _attr_event_types = EVENT_TYPES
    _last_event_count = 0

    @property
    def unique_id(self):
        return f"{self.imei}_event_feed"

    @property
    def name(self):
        return f"{self.data['info']['model']} Event Feed"

    @property
    def icon(self):
        return "mdi:bell-ring-outline"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire events for new entries since last update."""
        events = self.data.get("events", [])
        current_count = len(events)

        if current_count > self._last_event_count and self._last_event_count > 0:
            new_events = events[: current_count - self._last_event_count]
            for event in reversed(new_events):
                event_type = EVENT_CATEGORIES.get(
                    event.get("type", ""), "other"
                )
                self._trigger_event(
                    event_type,
                    {
                        "type": event.get("type", ""),
                        "message": event.get("message", ""),
                        "partition": event.get("partition", ""),
                        "zone": event.get("zone", ""),
                    },
                )

        self._last_event_count = current_count
        self.async_write_ha_state()
