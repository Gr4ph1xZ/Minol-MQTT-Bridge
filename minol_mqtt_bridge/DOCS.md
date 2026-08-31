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
| `state_class` | Home Assistant `state_class` for the consumption sensors: `total` (a meter that can be corrected/reset), `total_increasing`, `measurement`, or `none`. Minol values are period totals that reset each billing period, so `total` is recommended over `total_increasing`. | `total` |
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

### Units and heating data

* Each meter is published with the unit the Minol portal reports for it. Warm-water
  meters that are billed in kWh now appear as `kWh` / `energy` instead of `m³`.
* Some heating installations (heat cost allocators) do not expose a per-room table
  in kWh. The add-on retries in the meter's native units and, if that still returns
  nothing, falls back to the monthly chart for the heating total. If the heating
  sensor stays empty, run once with `log_level: DEBUG` and check the logged
  `EM data response content (HEIZUNG)` line.

## Sensors

Sensors are automatically created via **MQTT Auto-Discovery**.
After the first successful run, you can find them here:

1.  Go to **Settings** > **Devices & Services**.
2.  Click on the **MQTT** integration.
3.  Look for a device named **Minol Customer Portal**.

The add-on creates total consumption sensors as well as individual sensors for every radiator or water meter found in your account.