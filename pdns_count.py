import psutil
import requests
import json
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# ============ CONFIG ============
API_URL = "http://162.247.153.49/api/metrics"
STATE_FILE = "/tmp/pdns_metrics_state.json"
SERVER_NAME = os.getenv("NAME")
PDNS_LOG_PATH = "/var/log/messages"  # PowerDNS log file
# ================================


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


def parse_syslog_ts_from_line(line, now_local):
    """
    Parse syslog timestamp like 'Nov  6 09:16:35' → UTC epoch seconds.
    """
    try:
        ts_str = line[:15]  # "Nov  6 09:16:35"
        year = now_local.year
        log_dt_naive = datetime.strptime(ts_str, "%b %d %H:%M:%S")
        log_dt = log_dt_naive.replace(year=year)
        local_tz = now_local.tzinfo or datetime.now().astimezone().tzinfo
        log_dt = log_dt.replace(tzinfo=local_tz)
        if log_dt > now_local + timedelta(days=1):
            log_dt = log_dt.replace(year=year - 1)
        return int(log_dt.astimezone(timezone.utc).timestamp())
    except Exception:
        return None


def count_pdns_requests_in_period(log_path, last_ts, now_ts):
    """
    Read /var/log/messages from the bottom up and count PowerDNS requests
    between last_ts and now_ts.
    """
    count = 0
    now_local = datetime.now().astimezone()
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            buffer = b""
            file_size = f.tell()
            block_size = 4096
            pos = file_size

            while pos > 0:
                read_size = min(block_size, pos)
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                buffer = chunk + buffer

                lines = buffer.split(b"\n")
                buffer = lines[0]
                lines = lines[1:]

                for line in reversed(lines):
                    try:
                        line_decoded = line.decode("utf-8", "ignore")
                    except Exception:
                        continue

                    if "pdns_server:" not in line_decoded:
                        continue

                    log_ts = parse_syslog_ts_from_line(line_decoded, now_local)
                    if log_ts is None:
                        continue

                    # skip logs newer than current time
                    if log_ts > now_ts:
                        continue

                    # stop when reaching logs older than last_ts
                    if log_ts <= last_ts:
                        return count

                    # match PowerDNS request line
                    if "pdns_server: Remote" in line_decoded and "wants" in line_decoded:
                        count += 1

            return count
    except FileNotFoundError:
        return 0
    except Exception as e:
        print(f"Error reading {log_path}: {e}")
        return 0


def main():
    now_utc = datetime.now(timezone.utc)
    now_ts = int(now_utc.timestamp())

    cpu_usage = psutil.cpu_percent(interval=1)
    mem_usage = psutil.virtual_memory().percent
    disk_read_mb, disk_write_mb = get_disk_io_mb()

    last = load_state()
    last_disk_read_mb = last.get("disk_read_mb", disk_read_mb)
    last_disk_write_mb = last.get("disk_write_mb", disk_write_mb)
    last_time = last.get("timestamp", now_ts)
    last_log_ts = last.get("last_log_ts", last_time)

    elapsed = max(1, now_ts - last_time)

    disk_read_mb_per_min = (disk_read_mb - last_disk_read_mb) / (elapsed / 60)
    disk_write_mb_per_min = (disk_write_mb - last_disk_write_mb) / (elapsed / 60)

    # --- PowerDNS log counting ---
    request_count = count_pdns_requests_in_period(PDNS_LOG_PATH, last_log_ts, now_ts)
    requests_per_min = request_count / (elapsed / 60) if elapsed > 0 else 0

    # save new state
    save_state({
        "disk_read_mb": disk_read_mb,
        "disk_write_mb": disk_write_mb,
        "timestamp": now_ts,
        "last_log_ts": now_ts,
    })

    data = {
        "server": SERVER_NAME,
        "timestamp": now_ts,
        "cpu_usage": cpu_usage,
        "mem_usage": mem_usage,
        "disk_read_mb": round(disk_read_mb, 2),
        "disk_write_mb": round(disk_write_mb, 2),
        "disk_read_mb_per_min": round(disk_read_mb_per_min, 2),
        "disk_write_mb_per_min": round(disk_write_mb_per_min, 2),
        "nginx_request_count_per_min": round(requests_per_min, 2),
    }

    try:
        res = requests.post(API_URL, json=data, timeout=5)
        print(f"[{now_utc.isoformat()}] Sent metrics: {res.status_code} (pdns_count={request_count})")
    except Exception as e:
        print(f"[{now_utc.isoformat()}] Failed to send metrics: {e} (pdns_count={request_count})")


if __name__ == "__main__":
    main()

