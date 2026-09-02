# Minol MQTT Bridge

This add-on retrieves your consumption data (heating, hot water, cold water) from the Minol Customer Portal and publishes it to Home Assistant via MQTT.

It uses a browser automation (Playwright) to log in securely and fetch the latest data available in your account.

## Prerequisites

* You must have a valid account for the [Minol Customer Portal](https://webservices.minol.com).
* You need an MQTT Broker installed and configured in Home Assistant (e.g., the official **Mosquitto broker** add-on).

## Installation & Setup

1.  Ensure your MQTT Broker is running.
2.  Install this add-on from the add-on store.
3.  Go to the **Configuration** tab.
4.  Enter your Minol **email** and **password**.
5.  Configure the MQTT connection settings (usually, the defaults work if you use the official Mosquitto broker).
6.  Start the add-on.

## Configuration Options

| Option | Description | Default |
| :--- | :--- | :--- |
| `minol_email` | Your login email for the Minol portal. | - |
| `minol_password` | Your login password. | - |
| `mqtt_host` | Hostname of your broker. Use `core-mosquitto` for the internal add-on. | `core-mosquitto` |
| `mqtt_port` | Port of your MQTT broker. Use `1883` for plain TCP, `8883` for TLS. | `1883` |
| `mqtt_user` | MQTT username (optional). | - |
| `mqtt_password` | MQTT password (optional). | - |
| `mqtt_tls` | Connect to the broker over TLS. Enable this when `mqtt_port` is `8883`. | `false` |
| `mqtt_tls_insecure` | With `mqtt_tls`, skip certificate/hostname validation. Needed when the broker's certificate does not match the host you connect to (e.g. an internal name). | `false` |
| `scan_interval_hours` | How often the data should be updated (in hours). | `12` |
| `state_class` | Home Assistant `state_class` for the consumption sensors: `total_increasing`, `total`, `measurement`, or `none`. See [State class and the Energy Dashboard](#state-class-and-the-energy-dashboard). | `total_increasing` |
| `log_level` | Logging verbosity (DEBUG, INFO, WARNING, ERROR). | `INFO` |

### Connecting to a TLS-only broker

If your Mosquitto broker only accepts encrypted connections (port `8883`), set:

```yaml
mqtt_host: core-mosquitto
mqtt_port: 8883
mqtt_tls: true
mqtt_tls_insecure: true   # only if the broker cert is issued for a different hostname
```

With plain `mqtt_tls: true` the broker certificate is validated against `mqtt_host`;
use `mqtt_tls_insecure: true` if that name is not on the certificate.

### Units

* Heating is published in `kWh` (`device_class: energy`).
* Hot and cold water are published in `m³` (`device_class: water`).

> **Note for users upgrading from 1.1.0 or earlier:** hot water used to be
> published as `kWh`. That was wrong — the value was the meter's m³ reading
> carrying an energy unit, because the portal relabels the unit when asked to
> convert to kWh but does not convert that particular field. Hot water is now
> requested in its native m³ and the numbers are unchanged, only correctly
> labelled. Home Assistant will refuse the new unit for the existing
> statistics, so delete the old ones once: **Developer Tools → Statistics**,
> find `sensor.minol_hot_water_*`, and use *Fix issue* / delete.

### State class and the Energy Dashboard

The portal has no notion of a billing period in this API. It reports the meter
reading at the start of the window you ask for, the reading at the end, and the
difference — so the answer depends entirely on the window requested.

The add-on therefore anchors the start of that window far in the past and lets
the portal clamp to the earliest reading it holds. Consumption is then measured
from a fixed point and **only ever grows**, which is exactly what
`total_increasing` and the Energy Dashboard expect. (A window that slid with the
clock — say, always the last 12 months — would instead fall whenever a
high-consumption month dropped out of it, and Home Assistant would read that
fall as a meter reset and double-count.)

The sensors can be added to **Settings → Dashboards → Energy** directly; no
template sensors or filtering are needed in between.

The trade-off of `total_increasing` is that any drop counts as a meter reset. On
a monotonic series the only legitimate drop is a meter exchange, which is
genuinely a reset. But it also means a spurious 0 would make Home Assistant book
the recovery as fresh consumption and double-count. The add-on therefore never
publishes a placeholder 0:

* a failed login or a failed category leaves the retained value untouched
* a meter row that carries no consumption field at all is skipped rather than
  published as 0 (look for `reported no consumption value` in the log)
* the add-on publishes an availability topic (`minol/status`) with an MQTT Last
  Will, so if it stops or loses the broker the sensors go **unavailable** rather
  than showing a stale reading as if it were current

### How the heating meter is detected

The Minol portal keys heating by the metering technology installed in your flat,
and only one of the two ever holds data:

| Portal key | Metering technology |
| :--- | :--- |
| `HEIZUNG` | Heat cost allocators (Heizkostenverteiler), allocator units |
| `HZKWH` | Heat meters (Wärmezähler) reporting kWh |

The add-on tries both and uses whichever actually returns meters, so no
configuration is needed. The key that was used is published on the total
sensor as the `cons_type` attribute — useful when opening an issue.

If a consumption sensor stays empty, run once with `log_level: DEBUG` and look
for the line `<category>: consType=<key> accepted but carries no values`. That
means the portal has no data for your account in the requested period rather
than the add-on asking the wrong question.

## Sensors

Sensors are automatically created via **MQTT Auto-Discovery**.
After the first successful run, you can find them here:

1.  Go to **Settings** > **Devices & Services**.
2.  Click on the **MQTT** integration.
3.  Look for a device named **Minol Customer Portal**.

The add-on creates total consumption sensors as well as individual sensors for every radiator or water meter found in your account.