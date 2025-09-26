#!/usr/bin/env python3

import os
import sys
import copy
import time
import signal
import logging
import subprocess
import threading
import math
import inotify_simple
from datetime import datetime, timedelta
from shared_config import config_manager, read_camera_config, read_camera_command, read_camera_schedule
import configparser

CAMERA_CONFIG_FILE = 'camera_config.json'
CAMERA_SCHEDULE_FILE = 'camera_schedule.json'
CAMERA_STATS_FILE = 'camera_stats.json'
CAMERA_STATUS_FILE = 'camera_status.json'
CAMERA_RESULT_FILE = 'camera_result.json'

# File watch allowlists
RUNTIME_WATCH_FILES = {
    'camera_config.json',
    'camera_command.json',
    'schedule_settings.json',
    'image_settings.json',
    'camera_settings.json',
    'camera_stats.json',
    'camera_status.json',
    'camera_result.json',
}

PERSISTENT_WATCH_FILES = {
    'camera_schedule.json',
    'camera_default_config.json',
    'device_settings.json',
}

DEFAULT_CAMERA_CONFIG = {
    'iso': 'auto',
    'shutterspeed': '1/60',
    'aperture': 'f/5.6',
    'whitebalance': 'Auto',
}

DEFAULT_CAMERA_SCHEDULE = {
    'enabled': False,
    'type': 'windowed_interval',
    'start_time': '00:00',
    'end_time': '23:59',
    'capture_interval': 60,
    'interval_minutes': 60,
    'last_capture': None,
    'next_capture': None,
    'window_start': None,
    'window_end': None,
}

DEFAULT_CAMERA_STATS = {
    'date': None,
    'total_captures': 0,
    'successful_captures': 0,
    'missed_captures': 0,
    'last_capture_time': None,
    'last_successful_capture': None,
}

DEFAULT_CAMERA_STATUS = {
    'connected': False,
    'current_config': {},
    'photos_taken': 0,
    'storage_path': '',
    'timestamp': None,
}

DEFAULT_CAMERA_RESULT = {
    'success': False,
    'filename': None,
    'filepath': None,
    'error': None,
    'timestamp': None,
}

