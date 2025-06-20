#!/usr/bin/env python3
import sys
import os
import time
import subprocess
import re
from PIL import Image, ImageDraw, ImageFont, ImageOps
import traceback
import logging
import requests
import json  # Added for VPN connections

# --- Configure Logging ---
# For troubleshooting, temporarily change logging.INFO to logging.DEBUG
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# --- Load API Key and Base URL ---
RASPAP_API_KEY = os.environ.get('RASPAP_API_KEY')
RASPAP_BASE_URL = os.environ.get(
    'RASPAP_API_BASE_URL', "http://localhost:8081")
if RASPAP_API_KEY is None:
    logging.warning(
        "RASPAP_API_KEY env var not set. API features will use fallbacks.")
else:
    logging.info(f"RASPAP_API_KEY loaded. API base URL: {RASPAP_BASE_URL}")

script_dir = os.path.dirname(os.path.abspath(__file__))
lib_parent_path = os.path.join(script_dir, "lib")
if lib_parent_path not in sys.path:
    sys.path.insert(0, lib_parent_path)

EPD_DRIVER_CLASS = None
TOUCH_DRIVER_CLASS = None
GT_Development_Class = None
tp_config_module = None
TOUCH_INT_PIN = None
try:
    logging.info("Importing drivers from TP_lib...")
    from TP_lib import epdconfig as tp_epdconfig_module
    from TP_lib import epd2in13_V4
    from TP_lib import gt1151
    tp_config_module = tp_epdconfig_module
    EPD_DRIVER_CLASS = epd2in13_V4.EPD
    logging.info("Imported EPD: TP_lib.epd2in13_V4.EPD")
    TOUCH_DRIVER_CLASS = gt1151.GT1151
    GT_Development_Class = gt1151.GT_Development
    TOUCH_INT_PIN = getattr(tp_config_module, 'INT', getattr(
        tp_config_module, 'TP_INT_PIN', None))
    if TOUCH_INT_PIN:
        logging.debug(f"Touch INT pin from epdconfig: {TOUCH_INT_PIN}")
    else:
        logging.warning("Touch INT pin not found in TP_lib.epdconfig.")
    logging.info(f"Imported Touch: TP_lib.gt1151.GT1151")
except Exception as e:
    logging.critical(
        f"CRITICAL ERROR during driver imports: {e}\n{traceback.format_exc()}")
    sys.exit(1)

PHYSICAL_EPD_WIDTH = EPD_DRIVER_CLASS.width if EPD_DRIVER_CLASS and hasattr(
    EPD_DRIVER_CLASS, 'width') else 122
PHYSICAL_EPD_HEIGHT = EPD_DRIVER_CLASS.height if EPD_DRIVER_CLASS and hasattr(
    EPD_DRIVER_CLASS, 'height') else 250
UI_EPD_WIDTH = 250
UI_EPD_HEIGHT = 122

HOST_CONNECTED_VIA_INTERFACE = "wlan0"
AP_BROADCAST_INTERFACE = "wlan1"

# --- Button Layout Configuration ---
BTN_WIDTH = 80
BTN_MARGIN = 5
BTN_X = UI_EPD_WIDTH-BTN_WIDTH-BTN_MARGIN
NUM_MAIN_BUTTONS = 4
gap_h = 4
BTN_HEIGHT = (UI_EPD_HEIGHT - (2 * BTN_MARGIN) -
              ((NUM_MAIN_BUTTONS - 1) * gap_h)) // NUM_MAIN_BUTTONS
if BTN_HEIGHT <= 0:
    logging.warning(
        f"Calculated BTN_HEIGHT {BTN_HEIGHT} is too small. Defaulting to 20.")
    BTN_HEIGHT = 20
    gap_h = (UI_EPD_HEIGHT - (2 * BTN_MARGIN) - (NUM_MAIN_BUTTONS * BTN_HEIGHT)
             ) // (NUM_MAIN_BUTTONS - 1) if NUM_MAIN_BUTTONS > 1 else 0
    if gap_h < 0:
        gap_h = 2

BTN_SLOT_1_Y = BTN_MARGIN
BTN_SLOT_2_Y = BTN_SLOT_1_Y + BTN_HEIGHT + gap_h
BTN_SLOT_3_Y = BTN_SLOT_2_Y + BTN_HEIGHT + gap_h
BTN_SLOT_4_Y = BTN_SLOT_3_Y + BTN_HEIGHT + gap_h

logging.debug(
    f"Button layout: Height={BTN_HEIGHT}, Gap={gap_h}. Slots Y: {BTN_SLOT_1_Y}, {BTN_SLOT_2_Y}, {BTN_SLOT_3_Y}, {BTN_SLOT_4_Y}")

BUTTON_AREAS = {
    'net_toggle': (BTN_X, BTN_SLOT_1_Y, BTN_WIDTH, BTN_HEIGHT),
    # Will be "VPN"
    'vpn_menu':   (BTN_X, BTN_SLOT_2_Y, BTN_WIDTH, BTN_HEIGHT),
    'system':     (BTN_X, BTN_SLOT_3_Y, BTN_WIDTH, BTN_HEIGHT),
    'info':       (BTN_X, BTN_SLOT_4_Y, BTN_WIDTH, BTN_HEIGHT)
}
SYSTEM_BUTTON_AREAS = {
    'reboot':   (BTN_X, BTN_SLOT_1_Y, BTN_WIDTH, BTN_HEIGHT),
    'shutdown': (BTN_X, BTN_SLOT_2_Y, BTN_WIDTH, BTN_HEIGHT),
    'back':     (BTN_X, BTN_SLOT_4_Y, BTN_WIDTH, BTN_HEIGHT)
}
INFO_BUTTON_AREAS = {
    'prev': (BTN_X, BTN_SLOT_1_Y, BTN_WIDTH, BTN_HEIGHT),
    'next': (BTN_X, BTN_SLOT_2_Y, BTN_WIDTH, BTN_HEIGHT),
    'back': (BTN_X, BTN_SLOT_4_Y, BTN_WIDTH, BTN_HEIGHT)
}

# --- VPN List Menu Configuration ---
VPN_LIST_ITEM_HEIGHT = 20
VPN_LIST_ITEMS_PER_SCREEN = 4
VPN_LIST_X_START = BTN_MARGIN
VPN_LIST_Y_START = BTN_MARGIN + 20
VPN_LIST_WIDTH = UI_EPD_WIDTH - (2 * BTN_MARGIN) - BTN_WIDTH - BTN_MARGIN
vpn_list_scroll_offset = 0

