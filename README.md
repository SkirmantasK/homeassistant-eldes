# Eldes Alarm for Home Assistant

A custom Home Assistant integration for [Eldes](https://eldesalarms.com/) security systems (ESIM364, Pitbull, etc.) via the Eldes Cloud API. It adds arming with zone bypass and near-instant updates through the same cloud WebSocket the Eldes mobile app uses.

- **Domain:** `eldes_alarm`
- **Version:** 1.1.1
- **IoT class:** cloud polling + cloud push

## Features

- Alarm control panel per partition (arm away, arm home/stay, disarm)
- Arm with zone bypass, plus a one-call retry that bypasses whichever zones blocked the last arm attempt
- Instant updates via a persistent STOMP-over-WebSocket connection to Eldes Cloud. REST polling stays on as a fallback.
- Zone binary sensors whose device class (door, window, motion, smoke, gas, moisture, vibration, tamper) is guessed from the zone name. English and Lithuanian keywords are recognised.
- Output switches (PGM outputs)
- Temperature, GSM signal, battery, phone number, system fault and event history sensors
- Event entity that fires on each new panel event (alarm, user action, trouble, status)
- Multiple devices per account, all sharing one cloud session
- Translations: English, German, French, Lithuanian, Russian

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=SkirmantasK&repository=homeassistant-eldes&category=integration)

Or add it by hand:

1. In HACS, open the **⋮** menu → **Custom repositories**.
2. Add `https://github.com/SkirmantasK/homeassistant-eldes` with type **Integration**.
3. Search for **Eldes Alarm with Bypass**, download it, and restart Home Assistant.

### Manual

Copy `custom_components/eldes_alarm` from this repository into `/config/custom_components/` and restart Home Assistant.

Requires Home Assistant 2024.12 or newer.

## Configuration

[![Open your Home Assistant instance and start setting up the Eldes integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=eldes_alarm)

1. Go to **Settings → Devices & Services → Add Integration** and search for **Eldes Alarm with Bypass**.
2. Enter your Eldes Cloud email, password and the alarm user **PIN**. The PIN is sent along with arm/disarm commands.
3. Pick the device (location) to add. To add more devices from the same account, run the flow again.

### Options

| Option | Default | Range | Description |
| --- | --- | --- | --- |
| Arm modes shown on the panel | Away + Home | at least one | Which arm buttons the alarm panel shows |
| Instant updates via cloud push | on | — | Keep a WebSocket open to Eldes Cloud |
| Polling interval while push is connected | 120 s | 5–3600 | Slower safety-net polling while push is up |
| Scan interval | 15 s | 5–300 | Polling interval when push is off or disconnected |
| Events list size | 10 | 5–50 | Number of recent events fetched |
| PIN code | — | — | Change the PIN used for commands |

Saving options reloads the integration.

## Entities

For each device:

| Platform | Entity | Notes |
| --- | --- | --- |
| `alarm_control_panel` | one per partition | Attributes: `armed`, `armStay`, `state`, `hasUnacceptedPartitionAlarms` |
| `binary_sensor` | Connection Status | Whether the panel is online in Eldes Cloud |
| `binary_sensor` | Cloud Push | Diagnostic. WebSocket state, `messages_received`, `last_error` |
| `binary_sensor` | one per enabled zone | Turns on when the zone is reported active |
| `switch` | one per output | Turns the panel outputs on and off |
| `sensor` | Battery Status | `OK` / `Bad` |
| `sensor` | GSM Strength | % |
| `sensor` | Phone Number | |
| `sensor` | Temperature | one per temperature sensor |
| `sensor` | System Faults | Fault count, with details in the `faults` attribute |
| `sensor` | Events | Event count. Attributes split events into `events`, `alarms`, `user_actions` and `troubles` |
| `event` | Event Feed | Event types: `alarm`, `user_action`, `trouble`, `status`, `other` |

## Services

### `eldes_alarm.arm_with_bypass` / `eldes_alarm.arm_home_with_bypass`

Arm away / arm home while bypassing zones.

| Field | Description |
| --- | --- |
| `bypass_zones` | List of zone IDs to bypass, e.g. `[7, 12]` |
| `bypass_all` | Bypass all enabled zones (default `false`) |

```yaml
action: eldes_alarm.arm_with_bypass
target:
  entity_id: alarm_control_panel.house
data:
  bypass_zones: [7, 12]
```

> **Note:** these services currently arm **every partition of every configured Eldes device**, whatever `target` you give them.

### `eldes_alarm.retry_arm_with_bypass`

If an arm attempt fails because zones are open, the integration remembers the partition and the open zones for 120 seconds. Calling this service re-arms that partition with those zones bypassed. It takes no fields.

## Events

| Event | When | Data |
| --- | --- | --- |
| `eldes_alarm_arm_failed` | Arming was refused because zones are active | `entity_id`, `partition_name`, `partition_id`, `mode`, `active_zones` (names) |
| `eldes_alarm_cloud_event` | A push message arrived over the WebSocket | `imei`, `topic`, `payload` (raw) |

### Example: offer to bypass on failed arm

```yaml
automation:
  - alias: Eldes arm failed – notify with bypass action
    triggers:
      - trigger: event
        event_type: eldes_alarm_arm_failed
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >
            Could not arm {{ trigger.event.data.partition_name }}:
            {{ trigger.event.data.active_zones | join(', ') }} open.
          data:
            actions:
              - action: ELDES_BYPASS
                title: Arm with bypass

  - alias: Eldes – bypass from notification
    triggers:
      - trigger: event
        event_type: mobile_app_notification_action
        event_data:
          action: ELDES_BYPASS
    actions:
      - action: eldes_alarm.retry_arm_with_bypass
```

## How it works

- **REST** (`custom_components/eldes_alarm/core/eldes_cloud.py`): calls `https://cloud.eldesalarms.com:8083/api/` for login, token renewal, device info, partitions, zones, outputs, temperatures, events, faults and commands. A `DataUpdateCoordinator` polls it. If one endpoint fails, its last good value is kept, so a brief outage doesn't make entities unavailable.
- **Push** (`custom_components/eldes_alarm/core/eldes_ws.py`): keeps a STOMP session open on `wss://cloud.eldesalarms.com:8083/eldes/websocket` and subscribes to `/user/queue/device/event` and `/user/queue/device/response`. Each message triggers a coordinator refresh and an `eldes_alarm_cloud_event`. The session reconnects with exponential backoff (5–300 s) and is reopened with a fresh token every hour.
- **Arm feedback:** each arm command opens a short-lived WebSocket to read the command response. That response lists any active zones, which feed `eldes_alarm_arm_failed` and the retry service.

## Troubleshooting

Enable debug logging:

```yaml
logger:
  logs:
    custom_components.eldes_alarm: debug
```

- **Login failed:** check the email and password in the Eldes Cloud app. A wrong PIN does not stop you logging in, but arm/disarm commands will fail.
- **Event Feed shows "unknown":** this is normal until the first new panel event arrives after setup. Events already in the history are not replayed. Its state is the time of the last event.
- **Slow updates:** check the *Cloud Push* diagnostic sensor. If it shows disconnected, read its `last_error` attribute.

## Credits

Based on the Eldes integration by [@augustas2](https://github.com/augustas2) and [@simie7](https://github.com/simie7).

## License

[MIT](LICENSE), inherited from the original project.

This project is not affiliated with or endorsed by ELDES UAB.