class CameraController:
    """gphoto2 camera controller with configuration management"""

    def __init__(self):
        self.logger = logging.getLogger('CameraController')
        self.current_config = {}
        # Load storage paths from config
        self.config_path = "/etc/bmtl-device/config.ini"
        cfg = configparser.ConfigParser()
        cfg.read(self.config_path)
        self.upload_path = cfg.get('device', 'upload_path', fallback='/opt/bmtl-device/upload')
        self.backup_path = cfg.get('device', 'backup_path', fallback='/opt/bmtl-device/backup')

        # Ensure directories exist
        os.makedirs(self.upload_path, exist_ok=True)
        os.makedirs(self.backup_path, exist_ok=True)

        # Check if camera is connected
        self.check_camera_connection()

    def check_camera_connection(self):
        """Check if camera is connected via gphoto2"""
        try:
            result = subprocess.run(['gphoto2', '--auto-detect'],
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and 'usb:' in result.stdout:
                self.logger.info("Camera detected successfully")
                return True
            else:
                self.logger.warning("No camera detected")
                return False
        except Exception as e:
            self.logger.error(f"Error checking camera connection: {e}")
            return False

    def apply_config(self, config):
        """Apply camera configuration using gphoto2"""
        try:
            setting_map = {
                'iso': '/main/imgsettings/iso',
                'aperture': '/main/capturesettings/exposurecompensation',
                'shutterspeed': '/main/capturesettings/shutterspeed',
                'whitebalance': '/main/imgsettings/whitebalance',
                'imagequality': '/main/capturesettings/imagequality',
                'focusmode2': '/main/capturesettings/focusmode2',
            }
            alias_map = {
                'shutter_speed': 'shutterspeed',
                'image_quality': 'imagequality',
                'focus_mode': 'focusmode2',
            }
            applied_config = {}
            for setting, value in (config or {}).items():
                canonical_key = alias_map.get(setting, setting)
                gphoto_key = setting_map.get(canonical_key)
                if not gphoto_key:
                    self.logger.debug("Skipping unsupported gphoto2 setting: %s", setting)
                    continue

                cmd = ['gphoto2', '--set-config', f'{gphoto_key}={value}']
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                if result.returncode == 0:
                    applied_config[canonical_key] = value
                    self.logger.info("Applied %s=%s", canonical_key, value)
                else:
                    self.logger.error("Failed to set %s=%s: %s", canonical_key, value, result.stderr)

            if applied_config:
                self.current_config.update(applied_config)
                self.logger.info("Camera configuration applied: %s", applied_config)
            else:
                self.logger.info("No supported camera settings found in: %s", config)

        except Exception as e:
            self.logger.error("Error applying camera config: %s", e)

    def capture_photo(self, filename=None):
        """Capture a photo using gphoto2"""
        try:
            if not filename:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"photo_{timestamp}.jpg"

            # Save to upload folder first for Dropbox sync
            filepath = os.path.join(self.upload_path, filename)

            # Capture photo
            cmd = ['gphoto2', '--capture-image-and-download', '--filename', filepath]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            capture_result = {
                'success': result.returncode == 0,
                'filename': filename if result.returncode == 0 else None,
                'filepath': filepath if result.returncode == 0 else None,
                'timestamp': datetime.now().isoformat(),
                'config': self.current_config.copy()
            }

            if result.returncode == 0:
                self.logger.info(f"Photo captured successfully to upload folder: {filename}")
                # Update capture statistics
                self.update_capture_stats(True)
            else:
                self.logger.error(f"Photo capture failed: {result.stderr}")
                capture_result['error'] = result.stderr
                # Update missed capture count
                self.update_capture_stats(False)

            return capture_result

        except Exception as e:
            self.logger.error(f"Error capturing photo: {e}")
            # Update missed capture count
            self.update_capture_stats(False)
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }

    def update_capture_stats(self, success):
        """Update capture statistics"""
        try:
            from shared_config import config_manager

            # Read current stats
            stats = config_manager.read_config('camera_stats.json')
            today = datetime.now().strftime('%Y-%m-%d')

            # Initialize stats if empty or new day
            if not stats or stats.get('date') != today:
                stats = {
                    'date': today,
                    'total_captures': 0,
                    'successful_captures': 0,
                    'missed_captures': 0,
                    'last_capture_time': None,
                    'last_successful_capture': None
                }

            # Update stats
            stats['total_captures'] += 1
            stats['last_capture_time'] = datetime.now().isoformat()

            if success:
                stats['successful_captures'] += 1
                stats['last_successful_capture'] = datetime.now().isoformat()
            else:
                stats['missed_captures'] += 1

            # Save updated stats
            config_manager.write_config('camera_stats.json', stats)

        except Exception as e:
            self.logger.error(f"Error updating capture stats: {e}")

    def get_capture_stats(self):
        """Get current capture statistics"""
        try:
            from shared_config import config_manager
            stats = config_manager.read_config('camera_stats.json')
            today = datetime.now().strftime('%Y-%m-%d')

            # Return today's stats or defaults
            if stats and stats.get('date') == today:
                return stats
            else:
                return {
                    'date': today,
                    'total_captures': 0,
                    'successful_captures': 0,
                    'missed_captures': 0,
                    'last_capture_time': None,
                    'last_successful_capture': None
                }
        except Exception as e:
            self.logger.error(f"Error getting capture stats: {e}")
            return {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'total_captures': 0,
                'successful_captures': 0,
                'missed_captures': 0,
                'last_capture_time': None,
                'last_successful_capture': None
            }

    def get_camera_status(self):
        """Get current camera status and configuration"""
        try:
            # Get basic camera info
            result = subprocess.run(['gphoto2', '--summary'],
                                  capture_output=True, text=True, timeout=10)

            return {
                'connected': result.returncode == 0,
                'current_config': self.current_config,
                # Count photos that have been backed up (post-upload)
                'photos_taken': len([f for f in os.listdir(self.backup_path)
                                   if f.lower().endswith(('.jpg', '.jpeg', '.raw'))]),
                'storage_path': self.backup_path,
                'timestamp': datetime.now().isoformat()
            }

        except Exception as e:
            self.logger.error(f"Error getting camera status: {e}")
            return {
                'connected': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }


