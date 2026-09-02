import time
import json
import os
import ssl
import logging
import sys
import threading
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
            "mqtt_tls": os.environ.get("MQTT_TLS", "false").lower() == "true",
            "mqtt_tls_insecure": os.environ.get("MQTT_TLS_INSECURE", "false").lower() == "true",
            "scan_interval_hours": 6,
            "base_url": os.environ.get("BASE_URL"),
            "state_class": os.environ.get("STATE_CLASS", "total"),
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

if config.get("base_url") is None:
    logger.error("No base_url configured, aborting")
    sys.exit(1)

# state_class applied to the consumption sensors. "none" disables it entirely.
STATE_CLASS = (config.get("state_class") or "total").strip().lower()
if STATE_CLASS in ("", "none", "null"):
    STATE_CLASS = None

# Raw unit strings coming from the Minol API -> (Home Assistant unit, device_class)
_UNIT_MAP = {
    "KWH": ("kWh", "energy"),
    "MWH": ("MWh", "energy"),
    "WH": ("Wh", "energy"),
    "M3": ("m³", "water"),
    "M³": ("m³", "water"),
    "CBM": ("m³", "water"),
}


def ha_unit(raw, default=None):
    """Translate a Minol unit string (e.g. 'KWH', 'M3') to a Home Assistant unit."""
    key = (raw or "").strip().upper()
    if key in _UNIT_MAP:
        return _UNIT_MAP[key][0]
    return raw or default


def ha_device_class(raw, default=None):
    """Derive a Home Assistant device_class from a Minol unit string."""
    key = (raw or "").strip().upper()
    if key in _UNIT_MAP:
        return _UNIT_MAP[key][1]
    return default


mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

if config.get("mqtt_user") and config.get("mqtt_password"):
    mqtt_client.username_pw_set(config["mqtt_user"], config["mqtt_password"])

if config.get("mqtt_tls"):
    if config.get("mqtt_tls_insecure"):
        # Encrypt, but do not validate the broker certificate / hostname.
        # Needed when the broker presents a cert for a name other than the one
        # used to reach it (e.g. connecting via an internal hostname).
        mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)
        mqtt_client.tls_insecure_set(True)
        logger.warning("MQTT TLS enabled WITHOUT certificate validation (mqtt_tls_insecure)")
    else:
        mqtt_client.tls_set()
        logger.info("MQTT TLS enabled with certificate validation")

_connected = threading.Event()
_publish_failures = 0


def _on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0 or getattr(reason_code, "is_failure", False) is False:
        logger.info(f"Connected to MQTT broker {config['mqtt_host']}:{config['mqtt_port']} "
                    f"(tls={bool(config.get('mqtt_tls'))})")
        _connected.set()
    else:
        logger.error(f"MQTT connection refused: {reason_code}")


def _on_disconnect(client, userdata, *args):
    _connected.clear()
    reason = args[-2] if len(args) >= 2 else (args[0] if args else "unknown")
    logger.warning(f"Disconnected from MQTT broker (reason: {reason}); paho will retry")


mqtt_client.on_connect = _on_connect
mqtt_client.on_disconnect = _on_disconnect


def connect_mqtt():
    """Connect to the MQTT broker and block until the connection is confirmed."""
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=60)
    try:
        mqtt_client.connect(config["mqtt_host"], int(config["mqtt_port"]), keepalive=60)
    except Exception as e:
        logger.error(f"Failed to open MQTT connection to "
                     f"{config['mqtt_host']}:{config['mqtt_port']}: {e}")
        sys.exit(1)

    mqtt_client.loop_start()

    if not _connected.wait(timeout=15):
        logger.error(
            "No MQTT CONNACK within 15s. Check mqtt_host/mqtt_port/credentials and "
            "whether the broker expects TLS (set mqtt_tls: true) or plain TCP."
        )
        sys.exit(1)


def _publish(topic, payload, retain=True):
    """Publish a single MQTT message and log/track delivery failures."""
    global _publish_failures
    info = mqtt_client.publish(topic, payload, qos=1, retain=retain)
    try:
        info.wait_for_publish(timeout=10)
    except Exception:
        pass
    if info.rc != mqtt.MQTT_ERR_SUCCESS or not info.is_published():
        _publish_failures += 1
        logger.error(f"Failed to publish to {topic} (rc={info.rc}, "
                     f"published={info.is_published()})")


