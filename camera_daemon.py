#!/usr/bin/env python3

import os
import sys
import json
import time
import signal
import logging
import subprocess
import threading
import inotify_simple
from datetime import datetime, timedelta
from pathlib import Path
from shared_config import config_manager, read_camera_config, read_camera_command, read_camera_schedule

class CameraController:
    """gphoto2 camera controller with configuration management"""

    def __init__(self):
        self.logger = logging.getLogger('CameraController')
        self.current_config = {}
        self.photo_storage_path = '/opt/bmtl-device/photos'

        # Create photos directory
        os.makedirs(self.photo_storage_path, exist_ok=True)

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
            for setting, value in config.items():
                if setting in ['iso', 'shutterspeed', 'aperture', 'whitebalance']:
                    cmd = ['gphoto2', f'--set-config', f'{setting}={value}']
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                    if result.returncode == 0:
                        self.logger.info(f"Applied {setting}={value}")
                    else:
                        self.logger.error(f"Failed to set {setting}={value}: {result.stderr}")

            self.current_config.update(config)
            self.logger.info(f"Camera configuration applied: {config}")

        except Exception as e:
            self.logger.error(f"Error applying camera config: {e}")

    def capture_photo(self, filename=None):
        """Capture a photo using gphoto2"""
        try:
            if not filename:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"photo_{timestamp}.jpg"

            filepath = os.path.join(self.photo_storage_path, filename)

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
                self.logger.info(f"Photo captured successfully: {filename}")
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
                'photos_taken': len([f for f in os.listdir(self.photo_storage_path)
                                   if f.endswith(('.jpg', '.jpeg', '.raw'))]),
                'storage_path': self.photo_storage_path,
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
        self.config_path = "/tmp/bmtl-config"
        self.log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        self.running = True
        self.camera = CameraController()
        self.last_command_processed = None
        self.schedule_thread = None

        self.setup_logging()

        # Ensure config directory exists (create early to avoid systemd issues)
        try:
            os.makedirs(self.config_path, exist_ok=True)
            self.logger.info(f"Config directory created/verified: {self.config_path}")
        except Exception as e:
            self.logger.error(f"Failed to create config directory: {e}")
            sys.exit(1)

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

    def watch_config_files(self):
        """Watch for configuration file changes using inotify"""
        inotify = inotify_simple.INotify()
        watch_flags = inotify_simple.flags.MODIFY | inotify_simple.flags.MOVED_TO

        # Watch the config directory
        inotify.add_watch(self.config_path, watch_flags)
        self.logger.info(f"Watching config directory: {self.config_path}")

        try:
            while self.running:
                # Check for file changes with timeout
                events = inotify.read(timeout=1000)  # 1 second timeout

                for event in events:
                    if event.name:
                        self.handle_config_change(event.name)

        except Exception as e:
            self.logger.error(f"Error in config file watcher: {e}")
        finally:
            inotify.close()

    def handle_config_change(self, filename):
        """Handle configuration file changes"""
        try:
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

            elif filename == 'camera_schedule.json':
                schedule = read_camera_schedule()
                if schedule:
                    self.logger.info(f"Camera schedule updated: {schedule}")
                    self.update_schedule(schedule)

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

    def update_schedule(self, schedule):
        """Update camera shooting schedule"""
        # This would implement scheduled shooting logic
        # For now, just log the schedule
        self.logger.info(f"Schedule updated: {schedule}")

    def run_scheduled_tasks(self):
        """Run scheduled camera tasks in separate thread"""
        while self.running:
            try:
                # Check for scheduled tasks
                schedule = read_camera_schedule()
                if schedule and schedule.get('enabled', False):
                    self.check_and_execute_schedule(schedule)
                # If no schedule file exists or schedule is disabled, just wait

                time.sleep(60)  # Check every minute

            except Exception as e:
                self.logger.error(f"Error in scheduled tasks: {e}")
                time.sleep(60)

    def check_and_execute_schedule(self, schedule):
        """Check if scheduled capture should be executed"""
        try:
            schedule_type = schedule.get('type', 'interval')

            if schedule_type == 'interval':
                interval_minutes = schedule.get('interval_minutes', 60)
                last_capture = schedule.get('last_capture')

                if not last_capture:
                    # First time, capture now
                    self.execute_scheduled_capture(schedule)
                else:
                    last_time = datetime.fromisoformat(last_capture)
                    if datetime.now() - last_time >= timedelta(minutes=interval_minutes):
                        self.execute_scheduled_capture(schedule)

            elif schedule_type == 'time':
                # Specific time scheduling (e.g., every day at 12:00)
                target_time = schedule.get('time', '12:00')
                # Implementation for specific time scheduling
                pass

        except Exception as e:
            self.logger.error(f"Error checking schedule: {e}")

    def execute_scheduled_capture(self, schedule):
        """Execute a scheduled capture"""
        try:
            result = self.camera.capture_photo()

            # Update last capture time in schedule
            schedule['last_capture'] = datetime.now().isoformat()
            config_manager.write_config('camera_schedule.json', schedule)

            self.logger.info(f"Scheduled capture completed: {result}")

            # Save capture result for MQTT to report
            config_manager.write_config('camera_result.json', result)

        except Exception as e:
            self.logger.error(f"Error in scheduled capture: {e}")
            # Also update stats for failed scheduled capture
            self.camera.update_capture_stats(False)

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

            self.logger.info("Camera daemon started")

            # Main loop - watch for config changes
            self.watch_config_files()

        except Exception as e:
            self.logger.error(f"Error in main loop: {e}")
        finally:
            self.logger.info("Camera daemon stopped")


if __name__ == "__main__":
    daemon = BMTLCameraDaemon()
    daemon.run()