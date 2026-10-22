"""Support for Eldes control panels."""
import logging
from datetime import datetime

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DATA_CLIENT,
    DATA_COORDINATOR,
    DOMAIN,
    ALARM_MODES,
    EVENT_ARM_FAILED,
    CONF_ARM_MODES,
    DEFAULT_ARM_MODES,
    ARM_MODE_AWAY,
    ARM_MODE_HOME,
)
from . import EldesDeviceEntity

_LOGGER = logging.getLogger(__name__)

DATA_LAST_ARM_FAILURE = "_last_arm_failure"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up the Eldes alarm control panel platform."""
    client = hass.data[DOMAIN][entry.entry_id][DATA_CLIENT]
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    entities = []
    features = _features_from_modes(entry.options.get(CONF_ARM_MODES, DEFAULT_ARM_MODES))

    for device_index, device in enumerate(coordinator.data):
        for partition_index in range(len(device["partitions"])):
            entity = EldesAlarmPanel(client, coordinator, device_index, partition_index)
            entity._attr_supported_features = features
            entity._attr_alarm_state = entity.partition["state"]
            entities.append(entity)

    async_add_entities(entities)


def _features_from_modes(modes) -> AlarmControlPanelEntityFeature:
    """Map the arm modes chosen in options to panel features."""
    features = AlarmControlPanelEntityFeature(0)
    if ARM_MODE_AWAY in modes:
        features |= AlarmControlPanelEntityFeature.ARM_AWAY
    if ARM_MODE_HOME in modes:
        features |= AlarmControlPanelEntityFeature.ARM_HOME
    return features


class EldesAlarmPanel(EldesDeviceEntity, AlarmControlPanelEntity):
    """Class for the Eldes alarm control panel."""

    _attr_supported_features = (
            AlarmControlPanelEntityFeature.ARM_AWAY
            | AlarmControlPanelEntityFeature.ARM_HOME
    )
    _attr_code_arm_required = False

    def __init__(self, client, coordinator, device_index, partition_index):
        super().__init__(client, coordinator, device_index, partition_index)
        self._previous_state = None

    @property
    def partition(self):
        return self.data["partitions"][self.entity_index]

    @property
    def unique_id(self):
        return f"{self.imei}_zone_{self.partition['internalId']}"

    @property
    def name(self):
        return self.partition["name"]

    @property
    def extra_state_attributes(self):
        return {
            "armed": self.partition["armed"],
            "armStay": self.partition["armStay"],
            "state": self.partition["state"],
            "hasUnacceptedPartitionAlarms": self.partition["hasUnacceptedPartitionAlarms"],
        }

    @property
    def alarm_state(self) -> AlarmControlPanelState:
        return self.partition["state"]

    async def _async_set_alarm(
        self,
        mode: str,
        transition_state: AlarmControlPanelState,
        zones_to_bypass: list | None = None,
    ) -> None:
        self._previous_state = self.partition["state"]
        self.partition["state"] = transition_state
        self.async_write_ha_state()

        is_arming = mode in (ALARM_MODES["ARM_AWAY"], ALARM_MODES["ARM_HOME"])

        try:
            if is_arming:
                feedback = await self.client.set_alarm_with_feedback(
                    mode,
                    self.imei,
                    self.partition["internalId"],
                    zones_to_bypass=zones_to_bypass,
                )
            else:
                await self.client.set_alarm(
                    mode,
                    self.imei,
                    self.partition["internalId"],
                    zones_to_bypass=zones_to_bypass,
                )
                feedback = None
        except Exception as ex:
            _LOGGER.error("Failed to set alarm (%s): %s", mode, ex)
            self.partition["state"] = self._previous_state
            self.async_write_ha_state()
            raise

        if is_arming and feedback and feedback.get("active_zones"):
            active_zones = feedback["active_zones"]
            zone_names = [z.get("name", "Unknown") for z in active_zones]
            bypass_ids = [z.get("internalId") for z in active_zones if z.get("internalId")]

            _LOGGER.info(
                "Arming failed for %s — active zones: %s",
                self.partition["name"],
                ", ".join(zone_names),
            )

            self.partition["state"] = AlarmControlPanelState.DISARMED
            self.async_write_ha_state()

            self.hass.data[DOMAIN].setdefault(DATA_LAST_ARM_FAILURE, {})
            self.hass.data[DOMAIN][DATA_LAST_ARM_FAILURE] = {
                "imei": self.imei,
                "partition_id": self.partition["internalId"],
                "mode": mode,
                "bypass_zones": bypass_ids,
                "zone_names": zone_names,
                "entity_id": self.entity_id,
                "timestamp": datetime.now(),
            }

            self.hass.bus.async_fire(
                EVENT_ARM_FAILED,
                {
                    "entity_id": self.entity_id,
                    "partition_name": self.partition["name"],
                    "partition_id": self.partition["internalId"],
                    "mode": mode,
                    "active_zones": zone_names,
                },
                context=self._context,
            )

    async def async_alarm_disarm(self, code=None) -> None:
        await self._async_set_alarm(
            ALARM_MODES["DISARM"],
            AlarmControlPanelState.DISARMING
        )

    async def async_alarm_arm_away(self, code=None) -> None:
        await self._async_set_alarm(
            ALARM_MODES["ARM_AWAY"],
            AlarmControlPanelState.ARMING
        )

    async def async_alarm_arm_home(self, code=None) -> None:
        await self._async_set_alarm(
            ALARM_MODES["ARM_HOME"],
            AlarmControlPanelState.ARMING
        )

    async def async_arm_with_bypass(self, mode: str, zones_to_bypass: list | None = None) -> None:
        """Arm with optional zone bypass, called from service handler."""
        await self._async_set_alarm(
            mode,
            AlarmControlPanelState.ARMING,
            zones_to_bypass=zones_to_bypass,
        )
