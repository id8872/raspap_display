# RaspAP E-Paper Display Controller

This Python script provides a touch-enabled status display and control interface for a Raspberry Pi running RaspAP, designed for a 2.13-inch e-paper display (typically 122x250 pixels, used here in a 250x122 landscape UI orientation).

## Features

*   **At-a-glance Status (Main Screen):**
    *   Host Wi-Fi connection status (`wlan0`): Connected SSID, or "No IP", "Disconnected".
    *   Access Point (`wlan1`) status: "Hotspot Off" or number of connected clients.
    *   Current VPN connection status: "VPN: Off", "VPN: Connecting", "VPN: Ending", "VPN: Error", or "VPN: \[Display Name]".
    *   CPU Temperature.
    *   GeoIP location (city, country) of the Pi's external IP.
*   **Touch Controls (Main Screen):**
    *   **NET ON/OFF Button:** Toggles the `wlan0` (host Wi-Fi) connection.
    *   **VPN Button:** Navigates to the VPN Selection List.
    *   **SYSTEM Button:** Navigates to the System Menu.
    *   **INFO Button:** Navigates to the Information Screen.
*   **VPN Selection List (Scrollable):**
    *   Lists available VPN connections from `vpn_connections.json`.
    *   Displays up to 4 VPNs at a time (configurable).
    *   Allows selection of a VPN to connect by tapping its name.
    *   Highlights the currently active VPN in the list.
    *   **UP/DOWN Buttons:** Scroll through the list of VPNs.
    *   **DISCONNECT Button:** Appears if a VPN is active, allows disconnecting.
    *   **BACK Button:** Returns to the main screen.
*   **Information Screen (Paged):**
    *   **Page 1:** Detailed network information:
        *   Host connection (`wlan0`): SSID, IP address.
        *   AP Hotspot (`wlan1`): Status (ON/OFF), SSID, connected clients.
    *   **Page 2:** System statistics:
        *   CPU Usage (%)
        *   CPU Temperature (°C)
        *   GeoIP Location
    *   Navigation: PREV, NEXT, BACK buttons.
*   **System Menu:**
    *   **REBOOT Button:** Reboots the Raspberry Pi.
    *   **SHUTDOWN Button:** Shuts down the Raspberry Pi.
    *   **BACK Button:** Returns to the main screen.
*   **API Integration & Caching:**
    *   Utilizes the RaspAP API for AP status/details if `RASPAP_API_KEY` is set.
    *   AP SSID (10s cache), AP client count (5s cache).
    *   GeoIP location (15min cache, updated on VPN connect/disconnect & startup).
    *   Core system stats (CPU temp/usage) updated every 5 seconds.
*   **Visuals:**
    *   Displays the RaspAP logo (if `assets/raspAP-logo.png` is present).
    *   Customizable fonts (DejaVuSans by default).
*   **Robustness:**
    *   Touch debounce and menu transition handling.
    *   Graceful fallbacks for API unavailability.
    *   Final screen messages for reboot/shutdown.

## Data Update Frequencies

*   **Touch Responsiveness:** Main loop polls for touch ~every **0.1 seconds**.
*   **UI Refresh After Touch:** Immediate.
*   **Background UI Refresh (Idle):** The script checks for state changes (network, stats) roughly every **1 second** (due to periodic updates in the main loop) and redraws if necessary.
*   **Core System Stats (CPU Temp, CPU Usage):** Fetched and cached every **5 seconds**.
*   **GeoIP Location:** Fetched and cached every **15 minutes (900 seconds)**, and also on script startup, VPN connect, and VPN disconnect.
*   **AP SSID:** Cached for **10 seconds**.
*   **AP Client Count:** Cached for **5 seconds**.
*   **Network Status (Host, AP, VPN):** Checked before any potential redraw.

## Prerequisites

1.  **Raspberry Pi:** Model 3B+ or newer recommended. Raspbian/Raspberry Pi OS with RaspAP installed.
2.  **E-Paper Display:** A 2.13-inch e-paper display compatible with the `epd2in13_V4` driver (e.g., from Waveshare) and a GT1151 touch controller. The script assumes physical display dimensions of 122x250 pixels.
3.  **Python 3:** Version 3.7+
4.  **Python Libraries:**
    ```bash
    sudo apt update
    sudo apt install python3-pil python3-requests python3-gpiozero python3-spidev python3-smbus
    ```
