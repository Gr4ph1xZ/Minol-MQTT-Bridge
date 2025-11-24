import time
import json
import os
import logging
import sys
import paho.mqtt.client as mqtt
from minol_connector import MinolConnector

OPTIONS_PATH = '/data/options.json'


def load_config():
    """Load configuration from Home Assistant options.json or environment variables."""
    if os.path.exists(OPTIONS_PATH):
        with open(OPTIONS_PATH, 'r') as f:
            return json.load(f)
    else:
        return {
            "minol_email": os.environ.get("MINOL_EMAIL"),
            "minol_password": os.environ.get("MINOL_PASSWORD"),
            "mqtt_host": os.environ.get("MQTT_HOST", "localhost"),
            "mqtt_port": int(os.environ.get("MQTT_PORT", 1883)),
            "mqtt_user": os.environ.get("MQTT_USER"),
            "mqtt_password": os.environ.get("MQTT_PASSWORD"),
            "scan_interval_hours": 6,
            "base_url": os.environ.get("BASE_URL"),
            "log_level": os.environ.get("LOG_LEVEL", "INFO")
        }


config = load_config()

log_level_str = config.get("log_level", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger("MinolBridge")
logger.info(f"Log level set to: {log_level_str}")

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

if config.get("mqtt_user") and config.get("mqtt_password"):
    mqtt_client.username_pw_set(config["mqtt_user"], config["mqtt_password"])


def connect_mqtt():
    """Connect to the MQTT broker."""
    try:
        mqtt_client.connect(config["mqtt_host"], config["mqtt_port"], 60)
        mqtt_client.loop_start()
        logger.info("Connected to MQTT Broker")
    except Exception as e:
        logger.error(f"Failed to connect to MQTT: {e}")
        sys.exit(1)


def publish_discovery_config(sensor_type, unique_id, name, unit, icon, device_class, state_class="total_increasing", attributes_topic=None):
    """Publish Home Assistant MQTT discovery configuration for automatic sensor creation."""
    topic = f"homeassistant/sensor/minol/{unique_id}/config"

    payload = {
        "name": name,
        "unique_id": f"minol_{unique_id}",
        "state_topic": f"minol/{unique_id}/state",
        "unit_of_measurement": unit,
        "device_class": device_class,
        "state_class": state_class,
        "icon": icon,
        "platform": "mqtt",
        "device": {
            "identifiers": ["minol_account"],
            "name": "Minol Customer Portal",
            "manufacturer": "Minol",
            "model": "Web Scraper"
        }
    }

    if attributes_topic:
        payload["json_attributes_topic"] = attributes_topic

    mqtt_client.publish(topic, json.dumps(payload), qos=0, retain=True)


def publish_state(unique_id, value):
    """Publish sensor state value to MQTT."""
    topic = f"minol/{unique_id}/state"
    mqtt_client.publish(topic, str(value), qos=0, retain=True)


def publish_attributes(unique_id, attributes):
    """Publish sensor JSON attributes to MQTT."""
    topic = f"minol/{unique_id}/attributes"
    mqtt_client.publish(topic, json.dumps(attributes), qos=0, retain=True)


def run_sync():
    """
    Main sync cycle: authenticate, fetch data, and publish to MQTT.

    Publishes total consumption sensors, per-room/device sensors,
    DIN comparison sensors, and customer info.
    """
    connector = MinolConnector(config["minol_email"], config["minol_password"], config["base_url"])

    logger.info("Starting authentication...")
    if not connector.authenticate():
        logger.error("Authentication failed. Retrying next cycle.")
        return

    logger.info("Fetching consumption data...")
    data = connector.get_consumption_data(months_back=12, force_update=True)

    if not data:
        logger.error("No data received.")
        return

    user_info = connector.user_info
    if user_info:
        logger.info("Publishing customer data sensor...")

        address_parts = [
            user_info.get("addrStreet", ""),
            user_info.get("addrHouseNum", ""),
            user_info.get("addrPostalCode", ""),
            user_info.get("addrCity", "")
        ]
        full_address = " ".join([p for p in address_parts if p]).strip()

        customer_attrs = {
            "email": user_info.get("email", ""),
            "customer_number": user_info.get("userNumber", ""),
            "tenant_number": user_info.get("nenr", "").strip(),
            "property_number": user_info.get("lgnr", "").strip(),
            "floor": user_info.get("geschossText", ""),
            "position": user_info.get("lageText", ""),
            "address": full_address,
            "name": user_info.get("name", ""),
            "move_in_date": user_info.get("einzugMieter", ""),
        }

        publish_discovery_config(
            "info",
            "customer_info",
            "Minol Customer Info",
            "",
            "mdi:account",
            None,
            state_class=None,
            attributes_topic="minol/customer_info/attributes"
        )
        publish_state("customer_info", customer_attrs.get("customer_number", "N/A"))
        publish_attributes("customer_info", customer_attrs)

    def calculate_din_comparison(timeline):
        """Calculate percentage above/below DIN average."""
        if not timeline:
            return None

        try:
            total_actual = sum(
                float(entry.get("value", 0) or 0)
                for entry in timeline
                if entry and entry.get("label") != "REF"
            )
            total_ref = sum(
                float(entry.get("value", 0) or 0)
                for entry in timeline
                if entry and entry.get("label") == "REF"
            )

            if total_ref > 0:
                diff_percent = ((total_actual - total_ref) / total_ref) * 100
                return round(diff_percent, 1)
        except (TypeError, ValueError) as e:
            logger.warning(f"Error calculating DIN comparison: {e}")

        return None

    if "heating" in data and "total_consumption" in data["heating"]:
        val = data["heating"]["total_consumption"]
        timeline = data["heating"].get("timeline", [])
        din_comparison = calculate_din_comparison(timeline)

        # Build timeline attributes
        timeline_attrs = {
            "monthly_data": [
                {
                    "period": entry.get("period"),
                    "value": entry.get("value", 0),
                    "label": entry.get("label", "")
                }
                for entry in timeline
            ],
            "din_comparison_percent": din_comparison,
            "last_update": data.get("timestamp", ""),
        }

        publish_discovery_config(
            "heating",
            "heating_total",
            "Minol Heating Total",
            "kWh",
            "mdi:radiator",
            "energy",
            state_class="total_increasing",
            attributes_topic="minol/heating_total/attributes"
        )
        publish_state("heating_total", val)
        publish_attributes("heating_total", timeline_attrs)

    if "hot_water" in data and "total_consumption" in data["hot_water"]:
        val = data["hot_water"]["total_consumption"]
        timeline = data["hot_water"].get("timeline", [])
        din_comparison = calculate_din_comparison(timeline)

        timeline_attrs = {
            "monthly_data": [
                {
                    "period": entry.get("period"),
                    "value": entry.get("value", 0),
                    "label": entry.get("label", "")
                }
                for entry in timeline
            ],
            "din_comparison_percent": din_comparison,
            "last_update": data.get("timestamp", ""),
        }

        publish_discovery_config(
            "water",
            "hot_water_total",
            "Minol Hot Water Total",
            "m³",
            "mdi:water-thermometer",
            "water",
            state_class="total_increasing",
            attributes_topic="minol/hot_water_total/attributes"
        )
        publish_state("hot_water_total", val)
        publish_attributes("hot_water_total", timeline_attrs)

    if "cold_water" in data and "total_consumption" in data["cold_water"]:
        val = data["cold_water"]["total_consumption"]
        timeline = data["cold_water"].get("timeline", [])
        din_comparison = calculate_din_comparison(timeline)

        timeline_attrs = {
            "monthly_data": [
                {
                    "period": entry.get("period"),
                    "value": entry.get("value", 0),
                    "label": entry.get("label", "")
                }
                for entry in timeline
            ],
            "din_comparison_percent": din_comparison,
            "last_update": data.get("timestamp", ""),
        }

        publish_discovery_config(
            "water",
            "cold_water_total",
            "Minol Cold Water Total",
            "m³",
            "mdi:water-pump",
            "water",
            state_class="total_increasing",
            attributes_topic="minol/cold_water_total/attributes"
        )
        publish_state("cold_water_total", val)
        publish_attributes("cold_water_total", timeline_attrs)

    def process_rooms_extended(category_key, category_name, unit, icon, device_class):
        """Process room data and publish sensors with extended attributes and monthly history."""
        if category_key not in data or "by_room" not in data[category_key]:
            return

        overall_timeline = data[category_key].get("timeline", [])

        for room in data[category_key]["by_room"]:
            r_name = room.get("room_name", "Unknown")
            device_num = room.get("device_number", "")

            safe_room = "".join(c for c in r_name if c.isalnum()).lower()
            safe_device = "".join(c for c in str(device_num) if c.isalnum())
            uid = f"{category_key}_{safe_room}_{safe_device}" if safe_device else f"{category_key}_{safe_room}"

            val = room.get("consumption", 0)

            device_suffix = f" ({device_num})" if device_num else ""
            sensor_name = f"Minol {r_name} {category_name}{device_suffix}"

            extended_attrs = {
                "room_name": r_name,
                "device_number": device_num,
                "current_reading": room.get("reading", 0),
                "initial_reading": room.get("initial_reading", 0),
                "consumption": val,
                "evaluation_factor": room.get("evaluation_score", 0),
                "unit_raw": room.get("unit", ""),
                "consumption_evaluated": room.get("consumption_evaluated", 0),
            }

            extended_attrs["monthly_history"] = {
                "overall_timeline": [
                    {
                        "period": entry.get("period"),
                        "value": entry.get("value", 0),
                    }
                    for entry in overall_timeline
                ],
                "note": "Per-room timeline not available from API. Showing overall consumption timeline.",
            }

            publish_discovery_config(
                category_key,
                uid,
                sensor_name,
                unit,
                icon,
                device_class,
                state_class="total_increasing",
                attributes_topic=f"minol/{uid}/attributes"
            )
            publish_state(uid, val)
            publish_attributes(uid, extended_attrs)

    process_rooms_extended("heating", "Heating", "kWh", "mdi:radiator", "energy")
    process_rooms_extended("hot_water", "Hot Water", "m³", "mdi:water-thermometer", "water")
    process_rooms_extended("cold_water", "Cold Water", "m³", "mdi:water-pump", "water")

    def publish_din_comparison(category_key, category_name, unit):
        """Publish dedicated DIN comparison sensor."""
        if category_key not in data or "timeline" not in data[category_key]:
            return

        timeline = data[category_key]["timeline"]
        din_comparison = calculate_din_comparison(timeline)

        if din_comparison is not None:
            uid = f"{category_key}_din_comparison"
            sensor_name = f"Minol {category_name} DIN Comparison"

            publish_discovery_config(
                category_key,
                uid,
                sensor_name,
                "%",
                "mdi:chart-line",
                None,
                state_class="measurement",
                attributes_topic=f"minol/{uid}/attributes"
            )
            publish_state(uid, din_comparison)

            interpretation = "above average" if din_comparison > 0 else "below average"
            attrs = {
                "interpretation": interpretation,
                "din_comparison_percent": din_comparison,
            }
            publish_attributes(uid, attrs)

    publish_din_comparison("heating", "Heating", "kWh")
    publish_din_comparison("hot_water", "Hot Water", "m³")
    publish_din_comparison("cold_water", "Cold Water", "m³")

    logger.info("Data published to MQTT successfully with all enhancements!")


if __name__ == "__main__":
    connect_mqtt()

    while True:
        try:
            run_sync()
        except Exception as e:
            logger.error(f"Critical error in main loop: {e}")

        interval = config.get("scan_interval_hours", 12)
        logger.info(f"Sleeping for {interval} hours...")
        time.sleep(interval * 3600)