VPN_LIST_NAV_BUTTON_AREAS = {
    'up':       (BTN_X, BTN_SLOT_1_Y, BTN_WIDTH, BTN_HEIGHT),
    'down':     (BTN_X, BTN_SLOT_2_Y, BTN_WIDTH, BTN_HEIGHT),
    'disconnect': (BTN_X, BTN_SLOT_3_Y, BTN_WIDTH, BTN_HEIGHT),
    'back':     (BTN_X, BTN_SLOT_4_Y, BTN_WIDTH, BTN_HEIGHT)
}

# --- Status & UI State ---
current_wlan0_connection_status = "INIT"
current_ap_hotspot_status = "INIT"
current_vpn_status = "INIT"

show_system_menu = False
show_info_screen = False
show_vpn_menu = False
info_page = 0
MAX_INFO_PAGES = 2

touch_cooldown = 0.5
last_button_press = 0
last_displayed_state = {}

g_ignore_touch_input_temporarily = False
g_ignore_touch_until_timestamp = 0
ACTION_MENU_CHANGE = 2
ACTION_NORMAL_PRESS = 1
ACTION_NONE = 0

# --- VPN Connection Management ---
VPN_CONNECTIONS_FILE = "vpn_connections.json"
available_vpns = []
current_vpn_config_basename = None
current_vpn_display_name = None

try:
    assets_dir = os.path.join(script_dir, "assets")
    font_b_path = os.path.join(assets_dir, "DejaVuSans-Bold.ttf") if os.path.exists(os.path.join(
        assets_dir, "DejaVuSans-Bold.ttf")) else '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    font_r_path = os.path.join(assets_dir, "DejaVuSans.ttf") if os.path.exists(os.path.join(
        assets_dir, "DejaVuSans.ttf")) else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    font_l = ImageFont.truetype(font_b_path, 16)
    font_m = ImageFont.truetype(font_r_path, 13)
    font_s = ImageFont.truetype(font_r_path, 11)
    font_t = ImageFont.truetype(font_r_path, 9)
except IOError:
    logging.warning("DejaVu fonts fail, using default.")
    font_l, font_m, font_s, font_t = (ImageFont.load_default(),)*4

RASPAP_LOGO = None
try:
    logo_p = os.path.join(assets_dir, "raspAP-logo.png")
    if os.path.exists(logo_p):
        RASPAP_LOGO = ImageOps.invert(Image.open(
            logo_p).convert("1")).resize((20, 20))
        logging.info("RaspAP logo loaded.")
except Exception as e:
    logging.warning(f"Logo load err: {e}")

epd_instance = None
touch_instance = None
ui_image_buffer = None
current_gt_dev_data = None
old_gt_dev_data = None
system_stats_cache = {'cpu_temp': 0, 'cpu_usage': 0, 'mem_usage': 0, 'uptime': '', 'last_update': 0,
                      # Added last_core_stats_update
                      'geo_location': 'Unknown', 'last_geo_update': 0, 'last_core_stats_update': 0}

CACHE_AP_SSID_DURATION = 10
CACHE_AP_CLIENTS_DURATION = 5
_ap_ssid_cache = {'ssid': None, 'timestamp': 0}
_ap_clients_cache = {'clients': None, 'timestamp': 0}


def get_text_dimensions(draw, text, font):
    if hasattr(font, 'getbbox'):
        bbox = font.getbbox(text)
        return bbox[2]-bbox[0], bbox[3]-bbox[1]
    if hasattr(draw, 'textbbox'):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2]-bbox[0], bbox[3]-bbox[1]
    return draw.textsize(text, font=font)


def call_raspap_api(endpoint, method="GET", json_data=None, params=None):
    if not RASPAP_API_KEY:
        return None
    url = f"{RASPAP_BASE_URL}/{endpoint.lstrip('/')}"
    headers = {"Accept": "application/json", "access_token": RASPAP_API_KEY}
    try:
        logging.debug(
            f"API Call: {method} {url} Params: {params} Data: {json_data}")
        if method.upper() == "GET":
            resp = requests.get(url, headers=headers, params=params, timeout=5)
        elif method.upper() == "POST":
            resp = requests.post(url, headers=headers,
                                 json=json_data, params=params, timeout=5)
        else:
            logging.error(f"Unsupported API method: {method}")
            return None
        if resp.status_code == 204 or not resp.content:
            return {"success": True, "status_code": resp.status_code}
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logging.warning(f"API Call Error to {url}: {e}")
        return None


def get_cached_ap_ssid():
    global _ap_ssid_cache
    now = time.time()
    if _ap_ssid_cache['ssid'] is not None and (now - _ap_ssid_cache['timestamp'] < CACHE_AP_SSID_DURATION):
        logging.debug(f"Using cached AP SSID: {_ap_ssid_cache['ssid']}")
        return _ap_ssid_cache['ssid']
    ap_details = call_raspap_api("ap")
    ssid = ap_details.get('ssid', "N/A") if ap_details else "N/A"
    _ap_ssid_cache['ssid'] = ssid
    _ap_ssid_cache['timestamp'] = now
    logging.debug(f"Fetched and cached AP SSID: {ssid}")
    return ssid


def get_cached_ap_clients():
    global _ap_clients_cache
    now = time.time()
    if _ap_clients_cache['clients'] is not None and (now - _ap_clients_cache['timestamp'] < CACHE_AP_CLIENTS_DURATION):
        logging.debug(
            f"Using cached AP Clients: {_ap_clients_cache['clients']}")
        return _ap_clients_cache['clients']
    clients_details = call_raspap_api(f"clients/{AP_BROADCAST_INTERFACE}")
    clients = len(clients_details['active_clients']
                  ) if clients_details and 'active_clients' in clients_details else 0
    _ap_clients_cache['clients'] = clients
    _ap_clients_cache['timestamp'] = now
    logging.debug(f"Fetched and cached AP Clients: {clients}")
    return clients


