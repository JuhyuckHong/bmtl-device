#!/usr/bin/env python3

import os
import re
import socket
import subprocess
from datetime import datetime
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
    """마지막 촬영 시간 조회 (백업 폴더 우선)"""
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
            return datetime.fromtimestamp(mtime).isoformat()
        return None
    except Exception as e:
        logger.warning(f"Failed to get last capture time: {e}")
        return None

def get_boot_time():
    """부팅 시간 조회"""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
            boot_time = datetime.now().timestamp() - uptime_seconds
            return datetime.fromtimestamp(boot_time).isoformat()
    except Exception as e:
        logger.warning(f"Failed to get boot time: {e}")
        return datetime.now().isoformat()

def get_temperature():
    """온도 정보 조회"""
    try:
        # CPU 온도 조회 시도 (Raspberry Pi의 경우)
        if os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp_millidegree = int(f.read().strip())
                return round(temp_millidegree / 1000.0, 1)

        # Linux systems with lm-sensors
        try:
            result = subprocess.run(['sensors'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                import re
                temps = re.findall(r'(\d+\.\d+)°C', result.stdout)
                if temps:
                    return float(temps[0])
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            logger.warning(f"lm-sensors check failed: {e}")

        return 25.0  # 상온으로 가정
    except Exception as e:
        logger.warning(f"Failed to get temperature: {e}")
        return 25.0

def get_current_sw_version():
    """현재 SW 버전 조회"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd="/opt/bmtl-device/current", # Blue/Green 구조에 맞게 current 디렉토리 참조
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()[:12]  # 앞 12자리만
        # Fallback to VERSION file when git is unavailable
        version_file = os.path.join("/opt/bmtl-device/current", "VERSION")
        if os.path.exists(version_file):
            try:
                with open(version_file, 'r') as f:
                    return f.read().strip()
            except Exception:
                pass
        return "unknown"
    except Exception as e:
        # If git is missing or path invalid, attempt VERSION file fallback
        try:
            version_file = os.path.join("/opt/bmtl-device/current", "VERSION")
            if os.path.exists(version_file):
                with open(version_file, 'r') as f:
                    return f.read().strip()
        except Exception:
            pass
        logger.warning(f"Failed to get SW version: {e}")
        return "unknown"
