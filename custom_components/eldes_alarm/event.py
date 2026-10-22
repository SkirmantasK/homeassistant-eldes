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

    def __init__(self, client, coordinator, device_index):
        super().__init__(client, coordinator, device_index)
        # None until the first refresh, so events already in the list at
        # startup are not replayed as new.
        self._seen_events = None

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
        """Fire events for new entries since last update.

        The cloud returns a fixed-size window of the newest events, so new
        entries are found by identity rather than by the list growing.
        """
        events = self.data.get("events", [])
        if not events:
            # A failed fetch yields an empty list; keep what we have seen.
            self.async_write_ha_state()
            return

        fingerprints = [_fingerprint(event) for event in events]

        if self._seen_events is not None:
            new_events = [
                event for event, fp in zip(events, fingerprints)
                if fp not in self._seen_events
            ]
            # The API lists newest first; fire in chronological order.
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

        self._seen_events = set(fingerprints)
        self.async_write_ha_state()


def _fingerprint(event: dict) -> tuple:
    """Identify an event across refreshes."""
    if event.get("id") is not None:
        return ("id", event["id"])
    return (
        tuple(event.get("deviceTime") or []),
        event.get("type", ""),
        event.get("message", ""),
        event.get("partition", ""),
        event.get("zone", ""),
    )
