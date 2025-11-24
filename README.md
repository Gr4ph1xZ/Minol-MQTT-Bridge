# Minol MQTT Bridge for Home Assistant

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Add--on-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A Home Assistant add-on that automatically fetches consumption data (heating, hot water, and cold water) from the Minol customer portal and publishes it to MQTT for seamless integration with Home Assistant.

> **Note**: This is an unofficial integration and is not affiliated with or endorsed by Minol.

## Features

### Core Features
- **Automatic Authentication**: Uses Playwright to handle complex SAML/Azure B2C authentication flow
- **Home Assistant Auto-Discovery**: Sensors appear automatically in Home Assistant via MQTT discovery
- **Comprehensive Data**: Fetches heating (kWh), hot water (m³), and cold water (m³) consumption
- **Room-Level Breakdown**: Individual sensors for each device/room with unique IDs
- **Periodic Updates**: Configurable data fetch interval (default: 12 hours)
- **Extended Attributes**: Every sensor includes detailed metadata:
  - Current meter reading
  - Initial meter reading
  - Evaluation factor
  - Raw unit information
- **12-Month Timeline History**: Complete monthly consumption data for the past year
- **DIN Average Comparison**: Compare your consumption against German DIN standards
  - Shows percentage above/below average
  - Separate sensors for each consumption type
- **Customer Account Sensor**: View your account details directly in Home Assistant
  - Customer number
  - Property information
  - Address
  - Floor
- **Per-Room History**: Monthly consumption timeline for better tracking (stored as attributes)

## Prerequisites

- Home Assistant with MQTT broker (e.g., Mosquitto)
- Active Minol customer portal account
- MQTT integration enabled in Home Assistant

## Installation

### Option 1: Home Assistant Add-on (Recommended)

#### Via GitHub Repository (Coming Soon)

This add-on will be available through a Home Assistant add-on repository. Stay tuned!

#### Manual Installation

1. Clone or download this repository
2. Copy the `addons/minol_mqtt` directory to your Home Assistant `/addons` folder
   - On Home Assistant OS/Supervised: `/addons/minol_mqtt`
   - Alternative: Use the File Editor or SSH/Samba to copy files
3. Navigate to **Settings** → **Add-ons** → **Add-on Store**
4. Click the menu (⋮) → **Reload**
5. Find "Minol MQTT Bridge" in the list and click **Install**
6. Configure the add-on (see Configuration section below)
7. Start the add-on

### Option 2: Docker

```bash
cd addons/minol_mqtt
docker build -t minol-mqtt .
docker run -d \
  -e MINOL_EMAIL="your_email@example.com" \
  -e MINOL_PASSWORD="your_password" \
  -e MQTT_HOST="your_mqtt_broker" \
  -e MQTT_PORT=1883 \
  -e MQTT_USER="mqtt_user" \
  -e MQTT_PASSWORD="mqtt_password" \
  minol-mqtt
```

## Configuration

### Add-on Configuration

Configure the add-on through the Home Assistant UI:

```yaml
minol_email: "your_email@example.com"
minol_password: "your_password"
mqtt_host: "core-mosquitto"
mqtt_port: 1883
mqtt_user: "mqtt_user"
mqtt_password: "mqtt_password"
scan_interval_hours: 12
base_url: "https://webservices.minol.com"
```

### Configuration Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `minol_email` | Yes | - | Your Minol portal login email |
| `minol_password` | Yes | - | Your Minol portal password |
| `mqtt_host` | Yes | - | MQTT broker hostname (use `core-mosquitto` for built-in broker) |
| `mqtt_port` | No | 1883 | MQTT broker port |
| `mqtt_user` | Yes | - | MQTT username |
| `mqtt_password` | Yes | - | MQTT password |
| `scan_interval_hours` | No | 12 | How often to fetch new data (in hours) |
| `base_url` | No | https://webservices.minol.com | Minol portal base URL |

## Usage

Once configured and started, the add-on will:

1. Authenticate with the Minol portal using your credentials
2. Fetch consumption data for the past 12 months
3. Create Home Assistant sensors automatically via MQTT discovery
4. Update sensors according to the configured interval

### Created Sensors

The add-on creates the following sensors in Home Assistant:

#### Total Consumption Sensors
- `sensor.minol_heating_total` - Total heating consumption (kWh)
  - **Attributes**: 12-month timeline, DIN comparison %, last update timestamp
- `sensor.minol_hot_water_total` - Total hot water consumption (m³)
  - **Attributes**: 12-month timeline, DIN comparison %, last update timestamp
- `sensor.minol_cold_water_total` - Total cold water consumption (m³)
  - **Attributes**: 12-month timeline, DIN comparison %, last update timestamp

#### Per-Device/Room Sensors
- `sensor.minol_<room>_<type>_<device_id>` - One sensor per device
  - **Examples**:
    - `sensor.minol_roomX_heating_xxxxxxxxxx`
    - `sensor.minol_roomY_heating_yyyyyyyyyy`
  - **Attributes**:
    - `room_name`: Room name
    - `device_number`: Meter device number
    - `current_reading`: Current meter reading
    - `initial_reading`: Starting meter reading
    - `evaluation_factor`: Conversion factor for kWh
    - `monthly_history`: Reference to overall timeline
    - `consumption_evaluated`: Evaluated consumption value

