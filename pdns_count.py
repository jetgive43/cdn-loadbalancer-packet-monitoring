import psutil
import time
import requests
import socket
import json
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()
# ============ CONFIG ============
API_URL = "http://162.247.153.49/api/metrics"
INTERFACE = os.getenv("INTERFACE")  # Adjust this for your PowerDNS server
STATE_FILE = "/tmp/pdns_metrics_state.json"
SERVER_NAME = os.getenv("NAME")
# ================================

def read_rx_packets(interface):
    path = f"/sys/class/net/{interface}/statistics/rx_packets"
    try:
        with open(path, "r") as f:
            return int(f.read().strip())
    except FileNotFoundError:
        return 0

def get_disk_io_mb():
    io = psutil.disk_io_counters()
    return io.read_bytes / (1024 * 1024), io.write_bytes / (1024 * 1024)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def main():
    now_utc = datetime.now(timezone.utc)
    now_ts = int(now_utc.timestamp())

    cpu_usage = psutil.cpu_percent(interval=1)
    mem_usage = psutil.virtual_memory().percent
    disk_read_mb, disk_write_mb = get_disk_io_mb()
    rx_packets = read_rx_packets(INTERFACE)

    last = load_state()
    last_rx_packets = last.get("rx_packets", rx_packets)
    last_disk_read_mb = last.get("disk_read_mb", disk_read_mb)
    last_disk_write_mb = last.get("disk_write_mb", disk_write_mb)
    last_time = last.get("timestamp", now_ts)

    elapsed = max(1, now_ts - last_time)

    packet_count_per_min = (rx_packets - last_rx_packets) / (elapsed / 60)
    disk_read_mb_per_min = (disk_read_mb - last_disk_read_mb) / (elapsed / 60)
    disk_write_mb_per_min = (disk_write_mb - last_disk_write_mb) / (elapsed / 60)

    save_state({
        "rx_packets": rx_packets,
        "disk_read_mb": disk_read_mb,
        "disk_write_mb": disk_write_mb,
        "timestamp": now_ts,
    })

    data = {
        "server": SERVER_NAME,
        "timestamp": now_ts,  # UTC timestamp
        "cpu_usage": cpu_usage,
        "mem_usage": mem_usage,
        "disk_read_mb": round(disk_read_mb, 2),
        "disk_write_mb": round(disk_write_mb, 2),
        "disk_read_mb_per_min": round(disk_read_mb_per_min, 2),
        "disk_write_mb_per_min": round(disk_write_mb_per_min, 2),
        "nginx_request_count_per_min": round(packet_count_per_min, 2),  # same key name for backend
    }

    try:
        res = requests.post(API_URL, json=data, timeout=5)
        print(f"[{now_utc.isoformat()}] Sent metrics: {res.status_code}")
    except Exception as e:
        print(f"[{now_utc.isoformat()}] Failed to send metrics: {e}")

if __name__ == "__main__":
    main()