def publish_discovery_config(sensor_type, unique_id, name, unit, icon, device_class,
                             state_class="__unset__", attributes_topic=None):
    """Publish Home Assistant MQTT discovery configuration for automatic sensor creation."""
    topic = f"homeassistant/sensor/minol/{unique_id}/config"

    payload = {
        "name": name,
        "unique_id": f"minol_{unique_id}",
        "state_topic": f"minol/{unique_id}/state",
        "icon": icon,
        "platform": "mqtt",
        "device": {
            "identifiers": ["minol_account"],
            "name": "Minol Customer Portal",
            "manufacturer": "Minol",
            "model": "Web Scraper"
        }
    }

    if unit:
        payload["unit_of_measurement"] = unit
    if device_class:
        payload["device_class"] = device_class
    # "__unset__" -> use the globally configured state_class; explicit None/str wins.
    sc = STATE_CLASS if state_class == "__unset__" else state_class
    if sc:
        payload["state_class"] = sc
    if attributes_topic:
        payload["json_attributes_topic"] = attributes_topic

    _publish(topic, json.dumps(payload))


def publish_state(unique_id, value):
    """Publish sensor state value to MQTT."""
    _publish(f"minol/{unique_id}/state", str(value))


def publish_attributes(unique_id, attributes):
    """Publish sensor JSON attributes to MQTT."""
    _publish(f"minol/{unique_id}/attributes", json.dumps(attributes))


def _category_unit(data, category_key, default_unit, default_device_class):
    """Pick the HA unit/device_class for an aggregate sensor from its first meter."""
    rooms = (data.get(category_key) or {}).get("by_room") or []
    for room in rooms:
        raw = room.get("unit")
        if raw:
            return ha_unit(raw, default_unit), ha_device_class(raw, default_device_class)
    return default_unit, default_device_class


