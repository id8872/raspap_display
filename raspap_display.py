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

# --- Configure Logging ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# --- Load API Key and Base URL ---
RASPAP_API_KEY = os.environ.get('RASPAP_API_KEY')
RASPAP_BASE_URL = os.environ.get(
    'RASPAP_API_BASE_URL', "http://localhost:8081")
if RASPAP_API_KEY is None: logging.warning("RASPAP_API_KEY env var not set. API features will use fallbacks.")
else: logging.info(f"RASPAP_API_KEY loaded. API base URL: {RASPAP_BASE_URL}")

script_dir = os.path.dirname(os.path.abspath(__file__))
lib_parent_path = os.path.join(script_dir, "lib")
if lib_parent_path not in sys.path: sys.path.insert(0, lib_parent_path)

EPD_DRIVER_CLASS = None; TOUCH_DRIVER_CLASS = None; GT_Development_Class = None
tp_config_module = None; TOUCH_INT_PIN = None
try:
    logging.info("Importing drivers from TP_lib...")
    from TP_lib import epdconfig as tp_epdconfig_module
    from TP_lib import epd2in13_V4; from TP_lib import gt1151 # Assuming gt1151 is correctly imported
    tp_config_module = tp_epdconfig_module
    EPD_DRIVER_CLASS = epd2in13_V4.EPD; logging.info("Imported EPD: TP_lib.epd2in13_V4.EPD")
    TOUCH_DRIVER_CLASS = gt1151.GT1151; GT_Development_Class = gt1151.GT_Development
    TOUCH_INT_PIN = getattr(tp_config_module, 'INT', getattr(tp_config_module, 'TP_INT_PIN', None))
    if TOUCH_INT_PIN: logging.debug(f"Touch INT pin: {TOUCH_INT_PIN}")
    else: logging.warning("Touch INT pin not found in TP_lib.epdconfig.")
    logging.info(f"Imported Touch: TP_lib.gt1151.GT1151")
except Exception as e: logging.critical(f"CRITICAL ERROR during driver imports: {e}\n{traceback.format_exc()}"); sys.exit(1)

PHYSICAL_EPD_WIDTH = EPD_DRIVER_CLASS.width if EPD_DRIVER_CLASS and hasattr(EPD_DRIVER_CLASS,'width') else 122
PHYSICAL_EPD_HEIGHT = EPD_DRIVER_CLASS.height if EPD_DRIVER_CLASS and hasattr(EPD_DRIVER_CLASS,'height') else 250
UI_EPD_WIDTH = 250; UI_EPD_HEIGHT = 122

HOST_CONNECTED_VIA_INTERFACE = "wlan0"
AP_BROADCAST_INTERFACE = "wlan1"

BTN_WIDTH=60; BTN_HEIGHT=30; BTN_MARGIN=5; BTN_X=UI_EPD_WIDTH-BTN_WIDTH-BTN_MARGIN
total_btn_h=3*BTN_HEIGHT; rem_h=UI_EPD_HEIGHT-total_btn_h-(2*BTN_MARGIN); gap_h=rem_h/2 if rem_h>0 else BTN_MARGIN
BTN_AP_Y=BTN_MARGIN; BTN_SYS_Y=BTN_AP_Y+BTN_HEIGHT+int(gap_h); BTN_INFO_Y=BTN_SYS_Y+BTN_HEIGHT+int(gap_h)
BUTTON_AREAS={'ap_toggle':(BTN_X,BTN_AP_Y,BTN_WIDTH,BTN_HEIGHT),'system':(BTN_X,BTN_SYS_Y,BTN_WIDTH,BTN_HEIGHT),'info':(BTN_X,BTN_INFO_Y,BTN_WIDTH,BTN_HEIGHT)}
SYSTEM_BUTTON_AREAS={'reboot':(BTN_X,BTN_AP_Y,BTN_WIDTH,BTN_HEIGHT),'shutdown':(BTN_X,BTN_SYS_Y,BTN_WIDTH,BTN_HEIGHT),'back':(BTN_X,BTN_INFO_Y,BTN_WIDTH,BTN_HEIGHT)}

current_wlan0_connection_status = "INIT"
current_ap_hotspot_status = "INIT"

show_system_menu=False; show_info_screen=False; info_page=0; MAX_INFO_PAGES=2
touch_cooldown=0.5; last_button_press=0; last_displayed_state={}

# Globals for touch de-glitching / menu transition handling
g_ignore_touch_input_temporarily = False 
g_ignore_touch_until_timestamp = 0
ACTION_MENU_CHANGE = 2
ACTION_NORMAL_PRESS = 1
ACTION_NONE = 0

last_periodic_update_check = 0
periodic_update_interval = 5 # Seconds

try:
    assets_dir=os.path.join(script_dir,"assets")
    font_b_path=os.path.join(assets_dir,"DejaVuSans-Bold.ttf") if os.path.exists(os.path.join(assets_dir,"DejaVuSans-Bold.ttf")) else '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    font_r_path=os.path.join(assets_dir,"DejaVuSans.ttf") if os.path.exists(os.path.join(assets_dir,"DejaVuSans.ttf")) else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    font_l=ImageFont.truetype(font_b_path,16); font_m=ImageFont.truetype(font_r_path,13); font_s=ImageFont.truetype(font_r_path,11); font_t=ImageFont.truetype(font_r_path,9)
except IOError: logging.warning("DejaVu fonts fail, using default."); font_l,font_m,font_s,font_t=(ImageFont.load_default(),)*4