5.  **E-Paper Display Libraries (`TP_lib`):**
    *   A directory named `lib` is required in the same location as `raspap_display.py`.
    *   This `lib` directory must contain the Python driver files for your specific e-paper display model and touch controller. For the common Waveshare 2.13 V4 display with touch, these are typically `epdconfig.py`, `epd2in13_V4.py`, and `gt1151.py`.
    *   **Crucially, `lib/epdconfig.py` must be configured with the correct GPIO pin numbers for your Raspberry Pi HAT or wiring setup (RST, DC, BUSY, CS for EPD; TRST, INT for Touch).**
6.  **SPI and I2C Interfaces:** Must be enabled on the Raspberry Pi using `sudo raspi-config` (Interfacing Options).
7.  **Fonts (Recommended):**
    *   Install DejaVu Sans fonts: `sudo apt install fonts-dejavu-core`
    *   Alternatively, place `DejaVuSans-Bold.ttf` and `DejaVuSans.ttf` in an `assets` subdirectory next to `raspap_display.py`.
8.  **RaspAP API Key (Highly Recommended):**
    *   Obtain this from your RaspAP web interface (usually under Configure > Authentication).
    *   The script uses this for fetching AP status, SSID, and client counts more reliably. Fallbacks to system commands are used if the key is not provided, but these might be less efficient or provide less detail.
9.  **OpenVPN (Optional, for VPN feature):**
    *   OpenVPN client installed (`sudo apt install openvpn`).
    *   Your `.ovpn` configuration files, typically stored in `/etc/openvpn/ovpn_tcp/` or `/etc/openvpn/ovpn_udp/`.
    *   If your VPN requires credentials, ensure each `.ovpn` file contains an `auth-user-pass /path/to/your/creds.txt` line. The script assumes `/etc/openvpn/nord_creds.txt` by default if not specified in OVPN files, but it's best practice to specify in OVPN.
    *   Symbolic links created in `/etc/openvpn/client/` pointing to your actual `.ovpn` files, with a `.conf` extension (e.g., `sudo ln -s /etc/openvpn/ovpn_tcp/your-server.tcp.ovpn /etc/openvpn/client/your-server.tcp.conf`). This allows `systemd openvpn-client@.service` to manage them.

## Setup Instructions

1.  **Download/Clone Script:**
    Place `raspap_display.py` in a directory on your Raspberry Pi, for example:
    ```bash
    mkdir ~/raspap_display
    cd ~/raspap_display
    # Download raspap_display.py into this directory
    ```

2.  **Create Library Directory and Add Drivers:**
    Inside `~/raspap_display`, create the `lib` subdirectory and place your e-paper driver files there:
    ```bash
    mkdir lib
    cd lib
    # Download/copy epdconfig.py, epd2in13_V4.py, gt1151.py (and an empty __init__.py) here
    # Ensure epdconfig.py has the correct GPIO pin numbers for your hardware!
    touch __init__.py 
    cd .. 
    ```
    Your directory structure should look like:
    ```
    ~/raspap_display/
    ├── raspap_display.py
    └── lib/
        ├── __init__.py
        ├── epdconfig.py
        ├── epd2in13_V4.py
        └── gt1151.py
    ```

3.  **Create Assets Directory (Optional):**
    For the RaspAP logo and custom fonts (if not using system-installed):
    ```bash
    mkdir assets
    # Place raspAP-logo.png (e.g., 20x20 pixels) in assets/
    # Place DejaVuSans-Bold.ttf and DejaVuSans.ttf in assets/ (if preferred over system fonts)
    ```

4.  **Create VPN Configuration File (Optional):**
    If using the VPN menu feature, create `vpn_connections.json` in `~/raspap_display/`:
    ```json
    [
      {
        "name": "NordVPN US",
        "server": "us1234.nordvpn.com",
        "protocol": "UDP"
      },
      {
        "name": "Work VPN",
        "server": "vpn.mycompany.com",
        "protocol": "TCP"
      }
    ]
    ```
    *   Replace with your actual VPN server details.
    *   Ensure corresponding OpenVPN client configurations and systemd symlinks (as described in Prerequisites) are set up.

5.  **Set Script Permissions:**
    Make the main script executable:
    ```bash
    chmod +x raspap_display.py
    ```

6.  **Enable SPI and I2C:**
    Use `sudo raspi-config`, go to "Interfacing Options", and enable both SPI and I2C. Reboot if prompted.

## Configuration (Constants in the script)

Modify these at the top of `raspap_display.py` if needed:

