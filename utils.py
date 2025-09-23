#!/usr/bin/env python3

import os
import re
import socket
import subprocess
from datetime import datetime, timezone
import logging
import configparser

logger = logging.getLogger(__name__)

def extract_device_id_from_hostname():
    """Extract device ID from hostname (e.g., bmotion01 -> 01)"""
    try:
        hostname = socket.gethostname()
        match = re.search(r'bmotion(\d+)', hostname.lower())
        if match:
            device_num = match.group(1).zfill(2)  # Ensure 2-digit format
            logger.info(f"Extracted device ID '{device_num}' from hostname '{hostname}'")
            return device_num
        else:
            logger.warning(f"Could not extract device ID from hostname '{hostname}', using default '01'")
            return "01"
    except Exception as e:
        logger.error(f"Error extracting device ID from hostname: {e}, using default '01'")
        return "01"

def get_last_capture_time():
    """Return the most recent capture time as a UTC ISO 8601 string."""
    try:
        # Prefer backup folder (post-upload), fall back to legacy photos
        cfg = configparser.ConfigParser()
        cfg.read('/etc/bmtl-device/config.ini')
        backup_path = cfg.get('device', 'backup_path', fallback='/opt/bmtl-device/backup')
        legacy_photos = cfg.get('device', 'photo_storage_path', fallback='/opt/bmtl-device/photos')
        search_paths = [backup_path, legacy_photos]

        for storage_path in search_paths:
            if not os.path.exists(storage_path):
                continue
            files = [f for f in os.listdir(storage_path) if f.lower().endswith('.jpg')]
            if not files:
                continue
            latest_file = max(files, key=lambda f: os.path.getmtime(os.path.join(storage_path, f)))
            mtime = os.path.getmtime(os.path.join(storage_path, latest_file))
            return datetime.fromtimestamp(mtime, timezone.utc).isoformat()
        return None
    except Exception as exc:
        logger.warning(f"Failed to get last capture time: {exc}")
        return None


def get_boot_time():
    """Return the last boot time as a UTC ISO 8601 string."""
    try:
        with open('/proc/uptime', 'r') as uptime_file:
            uptime_seconds = float(uptime_file.readline().split()[0])
            boot_timestamp = datetime.now(timezone.utc).timestamp() - uptime_seconds
            return datetime.fromtimestamp(boot_timestamp, timezone.utc).isoformat()
    except Exception as exc:
        logger.warning(f"Failed to get boot time: {exc}")
        return datetime.now(timezone.utc).isoformat()


def get_temperature():
    """Return device temperature in Celsius."""
    try:
        # Try CPU temperature sensor (e.g., Raspberry Pi)
        if os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as temp_file:
                temp_millidegree = int(temp_file.read().strip())
                return round(temp_millidegree / 1000.0, 1)

        # Fallback to lm-sensors output if available
        try:
            result = subprocess.run(['sensors'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                import re
                temps = re.findall(r'(\d+\.\d+).*C', result.stdout)
                if temps:
                    return float(temps[0])
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as exc:
            logger.warning(f"lm-sensors check failed: {exc}")

        return 25.0  # Default fallback temperature
    except Exception as exc:
        logger.warning(f"Failed to get temperature: {exc}")
        return 25.0


def get_current_sw_version():
    """Return the current software version identifier."""
    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd='/opt/bmtl-device/current',  # Resolve against the active Blue/Green slot
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
        version_file = os.path.join('/opt/bmtl-device/current', 'VERSION')
        if os.path.exists(version_file):
            try:
                with open(version_file, 'r') as version_handle:
                    return version_handle.read().strip()
            except Exception:
                pass
        return 'unknown'
    except Exception as exc:
        try:
            version_file = os.path.join('/opt/bmtl-device/current', 'VERSION')
            if os.path.exists(version_file):
                with open(version_file, 'r') as version_handle:
                    return version_handle.read().strip()
        except Exception:
            pass
        logger.warning(f"Failed to get SW version: {exc}")
        return 'unknown'