RASPAP_LOGO=None
try:
    logo_p=os.path.join(assets_dir,"raspAP-logo.png")
    if os.path.exists(logo_p): RASPAP_LOGO=ImageOps.invert(Image.open(logo_p).convert("1")).resize((20,20)); logging.info("RaspAP logo loaded.")
except Exception as e: logging.warning(f"Logo load err: {e}")

epd_instance=None;touch_instance=None;ui_image_buffer=None;current_gt_dev_data=None;old_gt_dev_data=None
system_stats_cache={'cpu_temp':0,'cpu_usage':0,'mem_usage':0,'uptime':'','last_update':0,'geo_location':'Unknown','last_geo_update':0}
_ap_details_cache={'data':None,'timestamp':0}

def get_text_dimensions(draw,text,font):
    if hasattr(font,'getbbox'): bbox=font.getbbox(text); return bbox[2]-bbox[0],bbox[3]-bbox[1]
    if hasattr(draw,'textbbox'): bbox=draw.textbbox((0,0),text,font=font); return bbox[2]-bbox[0],bbox[3]-bbox[1]
    return draw.textsize(text,font=font)

def call_raspap_api(endpoint,method="GET",json_data=None,params=None):
    if not RASPAP_API_KEY: return None
    url=f"{RASPAP_BASE_URL}/{endpoint.lstrip('/')}"; headers={"Accept":"application/json","access_token":RASPAP_API_KEY}
    try:
        logging.debug(f"API Call: {method} {url} Params: {params} Data: {json_data}")
        if method.upper()=="GET": resp=requests.get(url,headers=headers,params=params,timeout=5)
        elif method.upper()=="POST": resp=requests.post(url,headers=headers,json=json_data,params=params,timeout=5)
        else: logging.error(f"Unsupported API method: {method}"); return None
        if resp.status_code==204 or not resp.content: return {"success":True,"status_code":resp.status_code}
        resp.raise_for_status(); return resp.json()
    except Exception as e: logging.warning(f"API Call Error to {url}: {e}"); return None

def run_command(command):
    try:
        proc=subprocess.Popen(command,shell=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE); stdout,stderr=proc.communicate(timeout=15)
        if proc.returncode!=0: 
            logging.debug(f"Command '{command}' failed with exit code {proc.returncode}. Error: {stderr.decode('utf-8').strip()}")
            return ""
        return stdout.decode('utf-8').strip()
    except Exception as e: logging.error(f"Command execution error for '{command}': {e}"); return ""

def get_ap_hotspot_status_via_api():
    data=call_raspap_api("system")
    if data and isinstance(data,dict):
        sc=data.get('hostapdStatus')
        if isinstance(sc,int): 
            logging.debug(f"API hostapdStatus: {'active' if sc==1 else 'inactive'}")
            return sc==1
    logging.debug("get_ap_hotspot_status_via_api: Falling back to system command.")
    return run_command("sudo systemctl is-active hostapd")=="active"

def get_ap_details_from_api(cache_duration=5):
    global _ap_details_cache; now=time.time()
    if _ap_details_cache['data'] and (now-_ap_details_cache['timestamp']<cache_duration): return _ap_details_cache['data']
    data=call_raspap_api("ap"); 
    _ap_details_cache={'data':data if data and isinstance(data,dict) else None,'timestamp':now}
    return _ap_details_cache['data']

def get_ap_broadcast_ssid():
    details=get_ap_details_from_api()
    if details:
        api_if,api_ssid=details.get('interface'),details.get('ssid')
        if api_if!=AP_BROADCAST_INTERFACE and api_if is not None: 
            logging.debug(f"API /ap interface:'{api_if}' differs from script AP target:'{AP_BROADCAST_INTERFACE}'. Using API SSID regardless.")
        if api_ssid: 
            logging.debug(f"AP SSID from API /ap: {api_ssid}")
            return api_ssid
        else: logging.warning(f"API /ap response missing SSID. Response (partial):{str(details)[:100]}")
    else: logging.warning("API /ap call failed or returned no data.")
    
    logging.debug(f"get_ap_broadcast_ssid: Falling back to config file.")
    conf="/etc/raspap/hostapd.ini" if os.path.exists("/etc/raspap/hostapd.ini") else "/etc/hostapd/hostapd.conf"
    if os.path.exists(conf):
        out=run_command(f"grep -Po '^ssid=\\K.*' {conf}"); ssid_conf=out.split('\n')[0] if out else None
        if ssid_conf: 
            logging.debug(f"AP SSID from config file {conf}: {ssid_conf}")
            return ssid_conf
        logging.warning(f"Could not grep SSID from {conf}")
        return "AP N/A (grep)"
    logging.warning(f"Config file {conf} not found for AP SSID.")
    return "AP N/A (file)"

def get_host_connected_ssid(interface=HOST_CONNECTED_VIA_INTERFACE):
    logging.debug(f"Getting SSID for host on {interface}.")
    ssid=run_command(f"iwgetid -r {interface}")
    return ssid if ssid and ssid.strip() and ssid!="N/A" else None

