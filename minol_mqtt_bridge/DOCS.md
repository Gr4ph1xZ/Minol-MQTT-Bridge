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
| `mqtt_port` | Port of your MQTT broker. | `1883` |
| `mqtt_user` | MQTT username (optional). | - |
| `mqtt_password` | MQTT password (optional). | - |
| `scan_interval_hours` | How often the data should be updated (in hours). | `12` |
| `log_level` | Logging verbosity (DEBUG, INFO, WARNING, ERROR). | `INFO` |

## Sensors

Sensors are automatically created via **MQTT Auto-Discovery**.
After the first successful run, you can find them here:

1.  Go to **Settings** > **Devices & Services**.
2.  Click on the **MQTT** integration.
3.  Look for a device named **Minol Customer Portal**.

The add-on creates total consumption sensors as well as individual sensors for every radiator or water meter found in your account.