class BMTLCameraDaemon:
    """BMTL Camera Control Daemon with file-based configuration"""

    def __init__(self):
        # Use shared runtime config directory from shared_config
        self.config_path = getattr(config_manager, 'base_path', '/opt/bmtl-device/tmp')
        self.log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        self.running = True
        self.camera = CameraController()
        self.last_command_processed = None
        self.schedule_thread = None
        self.upload_mover_thread = None
        self.current_schedule = copy.deepcopy(DEFAULT_CAMERA_SCHEDULE)
        # Debounce settings for noisy file change events
        self._event_debounce = {}
        self._debounce_sec = 0.5

        self.setup_logging()

        # Ensure config directory exists (create early to avoid systemd issues)
        try:
            os.makedirs(self.config_path, exist_ok=True)
            self.logger.info(f"Config directory created/verified: {self.config_path}")
        except Exception as e:
            self.logger.error(f"Failed to create config directory: {e}")
            sys.exit(1)

        self.ensure_default_configs()

        # Load persisted schedule state if available
        try:
            persisted_schedule = read_camera_schedule()
            if persisted_schedule:
                self.current_schedule = persisted_schedule
        except Exception as err:
            self.logger.warning(f"Failed to load existing camera schedule: {err}")

        # Apply saved schedule settings to derive an active plan
        try:
            existing_settings = config_manager.read_config('schedule_settings.json')
            if existing_settings:
                self.update_schedule(existing_settings)
        except Exception as err:
            self.logger.warning(f"Failed to apply saved schedule settings: {err}")

        # Brief current configuration on startup
        self.log_startup_briefing()

        self.logger.info("BMTL Camera Daemon initialized")

    def setup_logging(self):
        os.makedirs(self.log_dir, exist_ok=True)
        log_file = os.path.join(self.log_dir, "camera_daemon.log")

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger('BMTLCameraDaemon')

    def ensure_default_configs(self):
        """Seed default camera config/state files if they do not exist."""
        defaults = (
            (CAMERA_CONFIG_FILE, DEFAULT_CAMERA_CONFIG),
            (CAMERA_SCHEDULE_FILE, DEFAULT_CAMERA_SCHEDULE),
            (CAMERA_STATS_FILE, DEFAULT_CAMERA_STATS),
            (CAMERA_STATUS_FILE, DEFAULT_CAMERA_STATUS),
            (CAMERA_RESULT_FILE, DEFAULT_CAMERA_RESULT),
        )

        for filename, template in defaults:
            if config_manager.config_exists(filename):
                continue

            try:
                config_manager.write_config(filename, copy.deepcopy(template))
                self.logger.info(f"Seeded default camera config file: {filename}")
            except Exception as err:
                self.logger.warning(f"Failed to seed default config {filename}: {err}")

    def log_startup_briefing(self):
        """Log current camera configuration and schedule settings on startup"""
        try:
            self.logger.info("=" * 80)
            self.logger.info("📸 BMTL CAMERA DAEMON STARTUP BRIEFING")
            self.logger.info("=" * 80)

            # Camera configuration briefing
            try:
                camera_config = read_camera_config() or DEFAULT_CAMERA_CONFIG
                self.logger.info("📷 Camera Configuration:")
                for key, value in camera_config.items():
                    self.logger.info(f"   {key.upper()}: {value}")
            except Exception as e:
                self.logger.warning(f"   Failed to read camera config: {e}")

            self.logger.info("-" * 40)

            # Schedule briefing
            schedule = self.current_schedule
            if schedule.get('enabled', False):
                self.logger.info("⏰ Schedule Configuration: ENABLED")
                schedule_type = schedule.get('type', 'unknown')
                self.logger.info(f"   Type: {schedule_type}")

                if schedule_type == 'windowed_interval':
                    start_time = schedule.get('start_time', 'N/A')
                    end_time = schedule.get('end_time', 'N/A')
                    interval = schedule.get('capture_interval', 'N/A')
                    interval_minutes = schedule.get('interval_minutes', 'N/A')

                    self.logger.info(f"   Operating Hours: {start_time} - {end_time}")
                    self.logger.info(f"   Capture Interval: {interval} seconds")
                    self.logger.info(f"   Window Interval: {interval_minutes} minutes")

                    # Calculate next capture window
                    next_capture = schedule.get('next_capture')
                    window_start = schedule.get('window_start')
                    window_end = schedule.get('window_end')

                    if next_capture:
                        self.logger.info(f"   Next Capture: {next_capture}")
                    if window_start and window_end:
                        self.logger.info(f"   Current Window: {window_start} - {window_end}")

                elif schedule_type == 'continuous':
                    interval = schedule.get('capture_interval', 'N/A')
                    self.logger.info(f"   Continuous Mode - Interval: {interval} seconds")

                last_capture = schedule.get('last_capture')
                if last_capture:
                    self.logger.info(f"   Last Capture: {last_capture}")

            else:
                self.logger.info("⏰ Schedule Configuration: DISABLED")
                self.logger.info("   Manual capture mode only")

            self.logger.info("-" * 40)

            # Storage paths
            self.logger.info("📁 Storage Configuration:")
            self.logger.info(f"   Upload Path: {self.camera.upload_path}")
            self.logger.info(f"   Backup Path: {self.camera.backup_path}")
            self.logger.info(f"   Config Path: {self.config_path}")

            self.logger.info("-" * 40)

            # Camera connection status
            camera_connected = self.camera.check_camera_connection()
            status_icon = "✅" if camera_connected else "❌"
            self.logger.info(f"🔌 Camera Connection: {status_icon} {'Connected' if camera_connected else 'Not Connected'}")

            # Statistics
            try:
                stats = config_manager.read_config(CAMERA_STATS_FILE) or DEFAULT_CAMERA_STATS
                if stats.get('total_captures', 0) > 0:
                    self.logger.info("📊 Recent Statistics:")
                    self.logger.info(f"   Total Captures: {stats.get('total_captures', 0)}")
                    self.logger.info(f"   Successful: {stats.get('successful_captures', 0)}")
                    self.logger.info(f"   Missed: {stats.get('missed_captures', 0)}")
                    last_success = stats.get('last_successful_capture')
                    if last_success:
                        self.logger.info(f"   Last Successful: {last_success}")
            except Exception as e:
                self.logger.warning(f"   Failed to read statistics: {e}")

            self.logger.info("=" * 80)

        except Exception as e:
            self.logger.error(f"Error during startup briefing: {e}")

    def watch_config_files(self):
        """Watch for configuration file changes using inotify with filtering and debounce."""
        inotify = inotify_simple.INotify()
        # Expand events to catch atomic writes and file creation reliably
        watch_flags = (
            inotify_simple.flags.MODIFY |
            inotify_simple.flags.MOVED_TO |
            getattr(inotify_simple.flags, 'CLOSE_WRITE', inotify_simple.flags.MODIFY) |
            getattr(inotify_simple.flags, 'CREATE', inotify_simple.flags.MOVED_TO)
        )

        # Watch the runtime and persistent config directories
        persistent_dir = getattr(config_manager, 'persistent_path', '/etc/bmtl-device')
        watch_map = {}

        try:
            wd_runtime = inotify.add_watch(self.config_path, watch_flags)
            watch_map[wd_runtime] = self.config_path
            self.logger.info(f"Watching config directory: {self.config_path}")
        except Exception as e:
            self.logger.warning(f"Failed to watch runtime config directory {self.config_path}: {e}")

        if persistent_dir != self.config_path:
            try:
                wd_persist = inotify.add_watch(persistent_dir, watch_flags)
                watch_map[wd_persist] = persistent_dir
                self.logger.info(f"Watching persistent config directory: {persistent_dir}")
            except Exception as e:
                self.logger.warning(f"Failed to watch persistent config directory {persistent_dir}: {e}")

        try:
            while self.running:
                # Check for file changes with timeout
                events = inotify.read(timeout=1000)  # 1 second timeout

                for event in events:
                    if not event.name:
                        continue

                    directory = watch_map.get(event.wd, '?')
                    abs_path = os.path.join(directory, event.name)

                    # Per-directory allowlist
                    filename = event.name
                    if directory == self.config_path:
                        allowed = filename in RUNTIME_WATCH_FILES
                    elif directory == persistent_dir:
                        allowed = filename in PERSISTENT_WATCH_FILES
                    else:
                        allowed = False

                    if not allowed:
                        self.logger.debug(f"Ignoring change outside allowlist: {abs_path}")
                        continue

                    # Debounce rapidly repeated events
                    now = time.time()
                    last = self._event_debounce.get(abs_path, 0)
                    if (now - last) < self._debounce_sec:
                        self.logger.debug(f"Debounced duplicate event: {abs_path}")
                        continue
                    self._event_debounce[abs_path] = now

                    self.logger.info(
                        f"Config file change detected: {abs_path} (flags: {event.mask})"
                    )
                    self.handle_config_change(filename)

        except Exception as e:
            self.logger.error(f"Error in config file watcher: {e}")
        finally:
            inotify.close()

    def handle_config_change(self, filename):
        """Handle configuration file changes"""
        try:
            self.logger.info(f"Processing config change for: {filename}")
            if filename == 'camera_config.json':
                config = read_camera_config()
                if config:
                    self.logger.info(f"Camera config updated: {config}")
                    self.camera.apply_config(config)

            elif filename == 'camera_command.json':
                command = read_camera_command()
                if command and command != self.last_command_processed:
                    self.logger.info(f"Camera command received: {command}")
                    self.process_camera_command(command)
                    self.last_command_processed = command

            elif filename == 'schedule_settings.json':
                settings = config_manager.read_config('schedule_settings.json')
                if settings is not None:
                    self.logger.info(f"Schedule settings updated: {settings}")
                    self.update_schedule(settings)

            elif filename == 'camera_schedule.json':
                schedule = read_camera_schedule()
                if schedule:
                    self.logger.info(f"Camera schedule updated: {schedule}")
                    self.current_schedule = schedule

            elif filename == 'image_settings.json':
                image_settings = config_manager.read_config('image_settings.json')
                if image_settings:
                    self.logger.info(f"Image settings updated: {image_settings}")
                    self.camera.apply_config(image_settings)
                else:
                    self.logger.warning(f"Failed to read image_settings.json or file is empty")

            elif filename == 'camera_settings.json':
                camera_settings = config_manager.read_config('camera_settings.json')
                if camera_settings:
                    self.logger.info(f"Camera settings updated: {camera_settings}")
                    self.camera.apply_config(camera_settings)
                else:
                    self.logger.warning(f"Failed to read camera_settings.json or file is empty")

            else:
                self.logger.debug(f"Ignoring config file change: {filename}")

        except Exception as e:
            self.logger.error(f"Error handling config change for {filename}: {e}")

    def process_camera_command(self, command):
        """Process immediate camera commands"""
        try:
            cmd_type = command.get('type', '')

            if cmd_type == 'capture':
                filename = command.get('filename')
                result = self.camera.capture_photo(filename)
                self.logger.info(f"Capture command result: {result}")

                # Save result for MQTT daemon to read
                config_manager.write_config('camera_result.json', result)

            elif cmd_type == 'status':
                status = self.camera.get_camera_status()
                config_manager.write_config('camera_status.json', status)

            elif cmd_type == 'config':
                new_config = command.get('config', {})
                self.camera.apply_config(new_config)

            else:
                self.logger.warning(f"Unknown camera command type: {cmd_type}")

        except Exception as e:
            self.logger.error(f"Error processing camera command: {e}")

    def update_schedule(self, schedule_settings, current_time=None):
        """Normalize schedule settings and persist the derived capture plan."""
        try:
            schedule_settings = schedule_settings or {}
            now = current_time or datetime.now()

            existing_schedule = read_camera_schedule() or {}
            last_capture = existing_schedule.get('last_capture')
            if last_capture:
                try:
                    datetime.fromisoformat(last_capture)
                except ValueError:
                    self.logger.warning(f"Invalid last_capture timestamp '{last_capture}', resetting to None")
                    last_capture = None

            start_fallback = self._normalize_time_field(existing_schedule.get('start_time'), '00:00')
            end_fallback = self._normalize_time_field(existing_schedule.get('end_time'), '23:59')

            start_time = self._normalize_time_field(schedule_settings.get('start_time'), start_fallback)
            end_time = self._normalize_time_field(schedule_settings.get('end_time'), end_fallback)

            existing_interval = self._normalize_interval(
                existing_schedule.get('capture_interval', existing_schedule.get('interval_minutes')),
                60,
            )
            interval = self._normalize_interval(schedule_settings.get('capture_interval'), existing_interval)

            if 'enabled' in schedule_settings:
                enabled_flag = schedule_settings.get('enabled')
            elif schedule_settings:
                enabled_flag = True
            else:
                enabled_flag = existing_schedule.get('enabled', False)

            schedule_plan = {
                'enabled': bool(enabled_flag) and interval > 0,
                'type': 'windowed_interval',
                'start_time': start_time,
                'end_time': end_time,
                'capture_interval': interval,
                'interval_minutes': interval,
                'last_capture': last_capture,
            }

            normalized_state, _ = self._normalize_schedule_state(schedule_plan, now)
            schedule_plan.update(normalized_state)

            config_manager.write_config('camera_schedule.json', schedule_plan)
            self.current_schedule = schedule_plan

            self.logger.info(
                "Schedule normalized: start=%s end=%s interval=%s next=%s",
                start_time,
                end_time,
                interval,
                schedule_plan.get('next_capture'),
            )

        except Exception as e:
            self.logger.error(f"Failed to update schedule: {e}")

    def _normalize_time_field(self, value, fallback):
        """Return a HH:MM string, falling back when parsing fails."""
        def _try_parse(candidate):
            if candidate in (None, ''):
                return None
            candidate_str = str(candidate).strip()
            try:
                parsed = datetime.strptime(candidate_str, '%H:%M')
                return parsed.strftime('%H:%M')
            except ValueError:
                return None

        normalized = _try_parse(value)
        if normalized is not None:
            return normalized

        fallback_normalized = _try_parse(fallback)
        if fallback_normalized is not None:
            if value not in (None, '') and str(value).strip() != fallback_normalized:
                self.logger.warning(
                    f"Invalid time value '{value}', using fallback '{fallback_normalized}'"
                )
            return fallback_normalized

        return '00:00'

    def _normalize_interval(self, value, fallback):
        """Convert the interval to a non-negative integer number of minutes."""
        candidates = [value, fallback, 0]
        for candidate in candidates:
            if candidate in (None, ''):
                continue
            try:
                interval = int(candidate)
                return max(interval, 0)
            except (TypeError, ValueError):
                continue
        return 0

    def _calculate_window(self, reference, start_str, end_str):
        """Return datetime bounds for the capture window closest to reference."""
        start_hour, start_minute = map(int, start_str.split(':'))
        end_hour, end_minute = map(int, end_str.split(':'))

        start_today = reference.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        end_today = reference.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)

        if end_today > start_today:
            if reference < start_today:
                return start_today, end_today, False
            if reference <= end_today:
                return start_today, end_today, True
            next_start = start_today + timedelta(days=1)
            next_end = end_today + timedelta(days=1)
            return next_start, next_end, False

        if reference >= start_today:
            return start_today, end_today + timedelta(days=1), True
        if reference <= end_today:
            return start_today - timedelta(days=1), end_today, True

        next_start = start_today
        next_end = end_today + timedelta(days=1)
        return next_start, next_end, False

    def _align_to_interval(self, base, reference, interval_td):
        """Return the first capture time >= reference aligned to the interval."""
        if interval_td.total_seconds() <= 0:
            return base
        if reference <= base:
            return base

        delta_seconds = (reference - base).total_seconds()
        intervals = math.ceil(delta_seconds / interval_td.total_seconds())
        return base + (interval_td * intervals)

    def _advance_window(self, start_dt, end_dt):
        """Advance window bounds by one day preserving duration."""
        window_length = end_dt - start_dt
        if window_length <= timedelta(0):
            window_length = timedelta(days=1)
        next_start = start_dt + timedelta(days=1)
        next_end = next_start + window_length
        return next_start, next_end

    def _normalize_schedule_state(self, schedule, now):
        """Return normalized schedule fields and whether a capture is due."""
        normalized = {}

        start_time = self._normalize_time_field(schedule.get('start_time'), '00:00')
        end_time = self._normalize_time_field(schedule.get('end_time'), '23:59')
        interval = self._normalize_interval(
            schedule.get('capture_interval', schedule.get('interval_minutes')),
            0,
        )

        enabled = bool(schedule.get('enabled', True)) and interval > 0

        normalized.update({
            'type': 'windowed_interval',
            'start_time': start_time,
            'end_time': end_time,
            'capture_interval': interval,
            'interval_minutes': interval,
            'enabled': enabled,
        })

        if not enabled:
            normalized.update({
                'window_start': None,
                'window_end': None,
                'next_capture': None,
            })
            return normalized, False

        window_start, window_end, _ = self._calculate_window(now, start_time, end_time)
        normalized['window_start'] = window_start.isoformat()
        normalized['window_end'] = window_end.isoformat()

        interval_td = timedelta(minutes=interval)

        last_capture_str = schedule.get('last_capture')
        last_capture_dt = None
        if last_capture_str:
            try:
                last_capture_dt = datetime.fromisoformat(last_capture_str)
            except ValueError:
                self.logger.warning(f"Invalid last_capture timestamp '{last_capture_str}', resetting to None")
                normalized['last_capture'] = None

        candidate_time = max(now, window_start)
        if last_capture_dt:
            candidate_time = max(candidate_time, last_capture_dt + interval_td)

        next_capture = self._align_to_interval(window_start, candidate_time, interval_td)
        if next_capture is None or next_capture > window_end:
            window_start, window_end = self._advance_window(window_start, window_end)
            normalized['window_start'] = window_start.isoformat()
            normalized['window_end'] = window_end.isoformat()
            next_capture = window_start
            capture_due = False
        else:
            capture_due = window_start <= next_capture <= window_end and now >= next_capture

        normalized['next_capture'] = next_capture.isoformat()

        return normalized, capture_due

    def run_scheduled_tasks(self):
        """Run scheduled camera tasks in separate thread."""
        while self.running:
            try:
                schedule = read_camera_schedule()
                if schedule:
                    self.current_schedule = schedule
                else:
                    schedule = self.current_schedule

                if schedule:
                    self.check_and_execute_schedule(schedule)

                time.sleep(60)  # Check every minute

            except Exception as e:
                self.logger.error(f"Error in scheduled tasks: {e}")
                time.sleep(60)

    def check_and_execute_schedule(self, schedule, current_time=None):
        """Evaluate the schedule and trigger captures when due."""
        try:
            if not schedule:
                return

            now = current_time or datetime.now()
            original_schedule = copy.deepcopy(schedule)
            working_schedule = copy.deepcopy(schedule)

            normalized_state, capture_due = self._normalize_schedule_state(working_schedule, now)
            working_schedule.update(normalized_state)

            schedule_changed = working_schedule != original_schedule

            if capture_due:
                result, capture_time = self.execute_scheduled_capture(planned_time=now)
                working_schedule['last_capture'] = capture_time.isoformat()

                post_state, _ = self._normalize_schedule_state(working_schedule, capture_time)
                working_schedule.update(post_state)
                schedule_changed = True

            self.current_schedule = working_schedule

            if schedule_changed:
                config_manager.write_config('camera_schedule.json', working_schedule)

        except Exception as e:
            self.logger.error(f"Error checking schedule: {e}")

    def execute_scheduled_capture(self, planned_time=None):
        """Execute a scheduled capture and persist the result."""
        capture_time = planned_time or datetime.now()
        try:
            result = self.camera.capture_photo()
            config_manager.write_config('camera_result.json', result)
            self.logger.info(f"Scheduled capture completed: {result}")
            return result, capture_time

        except Exception as e:
            self.logger.error(f"Error in scheduled capture: {e}")
            failure_result = {
                'success': False,
                'error': str(e),
                'timestamp': datetime.now().isoformat(),
            }
            config_manager.write_config('camera_result.json', failure_result)
            self.camera.update_capture_stats(False)
            return failure_result, capture_time

    def signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

    def run(self):
        """Main daemon loop"""
        # Set up signal handlers
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)

        try:
            # Start scheduled tasks thread
            self.schedule_thread = threading.Thread(target=self.run_scheduled_tasks, daemon=True)
            self.schedule_thread.start()

            # Start upload → backup mover thread
            self.upload_mover_thread = threading.Thread(target=self.move_uploaded_files_loop, daemon=True)
            self.upload_mover_thread.start()

            self.logger.info("Camera daemon started")

            # Main loop - watch for config changes
            self.watch_config_files()

        except Exception as e:
            self.logger.error(f"Error in main loop: {e}")
        finally:
            self.logger.info("Camera daemon stopped")

    # -------------------------
    # Upload-to-backup movement
    # -------------------------
    def _file_size_stable(self, path, window_sec=30):
        """Return True if file size is stable for window_sec seconds."""
        try:
            size1 = os.path.getsize(path)
            time.sleep(window_sec)
            size2 = os.path.getsize(path)
            return size1 == size2
        except FileNotFoundError:
            return False
        except Exception as e:
            self.logger.warning(f"Size check failed for {path}: {e}")
            return False

    def move_uploaded_files_loop(self):
        """Periodically move files from upload to backup after they settle (assumed uploaded)."""
        # Read paths again (in case of config changes at runtime)
        cfg = configparser.ConfigParser()
        cfg.read(self.camera.config_path)
        upload_dir = getattr(self.camera, 'upload_path', '/opt/bmtl-device/upload')
        backup_dir = getattr(self.camera, 'backup_path', '/opt/bmtl-device/backup')

        settle_seconds = 30
        scan_interval = 20

        self.logger.info(f"Starting upload mover: {upload_dir} -> {backup_dir}")
        while self.running:
            try:
                if not os.path.exists(upload_dir):
                    os.makedirs(upload_dir, exist_ok=True)
                if not os.path.exists(backup_dir):
                    os.makedirs(backup_dir, exist_ok=True)

                for name in os.listdir(upload_dir):
                    src = os.path.join(upload_dir, name)
                    if not os.path.isfile(src):
                        continue
                    if not name.lower().endswith(('.jpg', '.jpeg', '.raw')):
                        continue

                    # Only move if file appears settled
                    mtime_age = time.time() - os.path.getmtime(src)
                    if mtime_age < settle_seconds:
                        continue
                    if not self._file_size_stable(src, window_sec=settle_seconds):
                        continue

                    dst = os.path.join(backup_dir, name)
                    try:
                        os.replace(src, dst)
                        self.logger.info(f"Moved uploaded file to backup: {name}")
                    except Exception as move_err:
                        self.logger.warning(f"Failed to move {src} -> {dst}: {move_err}")

            except Exception as e:
                self.logger.error(f"Error in upload mover loop: {e}")
            finally:
                time.sleep(scan_interval)


if __name__ == "__main__":
    daemon = BMTLCameraDaemon()
    daemon.run()
