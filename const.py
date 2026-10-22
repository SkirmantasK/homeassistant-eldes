"""Constant values for the Eldes component."""

DOMAIN = "eldes_alarm"
DEFAULT_NAME = "Eldes"

DATA_CLIENT = "eldes_client"
DATA_COORDINATOR = "coordinator"
CONF_DEVICE_IMEI = "device_imei"
CONF_EVENTS_LIST_SIZE = "events_list_size"
SCAN_INTERVAL_MIN = 5
SCAN_INTERVAL_MAX = 300
EVENTS_LIST_SIZE_MIN = 5
EVENTS_LIST_SIZE_MAX = 50

DEFAULT_SCAN_INTERVAL = 15

# Cloud push (persistent WebSocket)
DATA_LISTENER = "push_listener"
CONF_PUSH_ENABLED = "push_enabled"
CONF_PUSH_SCAN_INTERVAL = "push_scan_interval"
DEFAULT_PUSH_ENABLED = True
DEFAULT_PUSH_SCAN_INTERVAL = 120
EVENT_CLOUD_PUSH = "eldes_alarm_cloud_event"
SIGNAL_PUSH_STATE = "eldes_alarm_push_state_{}"

# Which arm buttons the alarm panel offers
CONF_ARM_MODES = "arm_modes"
ARM_MODE_AWAY = "arm_away"
ARM_MODE_HOME = "arm_home"
DEFAULT_ARM_MODES = [ARM_MODE_AWAY, ARM_MODE_HOME]
DEFAULT_EVENTS_LIST_SIZE = 10
DEFAULT_OUTPUT_ICON = "ICON_1"

API_URL = "https://cloud.eldesalarms.com:8083/api/"

API_PATHS = {
    "AUTH": "auth/",
    "DEVICE": "device/",
}

ALARM_MODES = {
    "DISARM": "disarm",
    "ARM_AWAY": "arm",
    "ARM_HOME": "armstay",
}

OUTPUT_TYPES = {
    "SWITCH": "SWITCH",
}

OUTPUT_ICONS_MAP = {
    "ICON_0": "mdi:fan",
    "ICON_1": "mdi:lightning-bolt-outline",
    "ICON_2": "mdi:power-socket-eu",
    "ICON_3": "mdi:power-plug",
}

SIGNAL_STRENGTH_MAP = {
    0: 0,
    1: 30,
    2: 60,
    3: 80,
    4: 100,
}

BATTERY_STATUS_MAP = {
    True: "OK",
    False: "Bad",
}

ATTR_EVENTS = "events"
ATTR_ALARMS = "alarms"
ATTR_USER_ACTIONS = "user_actions"

EVENT_TYPE_ALARM = "ALARM"
EVENT_TYPE_ARM = "ARM"
EVENT_TYPE_DISARM = "DISARM"
EVENT_TYPE_ZONE_TAMPER = "ZONE_TAMPER"
EVENT_TYPE_ZONE_RESTORE = "ZONE_RESTORE"
EVENT_TYPE_TROUBLE = "TROUBLE"
EVENT_TYPE_TROUBLE_RESTORE = "TROUBLE_RESTORE"
EVENT_TYPE_POWER_FAILURE = "POWER_FAILURE"
EVENT_TYPE_POWER_RESTORE = "POWER_RESTORE"
EVENT_TYPE_ZONE_BYPASS = "ZONE_BYPASS"
EVENT_TYPE_ZONE_BYPASS_RESTORE = "ZONE_BYPASS_RESTORE"

EVENT_CATEGORIES = {
    EVENT_TYPE_ALARM: "alarm",
    EVENT_TYPE_ARM: "user_action",
    EVENT_TYPE_DISARM: "user_action",
    EVENT_TYPE_ZONE_TAMPER: "alarm",
    EVENT_TYPE_ZONE_RESTORE: "status",
    EVENT_TYPE_TROUBLE: "trouble",
    EVENT_TYPE_TROUBLE_RESTORE: "status",
    EVENT_TYPE_POWER_FAILURE: "trouble",
    EVENT_TYPE_POWER_RESTORE: "status",
    EVENT_TYPE_ZONE_BYPASS: "user_action",
    EVENT_TYPE_ZONE_BYPASS_RESTORE: "user_action",
}

SERVICE_ARM_WITH_BYPASS = "arm_with_bypass"
SERVICE_ARM_HOME_WITH_BYPASS = "arm_home_with_bypass"
SERVICE_RETRY_ARM_WITH_BYPASS = "retry_arm_with_bypass"

EVENT_ARM_FAILED = "eldes_alarm_arm_failed"

ARM_FAILURE_CONTEXT_TTL_SECONDS = 120

ZONE_NAME_DEVICE_CLASS_MAP = {
    "door": "door",
    "durys": "door",
    "dur": "door",
    "gate": "door",
    "vartai": "door",
    "window": "window",
    "langas": "window",
    "lang": "window",
    "pir": "motion",
    "motion": "motion",
    "judesio": "motion",
    "judesys": "motion",
    "smoke": "smoke",
    "dumai": "smoke",
    "dum": "smoke",
    "gas": "gas",
    "dujos": "gas",
    "water": "moisture",
    "flood": "moisture",
    "vanduo": "moisture",
    "tamper": "tamper",
    "sd": "vibration",
    "stikl": "vibration",
    "glass": "vibration",
}