def get_ap_clients_count_via_api():
    target_if=AP_BROADCAST_INTERFACE
    logging.debug(f"Getting AP client count for: {target_if}")
    data=call_raspap_api(f"clients/{target_if}")
    if data and isinstance(data,dict):
        if 'active_clients' in data and isinstance(data['active_clients'],list):
            count=len(data['active_clients'])
            logging.debug(f"API Clients from /clients/{target_if} (list length): {count}")
            if 'active_clients_amount' in data and data['active_clients_amount']!=count: 
                logging.debug(f"API Info: 'active_clients_amount':{data['active_clients_amount']}, list len:{count}. Using list length.")
            return count
        elif 'active_clients_amount' in data: 
            count=data['active_clients_amount']
            logging.warning(f"API /clients/{target_if}: active_clients list missing/invalid. Using 'active_clients_amount': {count}.")
            return count
        else: logging.warning(f"API /clients/{target_if} response missing client data. Response: {data}")
    else: 
        logging.debug(f"API /clients/{target_if} call failed or returned no data. Response: {data}")

    logging.debug(f"AP clients count: API call failed for {target_if}, falling back to system command.")
    if run_command(f"ip link show {target_if} up"): 
        out=run_command(f"sudo iw dev {target_if} station dump")
        if out: 
            count=len(re.findall(r"Station (([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2}))",out))
            logging.debug(f"System command clients on '{target_if}': {count}")
            return count
    logging.warning(f"Could not get AP client count for {target_if} via system command.")
    return 0

def get_interface_ip(interface_name):
    ip_output = run_command(f"ip -4 addr show {interface_name}")
    logging.debug(f"Raw 'ip addr' output for {interface_name}: '{ip_output}'")
    if not ip_output:
        logging.debug(f"No 'ip addr' output for {interface_name}")
        return None
    ips_found = re.findall(r"inet\s+([\d.]+)/\d+", ip_output)
    logging.debug(f"IPs found by regex for {interface_name}: {ips_found}")
    for ip_addr in ips_found:
        logging.debug(f"Checking IP {ip_addr} for {interface_name}")
        if not ip_addr.startswith("127.") and not ip_addr.startswith("169.254."):
            logging.debug(f"Found valid IP {ip_addr} for {interface_name}. Returning it.")
            return ip_addr
    logging.debug(f"No valid (non-local, non-link-local) IP found in list for {interface_name}")
    return None

def update_wlan0_connection_status():
    global current_wlan0_connection_status
    iface_to_check = HOST_CONNECTED_VIA_INTERFACE
    ip_address = get_interface_ip(iface_to_check)
    old_status = current_wlan0_connection_status
    if ip_address:
        current_wlan0_connection_status = "NET_CONNECTED"
        if old_status != current_wlan0_connection_status:
            ssid_log = get_host_connected_ssid(iface_to_check)
            logging.info(f"Host ({iface_to_check}) status: {current_wlan0_connection_status} (IP: {ip_address}, SSID: {ssid_log or 'N/A'})")
    else:
        ssid = get_host_connected_ssid(iface_to_check)
        if ssid:
            current_wlan0_connection_status = "NET_ASSOCIATED_NO_IP"
            if old_status != current_wlan0_connection_status:
                 logging.info(f"Host ({iface_to_check}) status: {current_wlan0_connection_status} (SSID: {ssid}, IP: None)")
        else:
            current_wlan0_connection_status = "NET_DISCONNECTED"
            if old_status != current_wlan0_connection_status:
                logging.info(f"Host ({iface_to_check}) status: {current_wlan0_connection_status} (SSID: None, IP: None)")

def update_ap_hotspot_status():
    global current_ap_hotspot_status
    old_status = current_ap_hotspot_status
    current_ap_hotspot_status = "AP_ON" if get_ap_hotspot_status_via_api() else "AP_OFF"
    if old_status != current_ap_hotspot_status:
        logging.info(f"Hostapd service (for AP on {AP_BROADCAST_INTERFACE}): {current_ap_hotspot_status}")

def get_external_ip_location():
    try:
        resp=requests.get("https://ipapi.co/json/?fields=city,country_name",timeout=3); resp.raise_for_status(); data=resp.json()
        co,ci=data.get('country_name','?'),data.get('city','?')
        if ci!='?' and co!='?': return f"{ci}, {co}"
        return co if co!='?' else ci if ci!='?' else "Unknown"
    except Exception as e: logging.warning(f"GeoIP lookup failed: {e}"); return "Unknown"