def run_command(command):
    proc = None
    try:
        logging.debug(f"Executing command: {command}")
        proc = subprocess.Popen(command, shell=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate(timeout=15)
        stdout_decoded = stdout.decode('utf-8', errors='replace').strip()
        stderr_decoded = stderr.decode('utf-8', errors='replace').strip()
        if proc.returncode != 0:
            logging.debug(
                f"Cmd '{command}' failed ({proc.returncode}). stdout: '{stdout_decoded}', stderr: '{stderr_decoded}'")
            return ""
        if not stdout_decoded and proc.returncode == 0:
            logging.debug(
                f"Cmd '{command}' ok (0) but no stdout. stderr: '{stderr_decoded}'")
        return stdout_decoded
    except subprocess.TimeoutExpired:
        logging.error(f"Cmd '{command}' timed out.")
        if proc:
            try:
                proc.kill()
                proc.wait(timeout=1)
            except Exception as e_kill:
                logging.error(
                    f"Error killing timed-out process for '{command}': {e_kill}")
        return ""
    except Exception as e:
        logging.error(f"Cmd exec error for '{command}': {e}")
        logging.debug(traceback.format_exc())
        return ""


def get_vpn_config_basename_no_ext(
    vpn_def): return f"{vpn_def['server']}.{vpn_def['protocol'].lower()}"


def get_vpn_service_name(
    config_basename_no_ext): return f"openvpn-client@{config_basename_no_ext}"


def load_vpn_connections():
    global available_vpns
    filepath = os.path.join(script_dir, VPN_CONNECTIONS_FILE)
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                loaded_data = json.load(f)
            if not isinstance(loaded_data, list):
                logging.error(f"{VPN_CONNECTIONS_FILE} not a JSON list.")
                available_vpns = []
                return
            available_vpns = loaded_data
            logging.info(f"Loaded {len(available_vpns)} VPNs from {filepath}")
            for vpn in available_vpns:
                vpn['config_basename_no_ext'] = get_vpn_config_basename_no_ext(
                    vpn)
        except Exception as e:
            logging.error(f"Error loading/parsing {filepath}: {e}")
            available_vpns = []
    else:
        logging.warning(f"{filepath} not found. VPNs unavailable.")
        available_vpns = []


def get_specific_vpn_service_status(service_name_to_check):
    if not service_name_to_check:
        logging.error("get_specific_vpn_service_status: No service name.")
        return "VPN_ERROR"
    cmd = f"sudo systemctl is-active {service_name_to_check}"
    status_output = run_command(cmd)
    if status_output == "active":
        return "VPN_ON"
    if status_output == "inactive" or status_output == "failed":
        return "VPN_OFF"
    if status_output == "":
        logging.warning(f"Cmd '{cmd}' empty. Treating as VPN_ERROR.")
        return "VPN_ERROR"
    logging.warning(
        f"Service '{service_name_to_check}' status: '{status_output}'. Treating as VPN_ERROR.")
    return "VPN_ERROR"


def update_vpn_status(initial_check=False):
    global current_vpn_status, current_vpn_config_basename, current_vpn_display_name
    old_status, old_display_name = current_vpn_status, current_vpn_display_name
    if initial_check:
        current_vpn_status, current_vpn_config_basename, current_vpn_display_name = "VPN_OFF", None, None
        for vpn_def in available_vpns:
            svc_name = get_vpn_service_name(vpn_def['config_basename_no_ext'])
            status = get_specific_vpn_service_status(svc_name)
            if status == "VPN_ON":
                current_vpn_config_basename, current_vpn_display_name, current_vpn_status = vpn_def[
                    'config_basename_no_ext'], vpn_def['name'], "VPN_ON"
                logging.info(
                    f"Active VPN on startup: {current_vpn_display_name}")
                break
    elif current_vpn_config_basename:
        svc_name = get_vpn_service_name(current_vpn_config_basename)
        actual_status = get_specific_vpn_service_status(svc_name)
        if actual_status == "VPN_ON":
            current_vpn_status = "VPN_ON"
        elif actual_status == "VPN_OFF":
            current_vpn_status = "VPN_OFF"
            if old_status == "VPN_ON" and current_vpn_display_name:
                logging.info(
                    f"VPN '{current_vpn_display_name}' now OFF. Clearing active VPN.")
                current_vpn_config_basename, current_vpn_display_name = None, None
        else:
            current_vpn_status = "VPN_ERROR"
    else:
        current_vpn_status = "VPN_OFF"
        if not current_vpn_config_basename:
            current_vpn_display_name = None
    if old_status != current_vpn_status or old_display_name != current_vpn_display_name:
        logging.info(
            f"VPN status: {current_vpn_status} (Display: {current_vpn_display_name or 'N/A'})")


def connect_vpn(vpn_def_to_connect):
    global current_vpn_status, current_vpn_config_basename, current_vpn_display_name, show_vpn_menu
    if current_vpn_config_basename == vpn_def_to_connect['config_basename_no_ext'] and current_vpn_status == "VPN_ON":
        logging.info(f"VPN '{vpn_def_to_connect['name']}' already active.")
        show_vpn_menu = False
        return
    current_vpn_status = "VPN_CONNECTING"
    display_message(
        f"VPN: Connecting\n{vpn_def_to_connect['name'][:18]}", font_to_use=font_m)
    if current_vpn_config_basename:
        logging.info(
            f"Switching. Disconnecting: {current_vpn_display_name or current_vpn_config_basename}")
        disconnect_vpn(silent=True, called_during_connect=True)
    new_cfg_base, new_svc_name, new_disp_name = vpn_def_to_connect['config_basename_no_ext'], get_vpn_service_name(
        vpn_def_to_connect['config_basename_no_ext']), vpn_def_to_connect['name']
    logging.info(f"System: Starting VPN service '{new_svc_name}'...")
    run_command(f"sudo systemctl start {new_svc_name}")
    time.sleep(7)
    actual_status = get_specific_vpn_service_status(new_svc_name)
    if actual_status == "VPN_ON":
        current_vpn_config_basename, current_vpn_display_name, current_vpn_status = new_cfg_base, new_disp_name, "VPN_ON"
        logging.info(f"VPN '{new_disp_name}' connected.")
        update_system_stats(force_geoip_update=True)
        show_vpn_menu = False
    else:
        logging.error(
            f"Failed to start/activate '{new_svc_name}'. Status: '{actual_status}'. Check journalctl -u {new_svc_name}")
        display_message(
            f"VPN: Failed\n{new_disp_name[:18]}", font_to_use=font_m)
        current_vpn_config_basename, current_vpn_display_name = None, None
        current_vpn_status = "VPN_ERROR" if actual_status == "VPN_ERROR" else "VPN_OFF"
        time.sleep(2)
    update_vpn_status()


def disconnect_vpn(silent=False, called_during_connect=False):
    global current_vpn_status, current_vpn_config_basename, current_vpn_display_name
    if not current_vpn_config_basename:
        logging.info("Disconnect VPN: no active VPN.")
        current_vpn_status = "VPN_OFF"
        return
    svc_name, disp_name_disc = get_vpn_service_name(
        current_vpn_config_basename), current_vpn_display_name or current_vpn_config_basename
    current_vpn_status = "VPN_DISCONNECTING"
    if not silent:
        display_message(
            f"VPN: Disconnecting\n{disp_name_disc[:18]}", font_to_use=font_m)
    logging.info(f"System: Stopping VPN service '{svc_name}'...")
    run_command(f"sudo systemctl stop {svc_name}")
    time.sleep(3)
    actual_status = get_specific_vpn_service_status(svc_name)
    if actual_status == "VPN_OFF":
        logging.info(f"VPN '{disp_name_disc}' disconnected.")
    else:
        logging.warning(
            f"VPN '{disp_name_disc}' may not have stopped. Status: '{actual_status}'")
    current_vpn_config_basename, current_vpn_display_name, current_vpn_status = None, None, "VPN_OFF"
    if not called_during_connect:
        update_system_stats(force_geoip_update=True)
        update_vpn_status()


def get_ap_hotspot_status_via_api():
    data = call_raspap_api("system")
    if data and isinstance(data, dict):
        return data.get('hostapdStatus') == 1
    return run_command("sudo systemctl is-active hostapd") == "active"


def update_ap_hotspot_status():
    global current_ap_hotspot_status
    old_status = current_ap_hotspot_status
    current_ap_hotspot_status = "AP_ON" if get_ap_hotspot_status_via_api() else "AP_OFF"
    if old_status != current_ap_hotspot_status:
        logging.info(f"AP Hotspot: {current_ap_hotspot_status}")


def get_host_connected_ssid(interface=HOST_CONNECTED_VIA_INTERFACE):
    ssid = run_command(f"iwgetid -r {interface}")
    return ssid if ssid and ssid.strip() else None


def get_interface_ip(interface_name):
    ip_output = run_command(f"ip -4 addr show {interface_name}")
    if not ip_output:
        return None
    for ip_addr in re.findall(r"inet\s+([\d.]+)/\d+", ip_output):
        if not ip_addr.startswith("127.") and not ip_addr.startswith("169.254."):
            return ip_addr
    return None


def update_wlan0_connection_status():
    global current_wlan0_connection_status
    old_status = current_wlan0_connection_status
    ip_address = get_interface_ip(HOST_CONNECTED_VIA_INTERFACE)
    if ip_address:
        current_wlan0_connection_status = "NET_CONNECTED"
    else:
        current_wlan0_connection_status = "NET_DISCONNECTED" if not get_host_connected_ssid(
            HOST_CONNECTED_VIA_INTERFACE) else "NET_ASSOCIATED_NO_IP"
    if old_status != current_wlan0_connection_status:
        logging.info(
            f"Host ({HOST_CONNECTED_VIA_INTERFACE}): {current_wlan0_connection_status}")


def get_external_ip_location():
    try:
        resp = requests.get(
            "https://ipapi.co/json/?fields=city,country_name", timeout=3)
        resp.raise_for_status()
        data = resp.json()
        co, ci = data.get('country_name', '?'), data.get('city', '?')
        return f"{ci}, {co}" if ci != '?' and co != '?' else (co if co != '?' else (ci if ci != '?' else "Unknown"))
    except:
        return "Unknown"


def update_system_stats(force_geoip_update=False):
    global system_stats_cache
    now = time.time()

    if now - system_stats_cache.get('last_core_stats_update', 0) > 5:
        try:
            t = run_command("cat /sys/class/thermal/thermal_zone0/temp")
            system_stats_cache['cpu_temp'] = int(t)/1000 if t else 0
        except:
            system_stats_cache['cpu_temp'] = 0
        try:
            c = run_command("top -bn1 | grep 'Cpu(s)' | awk '{print $2+$4}'")
            system_stats_cache['cpu_usage'] = float(c) if c else 0
        except:
            system_stats_cache['cpu_usage'] = 0
        system_stats_cache['last_core_stats_update'] = now

    if force_geoip_update or (now - system_stats_cache.get('last_geo_update', 0) > 900):
        logging.info(
            f"Updating GeoIP location... Forced: {force_geoip_update}")
        system_stats_cache['geo_location'] = get_external_ip_location()
        system_stats_cache['last_geo_update'] = now

    system_stats_cache['last_update'] = now


def display_on_epd(dev, img):
    if not dev:
        logging.warning("EPD instance is None.")
        return
    try:
        dev.display(dev.getbuffer(img.convert('1')))
    except Exception as e:
        logging.error(f"EPD display error: {e}")


def display_message(msg_txt, font_to_use=font_l, fill=0):
    global ui_image_buffer, epd_instance, RASPAP_LOGO
    if not epd_instance or not ui_image_buffer:
        return
    draw = ImageDraw.Draw(ui_image_buffer)
    draw.rectangle((0, 0, UI_EPD_WIDTH, UI_EPD_HEIGHT), fill=255)
    hdr_h = 25
    if RASPAP_LOGO:
        lx, ly = 5, 5
        tw, th = get_text_dimensions(draw, "RaspAP", font_l)
        draw.bitmap((lx, ly), RASPAP_LOGO, fill=0)
        draw.text((lx+RASPAP_LOGO.width+5, ly+(RASPAP_LOGO.height-th)//2),
                  "RaspAP", font=font_l, fill=0)
    else:
        draw.text((5, 5), "RaspAP", font=font_l, fill=0)
    lines = msg_txt.split('\n')
    line_dims = [get_text_dimensions(draw, l, font_to_use) for l in lines]
    total_txt_h = sum(ld[1] for ld in line_dims)+(len(lines)-1)*2
    current_y = hdr_h + 5 + max(0, (UI_EPD_HEIGHT-hdr_h-5 - total_txt_h) / 2)
    for i, l in enumerate(lines):
        w, _ = line_dims[i]
        draw.text((max(5, (UI_EPD_WIDTH-w)/2), current_y),
                  l, font=font_to_use, fill=fill)
        current_y += line_dims[i][1]+2
    display_on_epd(epd_instance, ui_image_buffer)


def display_final_message(message_text="RaspAP"):
    global ui_image_buffer, epd_instance, RASPAP_LOGO, font_l
    if not epd_instance or not ui_image_buffer:
        return
    draw = ImageDraw.Draw(ui_image_buffer)
    draw.rectangle((0, 0, UI_EPD_WIDTH, UI_EPD_HEIGHT), fill=255)
    lines = message_text.split('\n')
    line_dims = [get_text_dimensions(draw, line, font_l) for line in lines]
    total_txt_h = sum(ld[1] for ld in line_dims)+(len(lines)-1)*5
    content_h = total_txt_h
    logo_h_total = 0
    if RASPAP_LOGO:
        logo_w, logo_h = RASPAP_LOGO.size
        logo_h_total = logo_h
        content_h += logo_h_total + (5 if lines and lines[0] else 0)
    curr_y = max(5, (UI_EPD_HEIGHT - content_h)//2)
    if RASPAP_LOGO:
        draw.bitmap(((UI_EPD_WIDTH - logo_w)//2, curr_y), RASPAP_LOGO, fill=0)
        curr_y += logo_h + (5 if lines and lines[0] else 0)
    for i, line in enumerate(lines):
        txt_w, txt_h = line_dims[i]
        if curr_y + txt_h > UI_EPD_HEIGHT - 5:
            break
        draw.text(((UI_EPD_WIDTH-txt_w)//2, curr_y), line, font=font_l, fill=0)
        curr_y += txt_h+5
    logging.info(f"Displaying final screen: '{message_text}'")
    display_on_epd(epd_instance, ui_image_buffer)
    time.sleep(5)


def toggle_internet_feed_action():
    global current_wlan0_connection_status
    update_wlan0_connection_status()
    action = "DOWN" if current_wlan0_connection_status == "NET_CONNECTED" else "UP"
    display_message(
        f"Net: Setting\n{HOST_CONNECTED_VIA_INTERFACE} {action}", font_to_use=font_m)
    logging.info(f"Bringing {HOST_CONNECTED_VIA_INTERFACE} {action}...")
    run_command(
        f"sudo ip link set {HOST_CONNECTED_VIA_INTERFACE} {'down' if action == 'DOWN' else 'up'}")
    if action == "UP":
        run_command(
            f"sudo systemctl try-restart wpa_supplicant@{HOST_CONNECTED_VIA_INTERFACE}.service || sudo systemctl try-restart wpa_supplicant.service")
        time.sleep(10)
    update_wlan0_connection_status()


def reboot_pi(): display_message("Rebooting..."); time.sleep(
    1); display_final_message("RaspAP\nRebooting"); run_command("sudo reboot")


def shutdown_pi(): display_message("Shutting down..."); time.sleep(
    1); display_final_message("RaspAP\nPowered Off"); run_command("sudo shutdown now")


def get_touch_coordinates(dev, gt_data, gt_old_data):
    global TOUCH_INT_PIN, tp_config_module
    if not dev or not gt_data or not gt_old_data or not tp_config_module:
        return None, None
    try:
        touch_active = (tp_config_module.digital_read(TOUCH_INT_PIN) == 0) if TOUCH_INT_PIN and hasattr(
            tp_config_module, 'digital_read') else True
        if touch_active:
            gt_data.Touch = 1
        elif gt_data.Touch == 0:
            return None, None
        dev.GT_Scan(gt_data, gt_old_data)
        if hasattr(gt_data, 'TouchCount') and gt_data.TouchCount > 0:
            ui_x = UI_EPD_WIDTH - 1 - gt_data.Y[0]
            ui_y = gt_data.X[0]
            return max(0, min(ui_x, UI_EPD_WIDTH-1)), max(0, min(ui_y, UI_EPD_HEIGHT-1))
    except Exception as e:
        logging.error(f"Touch coord error: {e}")
        logging.debug(traceback.format_exc())
    if gt_data:
        gt_data.Touch = 0
    return None, None


def draw_button(draw, x, y, w, h, txt, sel=False, font=font_s):
    fill_btn, fill_txt = (0, 255) if sel else (255, 0)
    draw.rectangle((x, y, x+w, y+h), fill=fill_btn, outline=0)
    if not sel:
        draw.rectangle((x+1, y+1, x+w-1, y+h-1), outline=0, width=1)
    txt_w, txt_h = get_text_dimensions(draw, txt, font)
    draw.text((x+(w-txt_w)//2, y+(h-txt_h)//2), txt, font=font, fill=fill_txt)


def check_button_press(tx, ty):
    global show_system_menu, show_info_screen, show_vpn_menu, info_page, last_button_press
    global available_vpns, vpn_list_scroll_offset

    if tx is None or ty is None:
        return ACTION_NONE
    now = time.time()
    if now - last_button_press < touch_cooldown:
        return ACTION_NONE

    pressed_action_name = None
    action_caused_menu_change = False

    if show_vpn_menu:
        for name, (x, y, w, h) in VPN_LIST_NAV_BUTTON_AREAS.items():
            if x <= tx <= x+w and y <= ty <= y+h:
                if name == 'up':
                    if vpn_list_scroll_offset > 0:
                        vpn_list_scroll_offset -= 1
                        pressed_action_name = "VPN_SCROLL_UP"
                elif name == 'down':
                    if vpn_list_scroll_offset < len(available_vpns) - VPN_LIST_ITEMS_PER_SCREEN:
                        vpn_list_scroll_offset += 1
                        pressed_action_name = "VPN_SCROLL_DOWN"
                elif name == 'disconnect':
                    if current_vpn_status == "VPN_ON":
                        disconnect_vpn()
                        pressed_action_name = "VPN_DISCONNECT"
                elif name == 'back':
                    show_vpn_menu = False
                    action_caused_menu_change = True
                    pressed_action_name = "VPN_BACK"
                break

        if not pressed_action_name:
            list_y_current = VPN_LIST_Y_START
            for i in range(vpn_list_scroll_offset, min(len(available_vpns), vpn_list_scroll_offset + VPN_LIST_ITEMS_PER_SCREEN)):
                vpn_def = available_vpns[i]
                item_x, item_y, item_w, item_h = VPN_LIST_X_START, list_y_current, VPN_LIST_WIDTH, VPN_LIST_ITEM_HEIGHT
                if item_x <= tx <= item_x + item_w and item_y <= ty <= item_y + item_h:
                    pressed_action_name = f"VPN_CONNECT_{vpn_def['name']}"
                    connect_vpn(vpn_def)
                    break
                list_y_current += VPN_LIST_ITEM_HEIGHT + 2

    elif show_info_screen:
        for name, (x, y, w, h) in INFO_BUTTON_AREAS.items():
            if x <= tx <= x+w and y <= ty <= y+h:
                pressed_action_name = f"INFO_{name.upper()}"
                if name == 'prev':
                    info_page = max(0, info_page - 1)
                elif name == 'next':
                    info_page = min(MAX_INFO_PAGES - 1, info_page + 1)
                elif name == 'back':
                    show_info_screen = False
                    action_caused_menu_change = True
                break
    elif show_system_menu:
        for name, (x, y, w, h) in SYSTEM_BUTTON_AREAS.items():
            if x <= tx <= x+w and y <= ty <= y+h:
                pressed_action_name = f"SYS_{name.upper()}"
                if name == 'reboot':
                    reboot_pi()
                elif name == 'shutdown':
                    shutdown_pi()
                elif name == 'back':
                    show_system_menu = False
                    action_caused_menu_change = True
                break
    else:
        for name, (x, y, w, h) in BUTTON_AREAS.items():
            if x <= tx <= x+w and y <= ty <= y+h:
                pressed_action_name = f"MAIN_{name.upper()}"
                if name == 'net_toggle':
                    toggle_internet_feed_action()
                elif name == 'vpn_menu':
                    show_vpn_menu = True
                    vpn_list_scroll_offset = 0
                    action_caused_menu_change = True
                elif name == 'system':
                    show_system_menu = True
                    action_caused_menu_change = True
                elif name == 'info':
                    show_info_screen = True
                    info_page = 0
                    action_caused_menu_change = True
                break

    if pressed_action_name:
        logging.info(f"Button action: {pressed_action_name} @({tx},{ty}).")
        last_button_press = now
        if action_caused_menu_change:
            return ACTION_MENU_CHANGE
        return ACTION_NORMAL_PRESS
    return ACTION_NONE


def draw_main_ui_elements(draw):
    global current_wlan0_connection_status, current_ap_hotspot_status, current_vpn_status, current_vpn_display_name
    global show_system_menu, show_info_screen, show_vpn_menu, info_page, RASPAP_LOGO, available_vpns
    global vpn_list_scroll_offset

    draw.rectangle((0, 0, UI_EPD_WIDTH, UI_EPD_HEIGHT), fill=255)
    update_system_stats()
    info_w = UI_EPD_WIDTH-BTN_WIDTH-(BTN_MARGIN*2)
    hdr_h = 22
    y_offset = hdr_h+5

    if RASPAP_LOGO:
        lx, ly = 5, (hdr_h-RASPAP_LOGO.height)//2
        ly = max(2, ly)
        draw.bitmap((lx, ly), RASPAP_LOGO, fill=0)
        htx = lx+RASPAP_LOGO.width+5
    else:
        htx = 5
    thw, thh = get_text_dimensions(draw, "RaspAP", font_l)
    hty = (hdr_h-thh)//2
    hty = max(2, hty)
    draw.text((htx, hty), "RaspAP", font=font_l, fill=0)
    draw.line((5, hdr_h, UI_EPD_WIDTH - BTN_WIDTH -
              (BTN_MARGIN*2) - BTN_MARGIN, hdr_h), fill=0)

    if show_vpn_menu:
        draw.text((VPN_LIST_X_START, BTN_MARGIN),
                  "VPN Connections:", font=font_m, fill=0)

        list_y_current = VPN_LIST_Y_START
        if not available_vpns:
            draw.text((VPN_LIST_X_START, list_y_current),
                      "No VPNs configured.", font=font_s, fill=0)
        else:
            for i in range(vpn_list_scroll_offset, min(len(available_vpns), vpn_list_scroll_offset + VPN_LIST_ITEMS_PER_SCREEN)):
                vpn_def = available_vpns[i]
                vpn_name_to_display = vpn_def['name']
                is_active = current_vpn_config_basename == vpn_def[
                    'config_basename_no_ext'] and current_vpn_status == "VPN_ON"

                _, text_height_s = get_text_dimensions(draw, "A", font_s)

                if is_active:
                    draw.rectangle((VPN_LIST_X_START, list_y_current,
                                    VPN_LIST_X_START + VPN_LIST_WIDTH, list_y_current + VPN_LIST_ITEM_HEIGHT-1),
                                   fill=0, outline=0)
                    draw.text((VPN_LIST_X_START + 2, list_y_current + (VPN_LIST_ITEM_HEIGHT - text_height_s)//2 - 1),
                              vpn_name_to_display[:20], font=font_s, fill=255)
                else:
                    draw.text((VPN_LIST_X_START + 2, list_y_current + (VPN_LIST_ITEM_HEIGHT - text_height_s)//2 - 1),
                              vpn_name_to_display[:20], font=font_s, fill=0)
                list_y_current += VPN_LIST_ITEM_HEIGHT + 2

        draw_button(draw, *VPN_LIST_NAV_BUTTON_AREAS['up'], "UP", sel=(
            vpn_list_scroll_offset == 0 and len(available_vpns) > 0))
        max_scroll = len(available_vpns) - VPN_LIST_ITEMS_PER_SCREEN
        if max_scroll < 0:
            max_scroll = 0
        draw_button(draw, *VPN_LIST_NAV_BUTTON_AREAS['down'], "DOWN", sel=(
            vpn_list_scroll_offset >= max_scroll and len(available_vpns) > 0))
        if current_vpn_status == "VPN_ON":
            draw_button(
                draw, *VPN_LIST_NAV_BUTTON_AREAS['disconnect'], "DISCONNECT")
        draw_button(draw, *VPN_LIST_NAV_BUTTON_AREAS['back'], "BACK")

    elif show_info_screen:
        y_info = y_offset
        if info_page == 0:
            draw.text(
                (5, y_info), f"Host Connection ({HOST_CONNECTED_VIA_INTERFACE}):", font=font_m, fill=0)
            y_info += 18
            host_ssid = get_host_connected_ssid()
            draw.text(
                (5, y_info), f"  Network: {host_ssid if host_ssid else 'Not Connected'}", font=font_s, fill=0)
            y_info += 15
            host_ip = get_interface_ip(HOST_CONNECTED_VIA_INTERFACE)
            draw.text(
                (5, y_info), f"  IP: {host_ip if host_ip else 'N/A'}", font=font_s, fill=0)
            y_info += 15
            y_info += 5
            update_ap_hotspot_status()
            draw.text(
                (5, y_info), f"AP Hotspot ({AP_BROADCAST_INTERFACE}):", font=font_m, fill=0)
            y_info += 18
            if current_ap_hotspot_status == "AP_ON":
                ap_ssid = get_cached_ap_ssid()
                clients = get_cached_ap_clients()
                draw.text(
                    (5, y_info), f"  Status: ON SSID: {ap_ssid[:10]} Clients: {clients}", font=font_s, fill=0)
            else:
                draw.text((5, y_info), "  Status: OFF", font=font_s, fill=0)
        elif info_page == 1:
            draw.text(
                (5, y_info), f"CPU: {system_stats_cache['cpu_usage']:.1f}% Temp: {system_stats_cache['cpu_temp']:.1f}°C", font=font_s, fill=0)
            y_info += 15
            geo = system_stats_cache['geo_location']
            draw.text((5, y_info), f"GeoIP: {geo[:22]}", font=font_s, fill=0)

        pg_txt_x_base = UI_EPD_WIDTH - BTN_WIDTH - (BTN_MARGIN * 2)
        pg_txt = f"Page {info_page+1}/{MAX_INFO_PAGES}"
        ptw, _ = get_text_dimensions(draw, pg_txt, font_t)
        draw.text((pg_txt_x_base - ptw - BTN_MARGIN,
                  UI_EPD_HEIGHT-12), pg_txt, font=font_t, fill=0)

        draw_button(
            draw, *INFO_BUTTON_AREAS['prev'], "PREV", sel=(info_page == 0))
        draw_button(
            draw, *INFO_BUTTON_AREAS['next'], "NEXT", sel=(info_page == MAX_INFO_PAGES - 1))
        draw_button(draw, *INFO_BUTTON_AREAS['back'], "BACK")

    elif show_system_menu:
        draw.text((5, y_offset), "System Menu:", font=font_m, fill=0)
        draw_button(draw, *SYSTEM_BUTTON_AREAS['reboot'], "REBOOT")
        draw_button(draw, *SYSTEM_BUTTON_AREAS['shutdown'], "SHUTDOWN")
        draw_button(draw, *SYSTEM_BUTTON_AREAS['back'], "BACK")

    else:  # Main screen
        update_wlan0_connection_status()
        update_ap_hotspot_status()
        update_vpn_status()
        y_main = y_offset
        line_spacing = 17

        wlan0_is_fully_connected = (
            current_wlan0_connection_status == "NET_CONNECTED")
        host_ssid_main = get_host_connected_ssid()
        if wlan0_is_fully_connected:
            draw.text(
                (5, y_main), f"Net: {(host_ssid_main[:20] if host_ssid_main else 'Connecting...')}", font=font_m, fill=0)
        elif current_wlan0_connection_status == "NET_ASSOCIATED_NO_IP":
            draw.text((5, y_main), "Net: No IP", font=font_m, fill=0)
        else:
            draw.text((5, y_main), "Net: Disconnected", font=font_m, fill=0)
        y_main += line_spacing

        vpn_text = "VPN: Off"
        if current_vpn_status == "VPN_ON" and current_vpn_display_name:
            vpn_text = f"VPN: {current_vpn_display_name[:12]}"
        elif current_vpn_status == "VPN_CONNECTING":
            vpn_text = "VPN: Connecting"
        elif current_vpn_status == "VPN_DISCONNECTING":
            vpn_text = "VPN: Ending"
        elif current_vpn_status == "VPN_ERROR":
            vpn_text = "VPN: Error"
        draw.text((5, y_main), vpn_text, font=font_m, fill=0)
        y_main += line_spacing

        if current_ap_hotspot_status == "AP_ON":
            clients = get_cached_ap_clients()
            draw.text((5, y_main), f"Clients: {clients}", font=font_m, fill=0)
        else:
            draw.text((5, y_main), "Hotspot Off", font=font_m, fill=0)

        _, geo_h = get_text_dimensions(draw, "GeoIP: Placeholder", font_s)
        geoip_y = UI_EPD_HEIGHT - BTN_MARGIN - geo_h
        geo = system_stats_cache['geo_location']
        draw.text((5, geoip_y), f"GeoIP: {geo[:20]}", font=font_s, fill=0)
        _, cpu_h = get_text_dimensions(draw, "CPU Temp: Placeholder", font_s)
        cpu_temp_y = geoip_y - cpu_h - 2
        draw.text((5, cpu_temp_y),
                  f"CPU Temp: {system_stats_cache['cpu_temp']:.1f}°C", font=font_s, fill=0)

        draw_button(draw, *BUTTON_AREAS['net_toggle'],
                    "NET ON" if wlan0_is_fully_connected else "NET OFF", sel=not wlan0_is_fully_connected)
        draw_button(draw, *BUTTON_AREAS['vpn_menu'], "VPN")
        draw_button(draw, *BUTTON_AREAS['system'], "SYSTEM")
        draw_button(draw, *BUTTON_AREAS['info'], "INFO")

    display_on_epd(epd_instance, ui_image_buffer)


def get_current_display_state():
    state = {
        'show_info': show_info_screen, 'info_pg': info_page if show_info_screen else -1,
        'show_system': show_system_menu,
        'show_vpn': show_vpn_menu, 'vpn_scroll': vpn_list_scroll_offset if show_vpn_menu else -1,
        'wlan0_conn': current_wlan0_connection_status, 'host_ssid': get_host_connected_ssid(),
        'ap_hotspot_stat': current_ap_hotspot_status,
        'vpn_stat': current_vpn_status, 'vpn_name': current_vpn_display_name,
    }
    if current_ap_hotspot_status == "AP_ON":
        state['ap_bssid_w1'] = get_cached_ap_ssid()
        state['ap_clients_w1'] = get_cached_ap_clients()

    if not any([show_system_menu, show_info_screen, show_vpn_menu]):
        state['cpu_temp'] = f"{system_stats_cache.get('cpu_temp', 0):.1f}"
        state['geo_loc'] = system_stats_cache.get('geo_location', 'Unknown')
    elif show_info_screen and info_page == 0:
        state['wlan0_ip'] = get_interface_ip(HOST_CONNECTED_VIA_INTERFACE)
    elif show_info_screen and info_page == 1:
        state['cpu_use'] = f"{system_stats_cache.get('cpu_usage', 0):.1f}"
        state['geo_loc_info_pg'] = system_stats_cache.get(
            'geo_location', 'Unknown')
    return state


def have_states_changed(old, new):
    if not old:
        return True

    # Make copies to avoid modifying the original dicts during pop
    old_cp = old.copy()
    new_cp = new.copy()

    # Extract CPU temperature strings for separate comparison
    # The key 'cpu_temp' is used on the main screen in your get_current_display_state
    old_t_str = old_cp.pop('cpu_temp', None)
    new_t_str = new_cp.pop('cpu_temp', None)

    # Compare the rest of the dictionaries (excluding cpu_temp)
    if old_cp != new_cp:
        changed_items = {k: (old_cp.get(k), new_cp.get(k)) for k in set(
            old_cp) | set(new_cp) if old_cp.get(k) != new_cp.get(k)}
        logging.debug(f"State change (non-temp items): {changed_items}")
        return True

    # Now, compare CPU temperatures if both exist
    if old_t_str is not None and new_t_str is not None:
        try:
            # ---- THIS IS THE LINE TO CHANGE THE TEMP DELTA ----
            temp_delta_threshold = 2.0  # Example: Refresh if temp changes by more than 2.0 degrees
            # ---- CHANGE THE VALUE ABOVE ----
            if abs(float(new_t_str) - float(old_t_str)) > temp_delta_threshold:
                logging.debug(
                    f"CPU Temp redraw trigger ({temp_delta_threshold}°C delta): {old_t_str}°C -> {new_t_str}°C")
                return True
        # Handle case where temp might not be float parsable (e.g. "N/A")
        except ValueError:
            # If string representation changed (e.g. from number to "N/A")
            if old_t_str != new_t_str:
                logging.debug(
                    f"CPU Temp string representation changed: {old_t_str} -> {new_t_str}")
                return True
    elif old_t_str != new_t_str:  # If one temp value exists and the other doesn't
        logging.debug(f"CPU Temp presence changed: {old_t_str} -> {new_t_str}")
        return True

    return False


def main():
    global epd_instance, touch_instance, ui_image_buffer, current_gt_dev_data, old_gt_dev_data, tp_config_module, last_displayed_state
    global g_ignore_touch_input_temporarily, g_ignore_touch_until_timestamp

    current_state = None
    try:
        if EPD_DRIVER_CLASS is None:
            logging.critical("EPD_DRIVER_CLASS not defined.")
            sys.exit(1)
        epd_instance = EPD_DRIVER_CLASS()
        logging.info(f"EPD instance created: {EPD_DRIVER_CLASS.__name__}")

        init_success = False
        if hasattr(epd_instance, 'init') and callable(getattr(epd_instance, 'init')):
            try:
                if hasattr(EPD_DRIVER_CLASS, 'FULL_UPDATE'):
                    logging.info(
                        "EPD init: Using EPD_DRIVER_CLASS.FULL_UPDATE.")
                    if epd_instance.init(EPD_DRIVER_CLASS.FULL_UPDATE) == -1:
                        logging.critical(
                            "EPD init() failed (driver internal).")
                        sys.exit(1)
                else:
                    logging.warning(
                        "EPD_DRIVER_CLASS.FULL_UPDATE not found. Trying init(0).")
                    if epd_instance.init(0) == -1:
                        logging.critical("EPD init(0) failed.")
                        sys.exit(1)
                init_success = True
                logging.info("EPD init() called.")
            except Exception as e_init:
                logging.critical(
                    f"EPD init() error: {e_init}\n{traceback.format_exc()}")
                sys.exit(1)
        else:
            logging.critical("EPD instance has no callable init() method.")
            sys.exit(1)

        if not init_success:
            logging.critical("EPD init not successful.")
            sys.exit(1)

        if hasattr(epd_instance, 'Clear') and callable(getattr(epd_instance, 'Clear')):
            epd_instance.Clear(0xFF)
            logging.info("EPD Clear(0xFF) called.")
        elif hasattr(epd_instance, 'clear') and callable(getattr(epd_instance, 'clear')):
            epd_instance.clear(0xFF)
            logging.info("EPD clear(0xFF) called.")
        else:
            logging.warning("EPD has no Clear/clear method.")

        ui_image_buffer = Image.new('1', (UI_EPD_WIDTH, UI_EPD_HEIGHT), 255)

        if TOUCH_DRIVER_CLASS and GT_Development_Class:
            try:
                touch_instance = TOUCH_DRIVER_CLASS()
                current_gt_dev_data = GT_Development_Class()
                old_gt_dev_data = GT_Development_Class()
                logging.info("Touch GT1151 instance created.")
                if hasattr(touch_instance, 'GT_Init'):
                    touch_instance.GT_Init()
                    logging.info("Touch GT_Init() called.")
            except Exception as e:
                logging.error(
                    f"Touch driver init ERROR: {e}\n{traceback.format_exc()}")
                touch_instance = None
        else:
            logging.warning(
                "Touch driver/GT_Development_Class missing. Touch disabled.")

        load_vpn_connections()
        update_vpn_status(initial_check=True)

        display_message("Initializing...")
        time.sleep(1)

        logging.info("Performing initial UI draw...")
        update_system_stats(force_geoip_update=True)
        update_wlan0_connection_status()
        update_ap_hotspot_status()

        current_state = get_current_display_state()
        draw_main_ui_elements(ImageDraw.Draw(ui_image_buffer))
        last_displayed_state = current_state

        logging.info("Starting main loop...")
        loop_counter = 0
        while True:
            tx, ty = None, None
            redraw = False
            now_loop = time.time()
            loop_counter += 1

            if g_ignore_touch_input_temporarily and now_loop < g_ignore_touch_until_timestamp:
                pass
            elif g_ignore_touch_input_temporarily:
                g_ignore_touch_input_temporarily = False
                logging.debug("Touch ignore period ended.")

            if not g_ignore_touch_input_temporarily and touch_instance:
                tx, ty = get_touch_coordinates(
                    touch_instance, current_gt_dev_data, old_gt_dev_data)

            action_result = ACTION_NONE
            if tx is not None:
                action_result = check_button_press(tx, ty)

            if action_result == ACTION_NORMAL_PRESS:
                redraw = True
                logging.debug("Normal button press, flagging redraw.")
            elif action_result == ACTION_MENU_CHANGE:
                redraw = True
                g_ignore_touch_input_temporarily = True
                g_ignore_touch_until_timestamp = time.time() + 0.7
                logging.info(
                    f"Menu change. Ignoring touch until {g_ignore_touch_until_timestamp:.2f}")
                if current_gt_dev_data:
                    current_gt_dev_data.Touch = 0
                    current_gt_dev_data.TouchCount = 0

            if loop_counter % 10 == 0:
                update_system_stats()
                update_wlan0_connection_status()
                update_ap_hotspot_status()
                update_vpn_status()

            current_state_candidate = get_current_display_state()
            if not redraw and have_states_changed(last_displayed_state, current_state_candidate):
                logging.info(
                    "State change (periodic/no touch) triggered redraw.")
                redraw = True

            if redraw:
                current_state = get_current_display_state()
                draw_main_ui_elements(ImageDraw.Draw(ui_image_buffer))
                last_displayed_state = current_state

            time.sleep(0.1)

    except IOError as e:
        logging.error(f"IOError in main: {e}\n{traceback.format_exc()}")
    except KeyboardInterrupt:
        logging.info("\nExiting via KeyboardInterrupt...")
    except Exception as e:
        logging.critical(
            f"UNEXPECTED ERROR IN MAIN: {e}\n{traceback.format_exc()}")
    finally:
        logging.info("Cleaning up...")
        if epd_instance and hasattr(epd_instance, 'sleep') and callable(getattr(epd_instance, 'sleep')):
            try:
                logging.info("Putting EPD to sleep.")
                epd_instance.sleep()
            except Exception as e:
                logging.error(f"EPD sleep error: {e}")

        if tp_config_module and hasattr(tp_config_module, 'module_exit') and callable(getattr(tp_config_module, 'module_exit')):
            try:
                logging.info("Calling TP_lib.epdconfig.module_exit()...")
                tp_config_module.module_exit()
            except Exception as e:
                logging.error(f"TP_lib.epdconfig.module_exit() error: {e}")
        elif epd_instance and hasattr(epd_instance, 'Dev_exit') and callable(getattr(epd_instance, 'Dev_exit')):
            try:
                logging.info("Calling EPD driver Dev_exit()...")
                epd_instance.Dev_exit()
            except Exception as e:
                logging.error(f"EPD driver Dev_exit() error: {e}")
        logging.info("Script finished.")


if __name__ == "__main__":
    main()