*   `BTN_WIDTH`: Default: `80`. Width of the side buttons. Adjusting this will affect the main content area width.
*   `HOST_CONNECTED_VIA_INTERFACE`: Default: `"wlan0"`.
*   `AP_BROADCAST_INTERFACE`: Default: `"wlan1"`.
*   `RASPAP_API_BASE_URL`: Default: `"http://localhost:8081"`.
*   `CACHE_*_DURATION`: Control caching times for API data.
*   GeoIP update interval: Hardcoded within `update_system_stats()` (default 900s for GeoIP, 5s for core stats).
*   `VPN_LIST_ITEMS_PER_SCREEN`: Default: `4`.

## Running the Script

**Manually (for testing):**
The script requires root privileges for some operations (managing network interfaces, system services like OpenVPN, reboot/shutdown).
```bash
cd ~/raspap_display
sudo RASPAP_API_KEY="YOUR_ACTUAL_API_KEY" python3 ./raspap_display.py
```
(Replace `YOUR_ACTUAL_API_KEY` with your key from RaspAP web UI > Configure > Authentication. If omitted, API features will use fallbacks.)

**As a Systemd Service (Recommended for auto-start):**

1.  Create a service file, e.g., `/etc/systemd/system/raspap-epaper.service`:
    ```ini
    [Unit]
    Description=RaspAP E-Paper Display Service
    After=multi-user.target network-online.target openvpn.service
    Wants=network-online.target openvpn.service

    [Service]
    Type=simple
    ExecStart=/usr/bin/python3 /home/raspap/raspap_display/raspap_display.py
    WorkingDirectory=/home/raspap/raspap_display
    StandardOutput=journal
    StandardError=journal
    Restart=always
    User=root # Script uses sudo internally, so running as root is simplest
    Environment="PYTHONUNBUFFERED=1"
    Environment="RASPAP_API_KEY=YOUR_ACTUAL_API_KEY" 
    # Add other environment variables if needed, e.g., RASPAP_API_BASE_URL

    [Install]
    WantedBy=multi-user.target
    ```
    *   Adjust `ExecStart`, `WorkingDirectory`, and `RASPAP_API_KEY` to match your setup.
    *   `PYTHONUNBUFFERED=1` ensures Python output appears in the journal immediately.

2.  Reload systemd, enable, and start the service:
    ```bash
    sudo systemctl daemon-reload
    sudo systemctl enable raspap-epaper.service
    sudo systemctl start raspap-epaper.service
    ```

3.  Check status and logs:
    ```bash
    sudo systemctl status raspap-epaper.service
    journalctl -u raspap-epaper.service -f 
    ```

## Troubleshooting

*   **"CRITICAL ERROR during driver imports" / "EPD init() reported failure"**:
    *   Verify `lib` directory structure and contents.
    *   Ensure `epdconfig.py` has correct GPIO pin numbers for RST, DC, BUSY, CS, INT, TRST.
    *   Confirm SPI/I2C are enabled in `raspi-config`.
    *   Check physical display connections.
    *   Ensure `python3-spidev` and `python3-smbus` are installed.
*   **Touch Issues (e.g., "Touch GT_Init() called" then `[49, 49, 53, 56]` but no response):**
    *   The `[49, 49, 53, 56]` output is normal (GT1151 chip ID "1158").
    *   Re-check I2C wiring, and `INT`/`TRST` pin definitions in `lib/epdconfig.py`.
*   **VPN Warnings ("Cmd 'sudo systemctl is-active ...' empty"):**
    *   `systemd` cannot find or query the state of the OpenVPN service.
    *   Ensure symbolic links in `/etc/openvpn/client/` are correct (e.g., `your-server.tcp.conf -> /path/to/your-server.tcp.ovpn`).
    *   Run `sudo systemctl daemon-reload` after creating/changing symlinks.
    *   Verify the `.ovpn` files themselves are valid and include `auth-user-pass` directives if needed.
    *   Test `sudo systemctl status openvpn-client@your-config-name.tcp` manually.
*   **"vpn_connections.json not found" or "not a JSON list"**: Ensure the file exists in the script's directory and contains valid JSON.
*   **API errors**: Check `RASPAP_API_KEY` and `RASPAP_API_BASE_URL`. Ensure RaspAP web UI is accessible.
*   **Font errors**: Install `fonts-dejavu-core` or place `.ttf` files in `assets/`.
*   For detailed debugging, change `logging.basicConfig(level=logging.INFO, ...)` to `logging.DEBUG` at the top of `raspap_display.py`.