def _category_has_data(data, category_key):
    """True if the portal returned usable data for this category."""
    cat = data.get(category_key) or {}
    if "error" in cat:
        return False
    if cat.get("by_room"):
        return True
    for entry in cat.get("timeline", []) or []:
        try:
            if float(entry.get("value") or 0) != 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def run_sync():
    """
    Main sync cycle: authenticate, fetch data, and publish to MQTT.

    Publishes total consumption sensors, per-room/device sensors,
    DIN comparison sensors, and customer info.
    """
    global _publish_failures
    _publish_failures = 0

    if not _connected.is_set():
        logger.error("MQTT not connected; skipping this sync cycle")
        return

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

    for cat in ("heating", "hot_water", "cold_water"):
        if isinstance(data.get(cat), dict) and data[cat].get("error"):
            logger.warning(f"Portal returned no usable '{cat}' data: {data[cat]['error']}")

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

    def calculate_din_comparison(actual_timeline, reference_timeline=None):
        """Calculate percentage above/below the DIN reference."""
        def _sum(entries):
            total = 0.0
            for entry in entries or []:
                try:
                    total += float(entry.get("value", 0) or 0)
                except (TypeError, ValueError):
                    continue
            return total

        if reference_timeline is None:
            # Legacy single-list form: REF entries carry label/keyFigure "REF".
            reference_timeline = [
                e for e in (actual_timeline or [])
                if e and (e.get("label") == "REF" or e.get("keyFigure") == "REF")
            ]
            actual_timeline = [
                e for e in (actual_timeline or [])
                if e and e.get("label") != "REF" and e.get("keyFigure") != "REF"
            ]

        total_actual = _sum(actual_timeline)
        total_ref = _sum(reference_timeline)
        if total_ref > 0:
            return round(((total_actual - total_ref) / total_ref) * 100, 1)
        return None

    def publish_total(category_key, uid, name, icon, default_unit, default_device_class):
        cat = data.get(category_key)
        if not isinstance(cat, dict):
            return

        unit, device_class = _category_unit(
            data, category_key, default_unit, default_device_class
        )
        timeline = cat.get("timeline", []) or []
        reference_timeline = cat.get("reference_timeline")
        din_comparison = calculate_din_comparison(timeline, reference_timeline)

        timeline_attrs = {
            "monthly_data": [
                {
                    "period": entry.get("period"),
                    "value": entry.get("value", 0),
                    "label": entry.get("label", ""),
                }
                for entry in timeline
            ],
            "din_comparison_percent": din_comparison,
            "total_source": cat.get("total_source"),
            "cons_type": cat.get("cons_type"),
            "last_update": data.get("timestamp", ""),
        }
        if cat.get("error"):
            timeline_attrs["error"] = cat["error"]

        publish_discovery_config(
            category_key, uid, name, unit, icon, device_class,
            attributes_topic=f"minol/{uid}/attributes",
        )
        publish_attributes(uid, timeline_attrs)

        if _category_has_data(data, category_key):
            publish_state(uid, cat.get("total_consumption", 0))
        else:
            logger.warning(
                f"No '{category_key}' consumption from portal - leaving {uid} "
                f"unchanged instead of publishing 0"
            )

    publish_total("heating", "heating_total", "Minol Heating Total",
                  "mdi:radiator", "kWh", "energy")
    publish_total("hot_water", "hot_water_total", "Minol Hot Water Total",
                  "mdi:water-thermometer", "m³", "water")
    publish_total("cold_water", "cold_water_total", "Minol Cold Water Total",
                  "mdi:water-pump", "m³", "water")

    def process_rooms_extended(category_key, category_name, default_unit, icon, default_device_class):
        """Process room data and publish sensors with extended attributes and monthly history."""
        cat = data.get(category_key)
        if not isinstance(cat, dict) or "by_room" not in cat:
            return

        overall_timeline = cat.get("timeline", []) or []

        for room in cat["by_room"]:
            r_name = room.get("room_name", "Unknown")
            device_num = room.get("device_number", "")

            safe_room = "".join(c for c in r_name if c.isalnum()).lower() \
                .replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
            safe_device = "".join(c for c in str(device_num) if c.isalnum())
            uid = f"{category_key}_{safe_room}_{safe_device}" if safe_device else f"{category_key}_{safe_room}"

            val = room.get("consumption", 0)
            raw_unit = room.get("unit")
            unit = ha_unit(raw_unit, default_unit)
            device_class = ha_device_class(raw_unit, default_device_class)

            device_suffix = f" ({device_num})" if device_num else ""
            sensor_name = f"Minol {r_name} {category_name}{device_suffix}"

            extended_attrs = {
                "room_name": r_name,
                "device_number": device_num,
                "internal_key": room.get("internal_key"),
                "current_reading": room.get("reading", 0),
                "initial_reading": room.get("initial_reading", 0),
                "consumption": val,
                "evaluation_factor": room.get("evaluation_score", 0),
                "unit_raw": raw_unit or "",
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
                attributes_topic=f"minol/{uid}/attributes"
            )
            publish_state(uid, val)
            publish_attributes(uid, extended_attrs)

    process_rooms_extended("heating", "Heating", "kWh", "mdi:radiator", "energy")
    process_rooms_extended("hot_water", "Hot Water", "m³", "mdi:water-thermometer", "water")
    process_rooms_extended("cold_water", "Cold Water", "m³", "mdi:water-pump", "water")

    def publish_din_comparison(category_key, category_name):
        """Publish dedicated DIN comparison sensor."""
        cat = data.get(category_key)
        if not isinstance(cat, dict) or "timeline" not in cat:
            return

        din_comparison = calculate_din_comparison(
            cat.get("timeline"), cat.get("reference_timeline")
        )

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

    publish_din_comparison("heating", "Heating")
    publish_din_comparison("hot_water", "Hot Water")
    publish_din_comparison("cold_water", "Cold Water")

    if _publish_failures:
        logger.error(f"Sync finished with {_publish_failures} failed MQTT publish(es)")
    else:
        logger.info("Data published to MQTT successfully with all enhancements!")


if __name__ == "__main__":
    connect_mqtt()

    while True:
        try:
            run_sync()
        except Exception as e:
            logger.exception(f"Critical error in main loop: {e}")

        interval = config.get("scan_interval_hours", 12)
        logger.info(f"Sleeping for {interval} hours...")
        time.sleep(interval * 3600)