#### DIN Comparison Sensors
- `sensor.minol_heating_din_comparison` - % above/below DIN average for heating
- `sensor.minol_hot_water_din_comparison` - % above/below DIN average for hot water
- `sensor.minol_cold_water_din_comparison` - % above/below DIN average for cold water
  - **Attributes**: `interpretation` (above average / below average)

#### Account Information Sensor
- `sensor.minol_customer_info` - Customer account details
  - **Attributes**:
    - `customer_number`
    - `email`
    - `property_number`
    - `address`
    - `floor`
    - `name`

All sensors are grouped under a single device: **Minol Customer Portal**

## How It Works

1. **Authentication**: Uses Playwright (headless Chromium) to authenticate through the Minol portal's Azure B2C SAML flow
2. **Session Management**: Extracts authentication cookies and uses them for subsequent API calls
3. **Data Fetching**: Calls the Minol API to retrieve consumption data for heating, hot water, and cold water
4. **MQTT Publishing**: Publishes data to MQTT using Home Assistant's discovery protocol
5. **Periodic Sync**: Repeats the process every configured interval

## Monitoring

### Logs

View logs in Home Assistant:
- Navigate to **Settings** → **Add-ons** → **Minol MQTT Bridge** → **Log**

Common log messages:
- `"Connected to MQTT Broker"` - Successfully connected to MQTT
- `"Authentication successful"` - Logged into Minol portal
- `"Data published to MQTT successfully."` - Sensors updated
- `"Sleeping for X hours..."` - Waiting until next sync

### MQTT Topics

Monitor MQTT traffic using MQTT Explorer or mosquitto_sub:

```bash
# Subscribe to all Minol topics
mosquitto_sub -h localhost -u mqtt_user -P mqtt_password -t "minol/#" -v

# Subscribe to discovery topics
mosquitto_sub -h localhost -u mqtt_user -P mqtt_password -t "homeassistant/sensor/minol/#" -v
```

## Troubleshooting

### Authentication Fails

**Problem**: "Authentication failed. Retrying next cycle."

**Solutions**:
- Verify your email and password are correct
- Try logging in manually at https://webservices.minol.com to ensure your account works
- Check the logs for more detailed error messages

### No Sensors Appearing in Home Assistant

**Problem**: Add-on runs but no sensors show up

**Solutions**:
- Ensure MQTT integration is enabled in Home Assistant
- Verify MQTT credentials are correct
- Check that the MQTT broker is running (if using Mosquitto add-on)
- Look for MQTT discovery messages on `homeassistant/sensor/minol/#` topic
- Restart Home Assistant after the first data sync

### Connection Timeout or Network Errors

**Problem**: "Failed to connect to MQTT" or timeout errors

**Solutions**:
- Verify `mqtt_host` is correct (use `core-mosquitto` for built-in broker)
- Ensure MQTT broker is running and accessible
- Check firewall settings if using external MQTT broker
- Verify network connectivity from the add-on container

### Docker/Container Issues

**Problem**: Add-on fails to start or crashes

**Solutions**:
- Ensure sufficient memory is available (Playwright requires ~1GB)
- Check Docker logs for detailed error messages
- Verify `/data/options.json` exists and is properly formatted
- Try rebuilding the Docker image

## Technical Details

### Architecture

- **Language**: Python 3
- **Browser Automation**: Playwright (Chromium)
- **HTTP Client**: requests + BeautifulSoup4
- **MQTT Client**: paho-mqtt
- **Base Image**: `mcr.microsoft.com/playwright/python:v1.56.0-jammy-amd64`

### Data Refresh

- Consumption data is cached for 1 hour to reduce API load
- Timeline data covers the past 12 months
- Per-room timeline data is not available from the Minol API (only aggregate timeline)

### Security

- Credentials are stored in Home Assistant's options system
- SAML tokens and session cookies are kept in memory only
- All communication with Minol portal uses HTTPS
- MQTT credentials can be configured for secure broker access

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

### Development Setup

```bash
# Clone the repository
git clone https://github.com/Gr4ph1xZ/Minol-MQTT-Bridge.git
cd Minol-MQTT-Bridge

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r old/requirements.txt

# Run locally
export MINOL_EMAIL="your_email"
export MINOL_PASSWORD="your_password"
export MQTT_HOST="localhost"
export MQTT_USER="mqtt_user"
export MQTT_PASSWORD="mqtt_password"
python addons/minol_mqtt/main.py
```

## Support

- **Issues**: [GitHub Issues](https://github.com/Gr4ph1xZ/Minol-MQTT-Bridge/issues)
- **Discussions**: [GitHub Discussions](https://github.com/Gr4ph1xZ/Minol-MQTT-Bridge/discussions)

## Disclaimer

This project is provided as-is without warranty. Use at your own risk. This is an unofficial integration and is not affiliated with or endorsed by Minol.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
