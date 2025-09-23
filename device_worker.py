#!/usr/bin/env python3

import os
import sys
import json
import time
import logging
import signal
import configparser
import subprocess
import threading
from datetime import datetime, timedelta
from shared_config import config_manager
from gphoto2_controller import GPhoto2Controller
from utils import extract_device_id_from_hostname, get_last_capture_time, get_boot_time, get_temperature, get_current_sw_version

class DeviceWorker:
    """
    Background worker process that executes device-level actions.
    - Consumes commands from the task queue populated by the MQTT daemon.
    - Publishes results back through the shared response queue.
    """

    def __init__(self, task_queue, response_queue):
        self.task_queue = task_queue
        self.response_queue = response_queue

        self.config_path = "/etc/bmtl-device/config.ini"
        self.log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        self.running = True

        # Controllers
        self.gphoto_controller = GPhoto2Controller()

        self.setup_logging()
        self.load_device_info()

    def setup_logging(self):
        os.makedirs(self.log_dir, exist_ok=True)
        log_file = os.path.join(self.log_dir, "device_worker.log")

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger('DeviceWorker')

    def load_device_info(self):
        """Load device metadata required for device operations."""
        config = configparser.ConfigParser()
        config.read(self.config_path)
        self.device_id = config.get('device', 'id', fallback=extract_device_id_from_hostname())
        self.device_location = config.get('device', 'location', fallback='Unknown')
        self.git_repo_url = config.get('update', 'git_repo_url', fallback="https://github.com/your-repo/bmtl-device.git") # Configurable Git URL
        self.logger.info(f"Worker loaded device info: ID={self.device_id}, Location={self.device_location}, Git Repo={self.git_repo_url}")

    def _handle_shutdown_signal(self, signum, frame):
        """Handle termination signals by stopping the worker loop."""
        self.logger.info(f"Received signal {signum}, stopping worker loop.")
        self.running = False
        try:
            self.task_queue.put_nowait(None)
        except Exception:
            pass
        try:
            self.response_queue.put_nowait(None)
        except Exception:
            pass

    def run(self):
        """Main loop: read commands from the queue and dispatch handlers."""
        self.logger.info("Device worker started.")
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)
        
        command_handlers = {
            'settings_request_all': self.handle_settings_request_all,
            'settings_request_individual': self.handle_settings_request_individual,
            'status_request': self.handle_status_request,
            'settings_change': self.handle_settings_change,
            'set_sitename': self.handle_set_sitename,
            'sw_update': self.handle_sw_update,
            'sw_rollback': self.handle_sw_rollback,
            'sw_version_request': self.handle_sw_version_request,
            'reboot_all': self.handle_reboot_all,
            'reboot_individual': self.handle_reboot_individual,
            'options_request_individual': self.handle_options_request_individual,
            'options_request_all': self.handle_options_request_all,
            'wiper_request': self.handle_wiper_request,
            'camera_power_request': self.handle_camera_power_request,
            'health_check': self.send_health_status,
        }

        while self.running:
            try:
                task = self.task_queue.get()
                if task is None:
                    self.running = False
                    continue

                self.logger.info(f"Received task: {task.get('command')}")
                
                command = task.get('command')
                payload = task.get('payload')
                device_id = task.get('device_id')

                handler = command_handlers.get(command)
                if handler:
                    # Handlers expect device_id and payload (if applicable)
                    if command in ['settings_change', 'set_sitename', 'sw_update', 'sw_rollback']:
                        handler(device_id, payload)
                    else:
                        handler(device_id)
                else:
                    self.logger.warning(f"Unknown command received: {command}")

            except Exception as e:
                self.logger.error(f"Error processing task: {e}", exc_info=True)

        self.logger.info("Device worker stopped.")

    def _publish(self, topic, payload):
        """Enqueue an outgoing MQTT response via the shared queue."""
        self.response_queue.put({
            'topic': topic,
            'payload': json.dumps(payload),
            'qos': 1
        })

    # ##################################################################
    # The following handlers mirror logic from the legacy device_mqtt_handler module
    # Calls were adapted from self.client.publish(...) to self._publish(...)
    # ##################################################################

    def get_enhanced_settings(self):
        """Return combined camera and device settings for inspector consumption."""
        try:
            gphoto_settings = self.gphoto_controller.get_current_settings()
            base_settings = gphoto_settings.get('settings', {}) if gphoto_settings.get('success') else {}
            
            # Helper for parsing additional configuration entries
            schedule_settings = config_manager.read_config('schedule_settings.json')
            image_settings = config_manager.read_config('image_settings.json')

            enhanced_settings = {
                "iso": base_settings.get("iso", "auto"),
                "aperture": base_settings.get("aperture", "f/2.8"),
                "shutter_speed": base_settings.get("shutter_speed", "1/60"),
                "startTime": schedule_settings.get("start_time", "08:00"),
                "endTime": schedule_settings.get("end_time", "18:00"),
                "captureInterval": schedule_settings.get("capture_interval", "10"),
                "imageSize": image_settings.get("image_size", "1920x1080"),
                "quality": image_settings.get("quality", "85"),
                "format": image_settings.get("format", "jpeg")
            }
            return enhanced_settings
        except Exception as e:
            self.logger.error(f"Error getting enhanced settings: {e}")
            return {}

    def handle_settings_request_all(self, device_id):
        try:
            enhanced_settings = self.get_enhanced_settings()
            response = {
                "response_type": "all_settings",
                "modules": {f"bmotion{device_id}": enhanced_settings},
                "timestamp": datetime.now().isoformat()
            }
            self._publish("bmtl/response/settings/all", response)
            self.logger.info("Sent all settings response")
        except Exception as e:
            self.logger.error(f"Error handling all settings request: {e}")

    def handle_settings_request_individual(self, device_id):
        try:
            enhanced_settings = self.get_enhanced_settings()
            response = {
                "response_type": "settings",
                "module_id": f"bmotion{device_id}",
                "settings": enhanced_settings,
                "timestamp": datetime.now().isoformat()
            }
            self._publish(f"bmtl/response/settings/{device_id}", response)
            self.logger.info("Sent individual settings response")
        except Exception as e:
            self.logger.error(f"Error handling individual settings request: {e}")

    def handle_status_request(self, device_id):
        try:
            response = {
                "response_type": "status",
                "system_status": "normal",
                "connected_modules": [f"bmotion{device_id}"],
                "timestamp": datetime.now().isoformat()
            }
            self._publish("bmtl/response/status", response)
            self.logger.info("Sent status response")
        except Exception as e:
            self.logger.error(f"Error handling status request: {e}")

    def handle_settings_change(self, device_id, payload):
        """Apply incoming setting changes on the device."""
        try:
            settings_data = json.loads(payload) if payload else {}
            self.logger.info(f"Received settings change request: {settings_data}")

            gphoto_settings, schedule_settings, image_settings = {}, {}, {}
            if "iso" in settings_data: gphoto_settings["iso"] = settings_data["iso"]
            if "aperture" in settings_data: gphoto_settings["aperture"] = settings_data["aperture"]
            if "shutter_speed" in settings_data: gphoto_settings["shutter_speed"] = settings_data["shutter_speed"]
            if "startTime" in settings_data: schedule_settings["start_time"] = settings_data["startTime"]
            if "endTime" in settings_data: schedule_settings["end_time"] = settings_data["endTime"]
            if "captureInterval" in settings_data: schedule_settings["capture_interval"] = settings_data["captureInterval"]
            if "imageSize" in settings_data: image_settings["image_size"] = settings_data["imageSize"]
            if "quality" in settings_data: image_settings["quality"] = settings_data["quality"]
            if "format" in settings_data: image_settings["format"] = settings_data["format"]

            results = {"gphoto_settings": {"success": True, "errors": []}, "schedule_settings": {"success": True, "errors": []}, "image_settings": {"success": True, "errors": []}}
            
            if gphoto_settings:
                results["gphoto_settings"] = self.gphoto_controller.apply_settings(gphoto_settings)
            if schedule_settings:
                try:
                    config_manager.write_config('schedule_settings.json', schedule_settings)
                except Exception as e:
                    results["schedule_settings"]["success"] = False
                    results["schedule_settings"]["errors"].append(str(e))
            if image_settings:
                try:
                    config_manager.write_config('image_settings.json', image_settings)
                except Exception as e:
                    results["image_settings"]["success"] = False
                    results["image_settings"]["errors"].append(str(e))

            config_manager.write_config('camera_settings.json', settings_data)
            overall_success = all(r["success"] for r in results.values())
            
            response = {
                "response_type": "set_settings_result",
                "module_id": f"bmotion{device_id}",
                "success": overall_success,
                "message": "Settings applied successfully" if overall_success else "Some settings failed to apply",
                "timestamp": datetime.now().isoformat()
            }
            self._publish(f"bmtl/response/set/settings/{device_id}", response)
            self.logger.info(f"Applied settings and sent response. Success: {overall_success}")

        except Exception as e:
            self.logger.error(f"Error handling settings change: {e}")
            error_response = {
                "response_type": "set_settings_result",
                "module_id": f"bmotion{device_id}",
                "success": False, "message": f"Settings application failed: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
            self._publish(f"bmtl/response/set/settings/{device_id}", error_response)

    def handle_reboot_all(self, device_id):
        try:
            response = {
                "response_type": "reboot_all_result", "success": True,
                "message": "Global reboot initiated successfully",
                "affected_modules": [f"bmotion{device_id}"],
                "timestamp": datetime.now().isoformat()
            }
            self._publish("bmtl/response/reboot/all", response)
            self.logger.info("Sent reboot all response, now rebooting...")
            os.system("sudo reboot")
        except Exception as e:
            self.logger.error(f"Error handling reboot all: {e}")

    def handle_reboot_individual(self, device_id):
        try:
            response = {
                "response_type": "reboot_result", "module_id": f"bmotion{device_id}",
                "success": True, "message": "Reboot initiated successfully",
                "timestamp": datetime.now().isoformat()
            }
            self._publish(f"bmtl/response/reboot/{device_id}", response)
            self.logger.info("Sent individual reboot response, now rebooting...")
            os.system("sudo reboot")
        except Exception as e:
            self.logger.error(f"Error handling individual reboot: {e}")

    def handle_options_request_individual(self, device_id):
        try:
            options_result = self.gphoto_controller.get_camera_options()
            response = {
                "response_type": "options", "module_id": f"bmotion{device_id}",
                "options": options_result.get('options', {}) if options_result.get('success') else {},
                "timestamp": datetime.now().isoformat()
            }
            self._publish(f"bmtl/response/options/{device_id}", response)
            self.logger.info("Sent individual options response")
        except Exception as e:
            self.logger.error(f"Error handling individual options request: {e}")

    def handle_options_request_all(self, device_id):
        try:
            options_result = self.gphoto_controller.get_camera_options()
            response = {
                "response_type": "all_options",
                "modules": {f"bmotion{device_id}": options_result.get('options', {}) if options_result.get('success') else {}},
                "timestamp": datetime.now().isoformat()
            }
            self._publish("bmtl/response/options/all", response)
            self.logger.info("Sent all options response")
        except Exception as e:
            self.logger.error(f"Error handling all options request: {e}")

    def handle_wiper_request(self, device_id):
        try:
            # Placeholder for GPIO-controlled wiper hardware integration
            self.logger.info("Wiper operation simulated.")
            response = {
                "response_type": "wiper_result", "module_id": f"bmotion{device_id}",
                "success": True, "message": "Wiper operation completed",
                "timestamp": datetime.now().isoformat()
            }
            self._publish(f"bmtl/response/wiper/{device_id}", response)
            self.logger.info("Sent wiper response")
        except Exception as e:
            self.logger.error(f"Error handling wiper request: {e}")

    def handle_camera_power_request(self, device_id):
        try:
            result = self.gphoto_controller.camera_power_toggle()
            response = {
                "response_type": "camera_power_result", "module_id": f"bmotion{device_id}",
                "success": result.get('success', False), "message": result.get('message', ''),
                "new_state": result.get('current_state', 'unknown'),
                "timestamp": datetime.now().isoformat()
            }
            self._publish(f"bmtl/response/camera-on-off/{device_id}", response)
            self.logger.info("Sent camera power response")
        except Exception as e:
            self.logger.error(f"Error handling camera power request: {e}")

    def handle_set_sitename(self, device_id, payload):
        try:
            data = json.loads(payload) if payload else {}
            new_sitename = data.get('site_name', '')
            if not new_sitename:
                # Ignore empty site-name requests
                return

            config = configparser.ConfigParser()
            config.read(self.config_path)
            if not config.has_section('device'): config.add_section('device')
            config.set('device', 'location', new_sitename)
            with open(self.config_path, 'w') as configfile:
                config.write(configfile)
            
            self.device_location = new_sitename  # Update cached location
            
            response = {
                "response_type": "set_sitename_result", "module_id": f"bmotion{device_id}",
                "success": True, "message": f"Site name updated to '{new_sitename}'. Daemon will restart.",
                "new_sitename": new_sitename, "timestamp": datetime.now().isoformat()
            }
            self._publish(f"bmtl/response/set/sitename/{device_id}", response)
            self.logger.info(f"Site name updated to '{new_sitename}', restarting service.")

            def restart_service():
                time.sleep(2)
                os.system("sudo systemctl restart bmtl-device.service")  # Restart service to reload config
            threading.Thread(target=restart_service, daemon=True).start()

        except Exception as e:
            self.logger.error(f"Error handling set sitename: {e}")

    def handle_sw_update(self, device_id, payload):
        """
        Handles the software update request by initiating a robust Blue/Green update process
        in a background thread.
        """
        try:
            response = {
                "response_type": "sw_update_result",
                "module_id": f"bmotion{device_id}",
                "success": True,
                "message": "Robust software update process initiated.",
                "timestamp": datetime.now().isoformat()
            }
            self._publish(f"bmtl/response/sw-update/{device_id}", response)
            self.logger.info("Software update initiated. Starting robust update process in background.")

            update_thread = threading.Thread(
                target=self._execute_robust_update,
                args=(device_id, payload),
                daemon=True
            )
            update_thread.start()

        except Exception as e:
            self.logger.error(f"Error initiating software update thread: {e}")
            self._publish(f"bmtl/response/sw-update/{device_id}", {
                "response_type": "sw_update_result", "module_id": f"bmotion{device_id}",
                "success": False, "message": f"Failed to start update thread: {e}",
                "timestamp": datetime.now().isoformat()
            })

    def _execute_robust_update(self, device_id, payload):
        """
        Executes the Blue/Green update logic.
        1. Determines active/inactive directories.
        2. Fetches new code into the inactive directory.
        3. Verifies the new code and dependencies.
        4. If successful, switches the 'current' symlink and restarts.
        5. If it fails at any step, it reports an error and cleans up.
        """
        base_dir = "/opt/bmtl-device"
        link_path = os.path.join(base_dir, "current")
        
        try:
            # 1. Determine active and inactive directories
            if not os.path.islink(link_path):
                raise FileNotFoundError("Symbolic link 'current' not found. Please run initial setup.")

            active_path = os.path.realpath(link_path)
            active_dir_name = os.path.basename(active_path)
            
            if active_dir_name == "v1":
                inactive_path = os.path.join(base_dir, "v2")
            elif active_dir_name == "v2":
                inactive_path = os.path.join(base_dir, "v1")
            else:
                raise ValueError(f"The 'current' link points to an unknown directory: {active_path}")

            self.logger.info(f"Active version: {active_dir_name}. Updating in {os.path.basename(inactive_path)}.")

            # 2. Fetch new code into inactive directory
            git_repo_url = self.git_repo_url
            
            if os.path.exists(inactive_path):
                subprocess.run(f"rm -rf {inactive_path}", shell=True, check=True)

            subprocess.run(
                ["git", "clone", "--depth=1", git_repo_url, inactive_path],
                check=True, capture_output=True, text=True
            )
            self.logger.info(f"Cloned latest code into {inactive_path}")

            # 3. Verify new code and install dependencies in a virtual environment
            venv_path = os.path.join(inactive_path, "venv")
            python_path = os.path.join(venv_path, "bin/python")
            pip_path = os.path.join(venv_path, "bin/pip")

            subprocess.run([sys.executable, "-m", "venv", venv_path], check=True)
            subprocess.run([pip_path, "install", "-r", os.path.join(inactive_path, "requirements.txt")], check=True)
            self.logger.info("Dependencies installed in virtual environment.")

            # Basic code integrity check
            result = subprocess.run([python_path, "-m", "compileall", inactive_path], capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Code verification failed (compileall): {result.stderr}")
            self.logger.info("Code verification successful.")

            # 4. Switch over: update the symbolic link
            # Use -n option with ln to avoid issues if 'current' is a dir
            subprocess.run(["ln", "-sfn", inactive_path, link_path], check=True)
            self.logger.info(f"Switched 'current' link to point to {os.path.basename(inactive_path)}")

            # 5. Restart the service to apply the update
            self.logger.info("Restarting service to apply update...")
            self._publish(f"bmtl/response/sw-update/{device_id}", {
                "response_type": "sw_update_result", "module_id": f"bmotion{device_id}",
                "success": True, "message": "Update successful. Restarting service.",
                "timestamp": datetime.now().isoformat()
            })
            time.sleep(1) # Allow time for the message to be sent
            subprocess.run(["sudo", "systemctl", "restart", "bmtl-device.service"], check=True)

        except (FileNotFoundError, ValueError, subprocess.CalledProcessError, RuntimeError) as e:
            self.logger.error(f"Robust update failed: {e}")
            if isinstance(e, subprocess.CalledProcessError):
                self.logger.error(f"Command stdout: {e.stdout}")
                self.logger.error(f"Command stderr: {e.stderr}")
            
            # Clean up the failed update directory
            if 'inactive_path' in locals() and os.path.exists(inactive_path):
                subprocess.run(f"rm -rf {inactive_path}", shell=True)
                self.logger.info(f"Cleaned up failed update directory: {inactive_path}")

            self._publish(f"bmtl/response/sw-update/{device_id}", {
                "response_type": "sw_update_result", "module_id": f"bmotion{device_id}",
                "success": False, "message": f"Update failed: {e}",
                "timestamp": datetime.now().isoformat()
            })

    def handle_sw_rollback(self, device_id, payload):
        # TODO: Additional update logic placeholder
        pass

    def handle_sw_version_request(self, device_id):
        try:
            commit_hash = "unknown"
            try:
                result = subprocess.run(["git", "rev-parse", "HEAD"], cwd="/opt/bmtl-device", capture_output=True, text=True, timeout=10)
                if result.returncode == 0: commit_hash = result.stdout.strip()[:12]
            except Exception as e:
                self.logger.warning(f"Failed to get git commit hash: {e}")

            response = {
                "response_type": "sw_version_result", "module_id": f"bmotion{device_id}",
                "success": True, "commit_hash": commit_hash,
                "timestamp": datetime.now().isoformat()
            }
            self._publish(f"bmtl/response/sw-version/{device_id}", response)
            self.logger.info(f"Sent SW version response: {commit_hash}")
        except Exception as e:
            self.logger.error(f"Error handling SW version request: {e}")

    def send_health_status(self, device_id):
        """Summarise recent capture information for diagnostics."""
        - After uploads complete, images move to backup storage for counting.
        - Operating window start/end times are required for scheduling.
        - Retention limits ensure old captures are recycled safely.
        """
        try:
            # Load paths from config
            cfg = configparser.ConfigParser()
            cfg.read(self.config_path)
            backup_path = cfg.get('device', 'backup_path', fallback='/opt/bmtl-device/backup')
            # Use backup_path for storage usage and counts
            storage_path = backup_path

            storage_used_percentage = 0
            if os.path.exists(storage_path):
                try:
                    import shutil
                    total, used, free = shutil.disk_usage(storage_path)
                    storage_used_percentage = round((used / total) * 100, 2)
                except Exception as e:
                    self.logger.warning(f"Failed to get disk usage for {storage_path}: {e}")
                    # Fallback to approximate calculation if disk_usage fails
                    total_size = sum(os.path.getsize(os.path.join(storage_path, f))
                                   for f in os.listdir(storage_path)
                                   if os.path.isfile(os.path.join(storage_path, f)))
                    # Assume 10GB total capacity for approximation if actual disk_usage fails
                    storage_used_percentage = round((total_size / (10 * 1024 * 1024 * 1024)) * 100, 2)

            # Ensure existing entries remain when merging configuration
            today = datetime.now().strftime("%Y%m%d")
            today_captures = 0
            if os.path.exists(storage_path):
                today_captures = len([f for f in os.listdir(storage_path)
                                    if f.startswith(f"photo_{today}")])

            # Load start/end time and operating schedule values
            schedule = config_manager.read_config('schedule_settings.json') or {}
            start_time_str = schedule.get('start_time', '00:00')
            end_time_str = schedule.get('end_time', '23:59')
            try:
                interval_min = int(schedule.get('capture_interval', 60))
                if interval_min <= 0:
                    interval_min = 60
            except Exception:
                interval_min = 60

            def parse_hhmm(s):
                try:
                    hh, mm = s.split(':')
                    return int(hh), int(mm)
                except Exception:
                    return 0, 0

            now_dt = datetime.now()
            sh, sm = parse_hhmm(start_time_str)
            eh, em = parse_hhmm(end_time_str)
            start_dt = now_dt.replace(hour=sh, minute=sm, second=0, microsecond=0)
            end_dt = now_dt.replace(hour=eh, minute=em, second=0, microsecond=0)
            if end_dt <= start_dt:
                # Overnight window: treat end as next day (e.g., 20:00 -> 06:00)
                end_dt = end_dt + timedelta(days=1)

            total_slots = 0
            expected_by_now = 0
            if interval_min > 0:
                window_seconds = max(0, (end_dt - start_dt).total_seconds())
                total_slots = int(window_seconds // (interval_min * 60)) + (1 if window_seconds >= 0 else 0)

                if now_dt < start_dt:
                    expected_by_now = 0
                elif now_dt >= end_dt:
                    expected_by_now = total_slots
                else:
                    elapsed = (now_dt - start_dt).total_seconds()
                    expected_by_now = int(elapsed // (interval_min * 60)) + 1

            missed = max(0, expected_by_now - today_captures)

            payload = {
                "module_id": f"bmotion{device_id}",
                "status": "online",
                "storage_used": storage_used_percentage,
                "temperature": get_temperature(),
                "last_capture_time": get_last_capture_time(),
                "last_boot_time": get_boot_time(),
                "site_name": self.device_location,
                "today_total_captures": total_slots,
                "today_captured_count": today_captures,
                "missed_captures": missed,
                "sw_version": get_current_sw_version(),
                "timestamp": datetime.now().isoformat()
            }
            self._publish(f"bmtl/status/health/{device_id}", payload)
        except Exception as e:
            self.logger.error(f"Error sending health status: {e}")

    # ##################################################################
    #                  Helper methods from handler
    # ##################################################################
    def get_last_capture_time(self):
        return get_last_capture_time()
    def get_boot_time(self):
        return get_boot_time()
    def get_temperature(self):
        return get_temperature()
    def get_current_sw_version(self):
        return get_current_sw_version()