def update_system_stats():
    global system_stats_cache; now=time.time()
    if now-system_stats_cache['last_update']<5: return 
    try: t=run_command("cat /sys/class/thermal/thermal_zone0/temp"); system_stats_cache['cpu_temp']=int(t)/1000 if t else 0
    except: system_stats_cache['cpu_temp']=0
    try: c=run_command("top -bn1 | grep 'Cpu(s)' | awk '{print $2+$4}'"); system_stats_cache['cpu_usage']=float(c) if c else 0
    except: system_stats_cache['cpu_usage']=0
    try: m=run_command("free | grep Mem | awk '{print ($3/$2) * 100.0}'"); system_stats_cache['mem_usage']=float(m) if m else 0
    except: system_stats_cache['mem_usage']=0
    try:
        up_out=run_command("cat /proc/uptime")
        if up_out: s=float(up_out.split()[0]); d=int(s//86400);h=int((s%86400)//3600);m=int((s%3600)//60); system_stats_cache['uptime']=f"{d}d {h}h {m}m"
    except: system_stats_cache['uptime']="N/A"
    if now-system_stats_cache.get('last_geo_update',0)>900: 
        system_stats_cache['geo_location']=get_external_ip_location(); system_stats_cache['last_geo_update']=now
    system_stats_cache['last_update']=now

def display_on_epd(dev,img):
    if not dev: logging.warning("EPD instance is None, cannot display."); return
    try: dev.display(dev.getbuffer(img.convert('1')))
    except Exception as e: logging.error(f"EPD display error: {e}"); logging.debug(traceback.format_exc())

def display_message(msg_txt,font_to_use=font_l,fill=0):
    global ui_image_buffer,epd_instance,RASPAP_LOGO
    if not epd_instance or not ui_image_buffer: logging.warning("EPD/UI buffer None, cannot display message."); return
    draw=ImageDraw.Draw(ui_image_buffer); draw.rectangle((0,0,UI_EPD_WIDTH,UI_EPD_HEIGHT),fill=255)
    hdr_h=25
    if RASPAP_LOGO: 
        lx,ly=5,5; 
        tw,th=get_text_dimensions(draw,"RaspAP",font_l)
        draw.bitmap((lx,ly),RASPAP_LOGO,fill=0)
        draw.text((lx+RASPAP_LOGO.width+5,ly+(RASPAP_LOGO.height-th)//2),"RaspAP",font=font_l,fill=0)
    else: draw.text((5,5),"RaspAP",font=font_l,fill=0)
    
    lines=msg_txt.split('\n'); line_dims = [get_text_dimensions(draw,l,font_to_use) for l in lines]
    total_txt_h=sum(ld[1] for ld in line_dims)+(len(lines)-1)*2 
    
    avail_h_for_text = UI_EPD_HEIGHT-hdr_h-5 
    current_y = hdr_h + 5 + (avail_h_for_text - total_txt_h) / 2
    current_y = max(hdr_h + 5, current_y) 

    for i,l in enumerate(lines): 
        w,_= line_dims[i]
        text_x=(UI_EPD_WIDTH-w)/2
        text_x=max(5,text_x) 
        draw.text((text_x,current_y),l,font=font_to_use,fill=fill)
        current_y += line_dims[i][1]+2
    display_on_epd(epd_instance,ui_image_buffer)

def display_final_message(message_text="RaspAP"):
    global ui_image_buffer, epd_instance, RASPAP_LOGO, font_l
    if not epd_instance or not ui_image_buffer:
        logging.warning("EPD/UI buffer None for final message.")
        return
    
    draw = ImageDraw.Draw(ui_image_buffer)
    draw.rectangle((0, 0, UI_EPD_WIDTH, UI_EPD_HEIGHT), fill=255) 

    lines = message_text.split('\n')
    line_dims = [get_text_dimensions(draw, line, font_l) for line in lines]
    total_text_block_height = sum(ld[1] for ld in line_dims) + (len(lines) - 1) * 5 

    content_height = total_text_block_height
    logo_total_height = 0
    if RASPAP_LOGO:
        logo_width, logo_height = RASPAP_LOGO.size
        logo_total_height = logo_height
        if lines and lines[0]: logo_total_height += 5 
        content_height += logo_total_height
    
    start_y = (UI_EPD_HEIGHT - content_height) // 2
    if start_y < 5: start_y = 5 

    current_y = start_y
    if RASPAP_LOGO:
        logo_x = (UI_EPD_WIDTH - logo_width) // 2
        draw.bitmap((logo_x, current_y), RASPAP_LOGO, fill=0)
        current_y += logo_height
        if lines and lines[0]: current_y += 5 

    for i, line in enumerate(lines):
        text_width, text_height = line_dims[i]
        text_x = (UI_EPD_WIDTH - text_width) // 2
        if current_y + text_height > UI_EPD_HEIGHT -5: break 
        draw.text((text_x, current_y), line, font=font_l, fill=0)
        current_y += text_height + 5 
    
    logging.info(f"Displaying final screen: '{message_text}'")
    display_on_epd(epd_instance, ui_image_buffer)
    time.sleep(5)

def toggle_wlan0_connection_system():
    global current_wlan0_connection_status
    update_wlan0_connection_status() 
    if current_wlan0_connection_status == "NET_CONNECTED":
        display_message(f"Sys: Disconnecting\n{HOST_CONNECTED_VIA_INTERFACE}", font_to_use=font_m)
        logging.info(f"System: Bringing {HOST_CONNECTED_VIA_INTERFACE} DOWN...")
        run_command(f"sudo ip link set {HOST_CONNECTED_VIA_INTERFACE} down")
    else:
        display_message(f"Sys: Connecting\n{HOST_CONNECTED_VIA_INTERFACE}", font_to_use=font_m)
        logging.info(f"System: Bringing {HOST_CONNECTED_VIA_INTERFACE} UP...")
        run_command(f"sudo ip link set {HOST_CONNECTED_VIA_INTERFACE} up")
        logging.info(f"System: Attempting to restart wpa_supplicant for {HOST_CONNECTED_VIA_INTERFACE}...")
        run_command(f"sudo systemctl try-restart wpa_supplicant@{HOST_CONNECTED_VIA_INTERFACE}.service || sudo systemctl try-restart wpa_supplicant.service")
        logging.info(f"Giving {HOST_CONNECTED_VIA_INTERFACE} 10s to connect...")
        time.sleep(10)
    update_wlan0_connection_status() 

def toggle_internet_feed_action():
    logging.info(f"Toggle Internet Feed ({HOST_CONNECTED_VIA_INTERFACE}) action triggered.")
    toggle_wlan0_connection_system()

def toggle_ap_hotspot_action_system():
    global current_ap_hotspot_status
    update_ap_hotspot_status() 
    if current_ap_hotspot_status == "AP_ON":
        display_message(f"Sys: Stop Hotspot\n({AP_BROADCAST_INTERFACE})", font_to_use=font_m)
        logging.info(f"Sys: Stopping Hotspot on {AP_BROADCAST_INTERFACE}...")
        run_command("sudo systemctl stop hostapd"); run_command("sudo systemctl stop dnsmasq")
    else:
        display_message(f"Sys: Start Hotspot\n({AP_BROADCAST_INTERFACE})", font_to_use=font_m)
        logging.info(f"Sys: Starting Hotspot on {AP_BROADCAST_INTERFACE}...")
        run_command("sudo rfkill unblock wifi"); time.sleep(1)
        run_command(f"sudo systemctl stop wpa_supplicant@{AP_BROADCAST_INTERFACE}.service || sudo systemctl stop wpa_supplicant.service")
        time.sleep(1)
        run_command("sudo systemctl restart dnsmasq"); run_command("sudo systemctl restart hostapd")
    time.sleep(5); update_ap_hotspot_status() 

def reboot_pi(): 
    logging.info("Rebooting system...")
    display_message("Rebooting...") 
    time.sleep(1) 
    display_final_message("RaspAP\nRebooting") 
    run_command("sudo reboot")

def shutdown_pi(): 
    logging.info("Shutting system down...")
    display_message("Shutting down...") 
    time.sleep(1) 
    display_final_message("RaspAP\nPowered Off") 
    run_command("sudo shutdown now")

def get_touch_coordinates(dev,gt_data,gt_old_data):
    global TOUCH_INT_PIN,tp_config_module
    if not dev or not gt_data or not gt_old_data or not tp_config_module:
        return None,None
    try:
        touch_active_signal = False
        if TOUCH_INT_PIN and hasattr(tp_config_module,'digital_read'):
            pin_state = tp_config_module.digital_read(TOUCH_INT_PIN)
            touch_active_signal = (pin_state == 0)
        else:
            touch_active_signal = True 

        if touch_active_signal:
            gt_data.Touch=1 
        elif gt_data.Touch==0: 
            return None,None

        dev.GT_Scan(gt_data,gt_old_data) 

        if hasattr(gt_data, 'TouchCount') and gt_data.TouchCount > 0:
            raw_x_from_driver = gt_data.X[0] 
            raw_y_from_driver = gt_data.Y[0] 
            
            ui_x = UI_EPD_WIDTH - 1 - raw_y_from_driver
            ui_y = raw_x_from_driver
            
            # logging.debug(f"Touch: Driver raw X[0]={raw_x_from_driver}, Y[0]={raw_y_from_driver} ==> UI (ux,uy)=({ui_x},{ui_y})")
            
            return max(0,min(ui_x,UI_EPD_WIDTH-1)),max(0,min(ui_y,UI_EPD_HEIGHT-1))
    except Exception as e:
        logging.error(f"Touch coordinate retrieval error: {e}")
        logging.debug(traceback.format_exc()) 

    if gt_data: gt_data.Touch=0 
    return None,None

def draw_button(draw,x,y,w,h,txt,sel=False):
    fill_btn,fill_txt=(0,255) if sel else (255,0)
    draw.rectangle((x,y,x+w,y+h),fill=fill_btn,outline=0)
    if not sel: draw.rectangle((x,y,x+w-1,y+h-1),outline=0) 
    txt_w,txt_h=get_text_dimensions(draw,txt,font_s); 
    draw.text((x+(w-txt_w)//2,y+(h-txt_h)//2),txt,font=font_s,fill=fill_txt)

def check_button_press(tx,ty): 
    global show_system_menu, show_info_screen, info_page, last_button_press
    # g_ignore_touch_input_temporarily and g_ignore_touch_until_timestamp are used in main loop now

    if tx is None or ty is None: return ACTION_NONE

    now = time.time()
    if now - last_button_press < touch_cooldown:
        logging.debug(f"Button press @({tx},{ty}) ignored: general touch cooldown active.")
        return ACTION_NONE 
    
    pressed = None
    action_caused_menu_change = False 

    if show_info_screen:
        ax,ay,aw,ah=BUTTON_AREAS['ap_toggle']; sx,sy,sw,sh=BUTTON_AREAS['system']; ix,iy,iw,ih=BUTTON_AREAS['info']
        if ax<=tx<=ax+aw and ay<=ty<=ay+ah: 
            pressed="INFO_PREV"; info_page=max(0,info_page-1) 
        elif sx<=tx<=sx+sw and sy<=ty<=sy+sh: 
            pressed="INFO_NEXT"; info_page=min(MAX_INFO_PAGES-1,info_page+1) 
        elif ix<=tx<=ix+iw and iy<=ty<=iy+ih: 
            pressed="INFO_BACK"; show_info_screen=False; action_caused_menu_change = True
    elif show_system_menu:
        for n,(x,y,w,h) in SYSTEM_BUTTON_AREAS.items():
            if x<=tx<=x+w and y<=ty<=y+h:
                pressed=f"SYS_{n.upper()}"
                if n=='reboot': reboot_pi() # Exits script
                elif n=='shutdown': shutdown_pi() # Exits script
                elif n=='back': 
                    show_system_menu=False; action_caused_menu_change = True
                break
    else: 
        for n,(x,y,w,h) in BUTTON_AREAS.items():
            if x<=tx<=x+w and y<=ty<=y+h:
                pressed=f"MAIN_{n.upper()}"
                if n=='ap_toggle': toggle_internet_feed_action() 
                elif n=='system': 
                    show_system_menu=True; action_caused_menu_change = True
                elif n=='info': 
                    show_info_screen=True; info_page=0; action_caused_menu_change = True
                break
    
    if pressed: 
        logging.info(f"Button action: {pressed} @({tx},{ty}).")
        last_button_press = now 
        if action_caused_menu_change:
            logging.debug(f"Menu change triggered by '{pressed}'.")
            return ACTION_MENU_CHANGE 
        return ACTION_NORMAL_PRESS
        
    return ACTION_NONE

def draw_main_ui_elements(draw):
    global current_wlan0_connection_status, current_ap_hotspot_status, show_system_menu, show_info_screen, info_page, RASPAP_LOGO
    draw.rectangle((0,0,UI_EPD_WIDTH,UI_EPD_HEIGHT),fill=255); update_system_stats()
    info_w = UI_EPD_WIDTH-BTN_WIDTH-(BTN_MARGIN*2); hdr_h=22; y=hdr_h+5

    if RASPAP_LOGO: 
        lx,ly=5,(hdr_h-RASPAP_LOGO.height)//2; ly=max(2,ly); 
        draw.bitmap((lx,ly),RASPAP_LOGO,fill=0); 
        htx=lx+RASPAP_LOGO.width+5
    else: htx=5
    thw,thh=get_text_dimensions(draw,"RaspAP",font_l); hty=(hdr_h-thh)//2; hty=max(2,hty); 
    draw.text((htx,hty),"RaspAP",font=font_l,fill=0)
    draw.line((5,hdr_h,info_w-BTN_MARGIN,hdr_h),fill=0)

    if show_info_screen:
        if info_page==0:
            y=hdr_h+5
            draw.text((5,y),f"Host Connection ({HOST_CONNECTED_VIA_INTERFACE}):",font=font_m,fill=0); y+=18
            host_ssid=get_host_connected_ssid(); draw.text((5,y),f"  Network: {host_ssid if host_ssid else 'Not Connected'}",font=font_s,fill=0); y+=15
            host_ip = get_interface_ip(HOST_CONNECTED_VIA_INTERFACE)
            draw.text((5,y),f"  IP: {host_ip if host_ip else 'N/A'}",font=font_s,fill=0); y+=15; y+=5

            update_ap_hotspot_status()
            draw.text((5,y),f"AP Hotspot ({AP_BROADCAST_INTERFACE}):",font=font_m,fill=0); y+=18
            if current_ap_hotspot_status=="AP_ON":
                ap_ssid=get_ap_broadcast_ssid() or "N/A"; clients=get_ap_clients_count_via_api()
                draw.text((5,y),f"  Status: ON",font=font_s,fill=0); y+=15
                draw.text((5,y),f"  SSID: {ap_ssid[:16]}",font=font_s,fill=0); y+=15
                draw.text((5,y),f"  Clients: {clients}",font=font_s,fill=0)
            else: draw.text((5,y),"  Status: OFF",font=font_s,fill=0)
            pg_txt=f"Page {info_page+1}/{MAX_INFO_PAGES}"; ptw,_=get_text_dimensions(draw,pg_txt,font_t); draw.text((info_w-BTN_MARGIN-ptw,UI_EPD_HEIGHT-12),pg_txt,font=font_t,fill=0)
        elif info_page==1:
            y=hdr_h+5
            draw.text((5,y),f"CPU: {system_stats_cache['cpu_usage']:.1f}%",font=font_s,fill=0); y+=15
            draw.text((5,y),f"CPU Temp: {system_stats_cache['cpu_temp']:.1f}°C",font=font_s,fill=0); y+=15
            draw.text((5,y),f"Memory: {system_stats_cache['mem_usage']:.1f}%",font=font_s,fill=0); y+=15
            draw.text((5,y),f"Uptime: {system_stats_cache['uptime']}",font=font_s,fill=0); y+=15
            geo=system_stats_cache['geo_location']; max_geo=22; geo=geo[:max_geo-3]+"..." if len(geo)>max_geo else geo
            draw.text((5,y),f"GeoIP: {geo}",font=font_s,fill=0)
            pg_txt=f"Page {info_page+1}/{MAX_INFO_PAGES}"; ptw,_=get_text_dimensions(draw,pg_txt,font_t); draw.text((info_w-BTN_MARGIN-ptw,UI_EPD_HEIGHT-12),pg_txt,font=font_t,fill=0)
        draw_button(draw,BTN_X,BTN_AP_Y,BTN_WIDTH,BTN_HEIGHT,"PREV",sel=(info_page==0))
        draw_button(draw,BTN_X,BTN_SYS_Y,BTN_WIDTH,BTN_HEIGHT,"NEXT",sel=(info_page==MAX_INFO_PAGES-1))
        draw_button(draw,BTN_X,BTN_INFO_Y,BTN_WIDTH,BTN_HEIGHT,"BACK")
    elif show_system_menu:
        draw.text((5,y),"System Menu:",font=font_m,fill=0)
        draw_button(draw,BTN_X,BTN_AP_Y,BTN_WIDTH,BTN_HEIGHT,"REBOOT")
        draw_button(draw,BTN_X,BTN_SYS_Y,BTN_WIDTH,BTN_HEIGHT,"SHUTDOWN")
        draw_button(draw,BTN_X,BTN_INFO_Y,BTN_WIDTH,BTN_HEIGHT,"BACK")
    else: # Main screen
        update_wlan0_connection_status(); update_ap_hotspot_status() 
        y=hdr_h+5

        wlan0_is_fully_connected = (current_wlan0_connection_status == "NET_CONNECTED")
        host_ssid_main = get_host_connected_ssid()

        if wlan0_is_fully_connected:
            max_len_main=20
            disp_ssid_main = host_ssid_main[:max_len_main] if host_ssid_main and len(host_ssid_main) > max_len_main else host_ssid_main
            draw.text((5,y),f"Net: {disp_ssid_main if disp_ssid_main else 'Connecting...'}",font=font_m,fill=0)
        elif current_wlan0_connection_status == "NET_ASSOCIATED_NO_IP":
            draw.text((5,y),"Net: No IP",font=font_m,fill=0)
        else: # NET_DISCONNECTED
            draw.text((5,y),"Net: Disconnected",font=font_m,fill=0)
        y+=20

        if current_ap_hotspot_status=="AP_ON": clients=get_ap_clients_count_via_api(); draw.text((5,y),f"Clients: {clients}",font=font_m,fill=0)
        else: draw.text((5,y),"Hotspot Off",font=font_m,fill=0)
        y+=20
        if current_ap_hotspot_status!="AP_ON": y+=15

        stats_y_start=UI_EPD_HEIGHT-35; y=max(y,hdr_h+55); y=min(y,stats_y_start) 
        draw.text((5,y),f"CPU Temp: {system_stats_cache['cpu_temp']:.1f}°C",font=font_s,fill=0); y+=15
        geo=system_stats_cache['geo_location']; max_geo=20; geo=geo[:max_geo-3]+"..." if len(geo)>max_geo else geo
        draw.text((5,y),f"GeoIP: {geo}",font=font_s,fill=0)

        draw_button(draw,BTN_X,BTN_AP_Y,BTN_WIDTH,BTN_HEIGHT, "NET ON" if wlan0_is_fully_connected else "NET OFF", sel=not wlan0_is_fully_connected)
        draw_button(draw,BTN_X,BTN_SYS_Y,BTN_WIDTH,BTN_HEIGHT,"SYSTEM")
        draw_button(draw,BTN_X,BTN_INFO_Y,BTN_WIDTH,BTN_HEIGHT,"INFO")
    display_on_epd(epd_instance,ui_image_buffer)

def get_current_display_state():
    update_system_stats() 
    update_wlan0_connection_status() 
    update_ap_hotspot_status()
    
    state={'show_info':show_info_screen,'show_system':show_system_menu,
           'wlan0_conn':current_wlan0_connection_status,'host_ssid':get_host_connected_ssid(),
           'ap_hotspot_stat':current_ap_hotspot_status}
    if current_ap_hotspot_status=="AP_ON":
        state['ap_bssid_w1']=get_ap_broadcast_ssid()
        state['ap_clients_w1']=get_ap_clients_count_via_api()
    
    if not show_system_menu and not show_info_screen: 
        state['cpu_temp']=f"{system_stats_cache['cpu_temp']:.1f}"
        state['geo_loc']=system_stats_cache['geo_location']
    elif show_info_screen:
        state['info_pg']=info_page
        if info_page==0:
             state['wlan0_ip'] = get_interface_ip(HOST_CONNECTED_VIA_INTERFACE) 
        if info_page==1: 
            state['cpu_use']=f"{system_stats_cache['cpu_usage']:.1f}"
            state['mem_use']=f"{system_stats_cache['mem_usage']:.1f}"
            state['uptime']=system_stats_cache['uptime']
            state['geo_loc_info_pg']=system_stats_cache['geo_location'] 
    return state

def have_states_changed(old,new):
    if not old: return True 
    
    old_cp = old.copy(); new_cp = new.copy()
    old_temp = old_cp.pop('cpu_temp', None); new_temp = new_cp.pop('cpu_temp', None)

    if old_cp != new_cp:
        changed_items = [f"{k}:'{old_cp.get(k)}'->'{new_cp.get(k)}'" for k in set(old_cp)|set(new_cp) if old_cp.get(k) != new_cp.get(k)]
        logging.debug(f"Significant state change detected: {', '.join(changed_items)}")
        return True

    if old_temp is not None and new_temp is not None:
        try:
            if abs(float(new_temp) - float(old_temp)) > 1.0: 
                logging.debug(f"CPU Temperature change triggered redraw: {old_temp}°C -> {new_temp}°C")
                return True
        except ValueError: 
            if old_temp != new_temp:
                logging.debug(f"CPU Temperature string representation changed: {old_temp} -> {new_temp}")
                return True
    elif old_temp != new_temp: 
        logging.debug(f"CPU Temperature presence changed: {old_temp} -> {new_temp}")
        return True
        
    return False

def main():
    global epd_instance,touch_instance,ui_image_buffer,current_gt_dev_data,old_gt_dev_data,tp_config_module,last_displayed_state
    global g_ignore_touch_input_temporarily, g_ignore_touch_until_timestamp # Changed global names
    global last_periodic_update_check, periodic_update_interval 

    if not tp_config_module or not hasattr(tp_config_module,'module_init'): 
        logging.critical("TP_lib.epdconfig module or module_init missing."); sys.exit(1)
    logging.info("Initializing hardware via TP_lib.epdconfig.module_init()...")
    try:
        if tp_config_module.module_init()!=0: 
            logging.critical("TP_lib.epdconfig.module_init() failed."); sys.exit(1)
    except Exception as e: logging.critical(f"TP_lib.epdconfig.module_init() exception: {e}"); sys.exit(1)
    
    current_state = None 
    try:
        if EPD_DRIVER_CLASS is None: logging.critical("EPD_DRIVER_CLASS is not defined."); sys.exit(1)
        epd_instance=EPD_DRIVER_CLASS(); 
        logging.info(f"EPD instance created. Driver dimensions: {PHYSICAL_EPD_WIDTH}x{PHYSICAL_EPD_HEIGHT}")
        
        if hasattr(epd_instance,'Init'): epd_instance.Init()
        elif hasattr(epd_instance,'init'): epd_instance.init(EPD_DRIVER_CLASS.FULL_UPDATE) 
        else: logging.critical("EPD instance has no init() or Init() method."); sys.exit(1)
        logging.info("EPD Init/init called.")
        
        if hasattr(epd_instance,'Clear'): epd_instance.Clear(0xFF)
        elif hasattr(epd_instance,'clear'): epd_instance.clear(0xFF)
        else: logging.warning("EPD instance has no Clear() or clear() method.")
        logging.info("EPD Clear/clear called.")
        
        ui_image_buffer=Image.new('1',(UI_EPD_WIDTH,UI_EPD_HEIGHT),255) 
        
        if TOUCH_DRIVER_CLASS and GT_Development_Class:
            try:
                touch_instance=TOUCH_DRIVER_CLASS()
                current_gt_dev_data=GT_Development_Class()
                old_gt_dev_data=GT_Development_Class()
                logging.info("Touch GT1151 driver instance created.")
                if hasattr(touch_instance,'GT_Init'): 
                    touch_instance.GT_Init()
                    logging.info("Touch GT_Init() called.")
            except Exception as e: 
                logging.error(f"Touch driver initialization ERROR: {e}. Touch will be disabled.")
                touch_instance=None
        else: logging.warning("Touch driver class or GT_Development_Class missing. Touch will be disabled.")
        
        display_message("Initializing..."); time.sleep(1) 
        
        logging.info("Performing initial UI draw...")
        current_state = get_current_display_state()
        draw_main_ui_elements(ImageDraw.Draw(ui_image_buffer))
        last_displayed_state = current_state 
        last_periodic_update_check = time.time() 
        
        logging.info("Starting main loop...")
        while True:
            tx,ty=None,None; redraw=False
            
            now_loop = time.time()
            if g_ignore_touch_input_temporarily and now_loop < g_ignore_touch_until_timestamp:
                logging.debug(f"Main loop: Touch ignored until {g_ignore_touch_until_timestamp:.2f} (current: {now_loop:.2f})")
                tx, ty = None, None # Skip getting coordinates
            elif g_ignore_touch_input_temporarily: # Time has passed
                logging.debug("Main loop: Touch ignore period ended.")
                g_ignore_touch_input_temporarily = False # Reset flag

            if tx is None and ty is None and touch_instance and not g_ignore_touch_input_temporarily: # Only get new coords if not ignoring
                 tx,ty=get_touch_coordinates(touch_instance,current_gt_dev_data,old_gt_dev_data)

            action_result = ACTION_NONE
            if tx is not None: # If tx,ty were obtained (i.e., not in ignore period and touch occurred)
                action_result = check_button_press(tx,ty)
            
            if action_result == ACTION_NORMAL_PRESS:
                redraw = True
            elif action_result == ACTION_MENU_CHANGE:
                redraw = True
                g_ignore_touch_input_temporarily = True 
                g_ignore_touch_until_timestamp = time.time() + 1.0 # Ignore touch processing for 1.0 second
                logging.info(f"Main loop: ACTION_MENU_CHANGE. Ignoring touch until {g_ignore_touch_until_timestamp:.2f}")
                if current_gt_dev_data: # Attempt to clear current touch data
                    current_gt_dev_data.Touch = 0
                    current_gt_dev_data.TouchCount = 0
            
            # Fetch current_state for comparison if no button action just fetched it
            if action_result == ACTION_NONE:
                 current_state_candidate = get_current_display_state()
            else: # Action occurred, state might have changed, so current_state should be up-to-date for redraw
                 current_state_candidate = get_current_display_state() # Re-fetch to be sure

            if not redraw and have_states_changed(last_displayed_state, current_state_candidate): 
                logging.info("State change (periodic/no touch) triggered redraw.")
                current_state = current_state_candidate
                redraw = True
            elif redraw and current_state is None: # Ensure current_state is set if redraw is true
                current_state = current_state_candidate
            
            if redraw:
                if current_state is None: # Should not happen if logic above is correct
                     logging.warning("Redraw triggered but current_state is None. Fetching again.")
                     current_state = get_current_display_state()
                draw_main_ui_elements(ImageDraw.Draw(ui_image_buffer))
                last_displayed_state=current_state
            
            time.sleep(0.1)
            
    except IOError as e: logging.error(f"IOError in main execution: {e}")
    except KeyboardInterrupt: logging.info("\nExiting script via KeyboardInterrupt...")
    except Exception as e: logging.critical(f"UNEXPECTED ERROR IN MAIN EXECUTION: {e}"); logging.critical(traceback.format_exc())
    finally:
        logging.info("Cleaning up resources...")
        if epd_instance:
            if hasattr(epd_instance,'sleep'):
                try: 
                    logging.info("Putting EPD to sleep."); 
                    epd_instance.sleep()
                except Exception as e: logging.error(f"Error putting EPD to sleep: {e}")
        
        if hasattr(tp_config_module,'module_exit'):
            try: 
                logging.info("Calling TP_lib.epdconfig.module_exit()..."); 
                tp_config_module.module_exit()
            except Exception as e: logging.error(f"Error in TP_lib.epdconfig.module_exit(): {e}")
        logging.info("Script finished.")

if __name__ == "__main__":
    main()

