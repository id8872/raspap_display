# RaspAP E-Paper Display Controller

This Python script provides a touch-enabled status display and control interface for a Raspberry Pi running RaspAP, designed for a 2.13-inch e-paper display (typically 122x250 pixels, used here in a 250x122 landscape UI orientation).

## Features

*   **At-a-glance Status (Main Screen):**
    *   Host Wi-Fi connection status (`wlan0`): Connected SSID, or "No IP", "Disconnected".
    *   Access Point (`wlan1`) status: "Hotspot Off" or number of connected clients.
    *   CPU Temperature.
    *   GeoIP location (city, country) of the Pi's external IP.
*   **Touch Controls (Main Screen):**
    *   **NET ON/OFF Button:** Toggles the `wlan0` (host Wi-Fi) connection.
        *   Label shows current state ("NET ON" or "NET OFF").
        *   Highlighted when `wlan0` is OFF.
    *   **SYSTEM Button:** Navigates to the System Menu.
    *   **INFO Button:** Navigates to the Information Screen.
*   **Information Screen (Paged):**
    *   **Page 1:** Detailed network information:
        *   Host connection (`wlan0`): SSID, IP address.
        *   AP Hotspot (`wlan1`): Status (ON/OFF), SSID, connected clients.
    *   **Page 2:** System statistics:
        *   CPU Usage (%)
        *   CPU Temperature (°C)
        *   Memory Usage (%)
        *   System Uptime
        *   GeoIP Location
    *   Navigation: PREV, NEXT, BACK buttons.
*   **System Menu:**
    *   **REBOOT Button:** Reboots the Raspberry Pi.
    *   **SHUTDOWN Button:** Shuts down the Raspberry Pi.
    *   **BACK Button:** Returns to the main screen.
*   **API Integration:**
    *   Utilizes the RaspAP API (if `RASPAP_API_KEY` is set) for fetching AP status, SSID, and client counts, with fallbacks to system commands.
*   **Visuals:**
    *   Displays the RaspAP logo (if `assets/raspAP-logo.png` is present).
    *   Customizable fonts (DejaVuSans by default, falls back to system default).
*   **Robustness:**
    *   Touch debounce (`touch_cooldown`) and menu transition handling (`g_ignore_touch_input_temporarily`) to prevent accidental double-presses.
    *   Graceful handling of API unavailability (falls back to system commands).
    *   Final screen messages for reboot/shutdown to ensure the e-paper display shows a clean state before power-off/reboot.

## Data Update Frequencies

*   **Touch Responsiveness:** The main loop polls for touch input approximately every **0.1 seconds**.
*   **UI Refresh After Touch:** Immediate.
*   **UI Refresh (Idle, Background Changes):** The script checks for changes in displayed data (network status, system stats that drive UI text) every `periodic_update_interval` (default: **5 seconds**) if no touch interaction occurs.
*   **Core System Stats (CPU Temp, CPU Usage, Memory, Uptime):** The actual system commands to fetch these values are run at most once every **5 seconds** (cached internally by `update_system_stats()`).
*   **GeoIP Location:** The external API call to fetch GeoIP location is made at most once every **15 minutes (900 seconds)** by default (cached internally by `update_system_stats()`). This can be adjusted in the `update_system_stats()` function.
*   **Network Status (Host & AP):** Checked whenever the UI needs to refresh data (i.e., after touch, or during the periodic idle check). API calls for AP details have their own short internal caches (e.g., 5 seconds for `/ap` endpoint).

## Prerequisites

1.  **Raspberry Pi:** With Raspbian/Raspberry Pi OS and RaspAP installed and configured.
2.  **E-Paper Display:** A 2.13-inch e-paper display compatible with the `epd2in13_V4` driver (e.g., from Waveshare) with a GT1151 touch controller. The script assumes physical dimensions of 122x250 and is configured for a 90-degree clockwise physical rotation to achieve a landscape UI.
3.  **Python 3:** With `Pillow` (PIL) and `requests` libraries installed.
    ```bash
    sudo apt update
    sudo apt install python3-pil python3-requests
    ```
4.  **E-Paper Display Libraries (`TP_lib`):**
    *   The script expects a directory named `lib` in the same location as `raspap_display.py`.
    *   This `lib` directory should contain the necessary Python driver files for your specific e-paper display and GT1151 touch controller (e.g., `epdconfig.py`, `epd2in13_V4.py`, `gt1151.py`).
    *   Ensure these library files are correctly configured for your hardware (SPI, I2C, GPIO pin numbers in `epdconfig.py`).
5.  **SPI and I2C Interfaces:** Must be enabled on the Raspberry Pi (via `sudo raspi-config`).
6.  **Fonts (Optional but Recommended):**
    *   DejaVu Sans fonts: `sudo apt install fonts-dejavu-core`
    *   Alternatively, place `DejaVuSans-Bold.ttf` and `DejaVuSans.ttf` in an `assets` subdirectory.
7.  **RaspAP API Key (Optional but Recommended):**
    *   Obtain from the RaspAP web interface (Authentication page).
    *   Set this key as an environment variable (see "Running the Script" or systemd setup).

## Setup

1.  **Clone/Download:** Place `raspap_display.py` in a directory (e.g., `/home/raspap/raspap_display/`).
2.  **Libraries:** Create a `lib` subdirectory within your script's directory and place the e-paper/touch driver files into it.
    ```
    your_script_directory/
    ├── raspap_display.py
    └── lib/
        ├── epdconfig.py
        ├── epd2in13_V4.py
        └── gt1151.py
        └── ... (other necessary library files)
    ```
3.  **Assets (Optional):**
    *   Create an `assets` subdirectory within your script's directory.
    *   Place `raspAP-logo.png` (20x20 pixels recommended) in this directory.
4.  **Permissions:** Ensure the script `raspap_display.py` has execute permissions: `chmod +x raspap_display.py`.

## Configuration (Constants in the script)

*   `HOST_CONNECTED_VIA_INTERFACE`: Default: `"wlan0"`.
*   `AP_BROADCAST_INTERFACE`: Default: `"wlan1"`.
*   `RASPAP_API_BASE_URL`: Default: `"http://localhost:8081"`.
*   `periodic_update_interval`: Default: `5` (seconds). Controls how often background state changes are checked when idle.
*   GeoIP update interval: Adjustable within `update_system_stats()` (default 900s).

## Running the Script

**Manually (for testing):**
It's recommended to run as the user who owns the script files (e.g., `raspap`).
```bash
cd /path/to/your_script_directory
RASPAP_API_KEY="YOUR_ACTUAL_API_KEY" python3 ./raspap_display.py