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
from datetime import datetime, timedelta, timezone
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

        # Ensure only one software update runs at a time
        self._update_lock = threading.Lock()
        self._update_thread = None

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
            'camera_power_status_request': self.handle_camera_power_status_request,
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
                    payload_commands = {
                        'settings_change',
                        'set_sitename',
                        'sw_update',
                        'sw_rollback',
                        'reboot_all',
                        'reboot_individual',
                        'wiper_request',
                        'camera_power_request',
                        'camera_power_status_request',
                    }
                    if command in payload_commands:
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
        """Return combined camera and device settings using snake_case keys."""
        try:
            gphoto_response = self.gphoto_controller.get_current_settings()
            if isinstance(gphoto_response, dict):
                camera_settings = gphoto_response.get('settings', {}) or {}
                camera_options_raw = gphoto_response.get('options', {}) or {}
                camera_success = bool(gphoto_response.get('success'))
                camera_errors = gphoto_response.get('errors', []) or []
                camera_partial = bool(gphoto_response.get('partial_success', False))
            else:
                camera_settings = {}
                camera_options_raw = {}
                camera_success = False
                camera_errors = []
                camera_partial = False

            schedule_settings = config_manager.read_config('schedule_settings.json') or {}
            image_settings = config_manager.read_config('image_settings.json') or {}

            enhanced_settings = {
                # Requested defaults when values are missing
                'iso': camera_settings.get('iso', '800'),
                # In this project, 'aperture' tracks exposure compensation
                'aperture': camera_settings.get('aperture', '-3'),
                'focus_mode': camera_settings.get('focus_mode', 'MF (fixed)'),
                'resolution': camera_settings.get('resolution', image_settings.get('image_size', '3696x2448')),
                'image_quality': camera_settings.get('image_quality', 'JPEG Fine'),
                'start_time': schedule_settings.get('start_time', '08:00'),
                'end_time': schedule_settings.get('end_time', '18:00'),
                'capture_interval': schedule_settings.get('capture_interval', '10'),
                'format': image_settings.get('format', 'jpeg'),
            }

            camera_options = self._prepare_camera_options(camera_options_raw)
            camera_meta = {
                'success': camera_success,
                'errors': camera_errors,
                'partial_success': camera_partial,
            }
            if not camera_meta.get('errors'):
                camera_meta.pop('errors', None)
            if not camera_meta.get('partial_success'):
                camera_meta.pop('partial_success', None)

            return enhanced_settings, camera_options, camera_meta
        except Exception as e:
            self.logger.error(f"Error getting enhanced settings: {e}")
            return {}, {}, {'success': False, 'errors': [str(e)]}

    def _prepare_camera_options(self, options):
        """Normalize camera option keys for consistency."""
        prepared = {}

        for key, raw in (options or {}).items():
            payload = dict(raw or {})
            payload.setdefault('canonical_key', key)
            payload.setdefault('choices', [])
            prepared[key] = payload

        return prepared
    def handle_settings_request_all(self, device_id):
        try:
            self.handle_settings_request_individual(device_id)
        except Exception as e:
            self.logger.error(f"Error handling all settings request: {e}")


    def handle_settings_request_individual(self, device_id):
        try:
            settings_payload, camera_options, camera_meta = self.get_enhanced_settings()
            response = {
                'response_type': 'settings',
                'module_id': f"bmotion{device_id}",
                'settings': settings_payload,
                'camera_options': camera_options,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }

            if isinstance(camera_meta, dict):
                status = {}
                if 'success' in camera_meta:
                    status['success'] = camera_meta['success']
                if camera_meta.get('errors'):
                    status['errors'] = camera_meta['errors']
                if camera_meta.get('partial_success'):
                    status['partial_success'] = True
                if status:
                    response['camera_options_status'] = status

            self._publish(f"bmtl/response/settings/{device_id}", response)
            self.logger.info("Sent individual settings response")
        except Exception as e:
            self.logger.error(f"Error handling individual settings request: {e}")


    def handle_status_request(self, device_id):
        try:
            self.send_health_status(device_id)
        except Exception as e:
            self.logger.error(f"Error handling status request: {e}")


    def handle_settings_change(self, device_id, payload):
        """Apply incoming setting changes on the device."""
        request_id = None
        try:
            settings_data = json.loads(payload) if payload else {}
            request_id = settings_data.pop('request_id', None)
            self.logger.info(f"Received settings change request: {settings_data}")

            gphoto_settings, schedule_settings, image_settings = {}, {}, {}
            if 'iso' in settings_data:
                gphoto_settings['iso'] = settings_data['iso']
            if 'aperture' in settings_data:
                gphoto_settings['aperture'] = settings_data['aperture']
            if 'shutter_speed' in settings_data:
                gphoto_settings['shutter_speed'] = settings_data['shutter_speed']
            if 'image_quality' in settings_data:
                gphoto_settings['image_quality'] = settings_data['image_quality']
            if 'focus_mode' in settings_data:
                gphoto_settings['focus_mode'] = settings_data['focus_mode']
            if 'white_balance' in settings_data:
                gphoto_settings['whitebalance'] = settings_data['white_balance']
            if 'whitebalance' in settings_data:
                gphoto_settings['whitebalance'] = settings_data['whitebalance']
            if 'start_time' in settings_data:
                schedule_settings['start_time'] = settings_data['start_time']
            if 'end_time' in settings_data:
                schedule_settings['end_time'] = settings_data['end_time']
            if 'capture_interval' in settings_data:
                schedule_settings['capture_interval'] = settings_data['capture_interval']
            if 'resolution' in settings_data:
                image_settings['image_size'] = settings_data['resolution']
            if 'quality' in settings_data:
                image_settings['quality'] = settings_data['quality']
            if 'format' in settings_data:
                image_settings['format'] = settings_data['format']

            results = {
                'gphoto_settings': {'success': True, 'errors': []},
                'schedule_settings': {'success': True, 'errors': []},
                'image_settings': {'success': True, 'errors': []}
            }

            if gphoto_settings:
                try:
                    alias_map = {
                        'shutter_speed': 'shutterspeed',
                        'image_quality': 'imagequality',
                        'focus_mode': 'focusmode2',
                    }
                    camera_config_payload = {}
                    for source_key, value in gphoto_settings.items():
                        target_key = alias_map.get(source_key, source_key)
                        camera_config_payload[target_key] = value

                    if camera_config_payload:
                        # Write only when values actually change; persist full desired state
                        existing_config = config_manager.read_config('camera_config.json') or {}
                        diff_payload = {k: v for k, v in camera_config_payload.items() if existing_config.get(k) != v}
                        if diff_payload:
                            new_config = dict(existing_config)
                            new_config.update(diff_payload)
                            config_manager.write_config('camera_config.json', new_config)

                    results['gphoto_settings'] = {
                        'success': True,
                        'errors': [],
                        'queued_settings': diff_payload if 'diff_payload' in locals() else {},
                    }
                except Exception as exc:
                    results['gphoto_settings']['success'] = False
                    results['gphoto_settings']['errors'].append(str(exc))
            if schedule_settings:
                try:
                    # Avoid spurious writes: only persist changed schedule fields
                    existing_sched = config_manager.read_config('schedule_settings.json') or {}
                    changed = {k: v for k, v in schedule_settings.items() if existing_sched.get(k) != v}
                    if changed:
                        config_manager.write_config('schedule_settings.json', {**existing_sched, **changed})
                except Exception as exc:
                    results['schedule_settings']['success'] = False
                    results['schedule_settings']['errors'].append(str(exc))
            if image_settings:
                try:
                    # Avoid spurious writes: only persist changed image fields
                    existing_img = config_manager.read_config('image_settings.json') or {}
                    changed_img = {k: v for k, v in image_settings.items() if existing_img.get(k) != v}
                    if changed_img:
                        config_manager.write_config('image_settings.json', {**existing_img, **changed_img})
                except Exception as exc:
                    results['image_settings']['success'] = False
                    results['image_settings']['errors'].append(str(exc))
            config_manager.write_config('camera_settings.json', settings_data)
            overall_success = all(result['success'] for result in results.values())

            applied_settings = {key: value for key, value in settings_data.items()}
            response = {
                'success': overall_success,
                'message': 'Settings applied successfully' if overall_success else 'Some settings failed to apply',
                'applied': applied_settings,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            if request_id:
                response['request_id'] = request_id
            self._publish(f"bmtl/response/set/settings/{device_id}", response)
            self.logger.info(f"Applied settings and sent response. Success: {overall_success}")

        except Exception as exc:
            self.logger.error(f"Error handling settings change: {exc}")
            error_response = {
                'success': False,
                'message': f"Settings application failed: {exc}",
                'applied': {},
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            if request_id:
                error_response['request_id'] = request_id
            self._publish(f"bmtl/response/set/settings/{device_id}", error_response)


    def handle_reboot_all(self, device_id, payload):
        try:
            request = json.loads(payload) if payload else {}
            request_id = request.get('request_id')

            ack = {
                'response_type': 'reboot_ack',
                'accepted': True,
                'message': 'Global reboot scheduled',
                'eta_ms': 1000,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            if request_id:
                ack['request_id'] = request_id
            self._publish(f"bmtl/ack/reboot/{device_id}", ack)

            response = {
                'response_type': 'reboot_all_result',
                'success': True,
                'message': 'Global reboot initiated',
                'affected_modules': [f"bmotion{device_id}"],
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            if request_id:
                response['request_id'] = request_id
            self._publish('bmtl/response/reboot/all', response)
            self.logger.info('Sent reboot-all acknowledgement, rebooting...')
            os.system('sudo reboot')
        except Exception as exc:
            self.logger.error(f"Error handling reboot all: {exc}")


    def handle_reboot_individual(self, device_id, payload):
        try:
            request = json.loads(payload) if payload else {}
            request_id = request.get('request_id')

            ack = {
                'response_type': 'reboot_ack',
                'accepted': True,
                'message': 'Rebooting soon',
                'eta_ms': 1000,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            if request_id:
                ack['request_id'] = request_id
            self._publish(f"bmtl/ack/reboot/{device_id}", ack)

            response = {
                'response_type': 'reboot_result',
                'module_id': f"bmotion{device_id}",
                'success': True,
                'message': 'Reboot initiated',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            if request_id:
                response['request_id'] = request_id
            self._publish(f"bmtl/response/reboot/{device_id}", response)
            self.logger.info('Sent individual reboot acknowledgement, rebooting...')
            os.system('sudo reboot')
        except Exception as exc:
            self.logger.error(f"Error handling individual reboot: {exc}")


    def handle_options_request_individual(self, device_id):
        try:
            options_result = self.gphoto_controller.get_camera_options()
            response = {
                'response_type': 'options',
                'module_id': f"bmotion{device_id}",
                'options': options_result.get('options', {}) if options_result.get('success') else {},
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            self._publish(f"bmtl/response/options/{device_id}", response)
            self.logger.info('Sent individual options response')
        except Exception as exc:
            self.logger.error(f"Error handling individual options request: {exc}")


    def handle_options_request_all(self, device_id):
        try:
            self.handle_options_request_individual(device_id)
        except Exception as exc:
            self.logger.error(f"Error handling all options request: {exc}")


    def handle_wiper_request(self, device_id, payload):
        try:
            data = json.loads(payload) if payload else {}
            request_id = data.get('request_id')
            duration = data.get('duration_s')

            self.logger.info('Wiper operation simulated.')
            response = {
                'response_type': 'wiper_result',
                'module_id': f"bmotion{device_id}",
                'success': True,
                'message': 'Wiper operation completed',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            if request_id:
                response['request_id'] = request_id
            if duration is not None:
                response['duration_s'] = duration
            self._publish(f"bmtl/response/wiper/{device_id}", response)
            self.logger.info('Sent wiper response')
        except Exception as exc:
            self.logger.error(f"Error handling wiper request: {exc}")


    def handle_camera_power_request(self, device_id, payload):
        try:
            data = json.loads(payload) if payload else {}
            request_id = data.get('request_id')

            result = self.gphoto_controller.camera_power_toggle()
            response = {
                'response_type': 'camera_power_result',
                'module_id': f"bmotion{device_id}",
                'success': result.get('success', False),
                'message': result.get('message', ''),
                'new_state': result.get('current_state', 'unknown'),
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            if request_id:
                response['request_id'] = request_id
            self._publish(f"bmtl/response/camera-on-off/{device_id}", response)
            self.logger.info('Sent camera power response')
        except Exception as exc:
            self.logger.error(f"Error handling camera power request: {exc}")


    def handle_camera_power_status_request(self, device_id, payload):
        try:
            data = json.loads(payload) if payload else {}
            request_id = data.get('request_id')

            result = self.gphoto_controller.camera_power_toggle()
            power_status = 'on' if result.get('current_state') == 'on' else 'off'
            response = {
                'success': result.get('success', False),
                'power_status': power_status,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            if not result.get('success', False) and result.get('error'):
                response['message'] = result['error']
            if request_id:
                response['request_id'] = request_id
            self._publish(f"bmtl/response/camera-power-status/{device_id}", response)
            self.logger.info('Sent camera power status response')
        except Exception as exc:
            self.logger.error(f"Error handling camera power status request: {exc}")


    def handle_set_sitename(self, device_id, payload):
        try:
            data = json.loads(payload) if payload else {}
            request_id = data.get('request_id')
            new_sitename = data.get('site_name', '')
            if not new_sitename:
                return

            config = configparser.ConfigParser()
            config.read(self.config_path)
            if not config.has_section('device'):
                config.add_section('device')
            config.set('device', 'location', new_sitename)
            with open(self.config_path, 'w') as configfile:
                config.write(configfile)

            self.device_location = new_sitename

            response = {
                'response_type': 'set_sitename_result',
                'module_id': f"bmotion{device_id}",
                'success': True,
                'message': f"Site name updated to '{new_sitename}'. Daemon will restart.",
                'site_name': new_sitename,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            if request_id:
                response['request_id'] = request_id
            self._publish(f"bmtl/response/set/sitename/{device_id}", response)
            self.logger.info(f"Site name updated to '{new_sitename}', restarting service.")

            def restart_service():
                time.sleep(2)
                os.system('sudo systemctl restart bmtl-device.service')
            threading.Thread(target=restart_service, daemon=True).start()

        except Exception as exc:
            self.logger.error(f"Error handling set sitename: {exc}")


    def handle_sw_update(self, device_id, payload):
        """
        Handle software update request.
        - Ensures only one update runs at a time to avoid races that can remove
          the active slot while the service restarts (sporadic 203/EXEC issues).
        - Starts the robust Blue/Green update in a background thread.
        """
        try:
            # Concurrency guard: reject or ignore if an update is already running
            if self._update_lock.locked() or (self._update_thread and self._update_thread.is_alive()):
                self.logger.warning("Update request ignored: another update is already in progress.")
                self._publish(f"bmtl/response/sw-update/{device_id}", {
                    "response_type": "sw_update_result",
                    "module_id": f"bmotion{device_id}",
                    "success": False,
                    "message": "Update already in progress. Ignoring duplicate request.",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                return

            # Acknowledge start
            self._publish(f"bmtl/response/sw-update/{device_id}", {
                "response_type": "sw_update_result",
                "module_id": f"bmotion{device_id}",
                "success": True,
                "message": "Robust software update process initiated.",
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            self.logger.info("Software update initiated. Starting robust update process in background.")

            # Wrap execution with the lock to serialize updates
            def run_with_lock():
                acquired = self._update_lock.acquire(blocking=False)
                if not acquired:
                    # Another update slipped in; just log and return
                    self.logger.warning("Skipped starting update thread: lock already held.")
                    return
                try:
                    self._execute_robust_update(device_id, payload)
                finally:
                    try:
                        self._update_lock.release()
                    except Exception:
                        pass

            self._update_thread = threading.Thread(target=run_with_lock, daemon=True, name="UpdateThread")
            self._update_thread.start()

        except Exception as e:
            self.logger.error(f"Error initiating software update thread: {e}")
            self._publish(f"bmtl/response/sw-update/{device_id}", {
                "response_type": "sw_update_result", "module_id": f"bmotion{device_id}",
                "success": False, "message": f"Failed to start update thread: {e}",
                "timestamp": datetime.now(timezone.utc).isoformat()
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

            pip_env = os.environ.copy()
            pip_cache_dir = os.path.join(inactive_path, "pip-cache")
            pip_tmp_dir = os.path.join(inactive_path, "pip-tmp")
            os.makedirs(pip_cache_dir, exist_ok=True)
            os.makedirs(pip_tmp_dir, exist_ok=True)
            pip_env["PIP_CACHE_DIR"] = pip_cache_dir
            pip_env["XDG_CACHE_HOME"] = pip_cache_dir
            pip_env["TMPDIR"] = pip_tmp_dir
            pip_env.setdefault("HOME", inactive_path)

            subprocess.run([pip_path, "install", "--no-cache-dir", "-r", os.path.join(inactive_path, "requirements.txt")], check=True, env=pip_env)
            self.logger.info("Dependencies installed in virtual environment.")

            # Basic code integrity check
            result = subprocess.run([python_path, "-m", "compileall", inactive_path], capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Code verification failed (compileall): {result.stderr}")
            self.logger.info("Code verification successful.")

            # 4. Switch over: update the symbolic link
            # Safety: ensure new venv Python exists before switching
            if not (os.path.isfile(python_path) and os.access(python_path, os.X_OK)):
                raise RuntimeError(f"New environment missing or not executable: {python_path}")

            # Use -sfn with ln to atomically repoint 'current'
            previous_path = active_path
            subprocess.run(["ln", "-sfn", inactive_path, link_path], check=True)
            self.logger.info(f"Switched 'current' link to point to {os.path.basename(inactive_path)}")

            # Post-switch sanity check: verify ExecStart interpreter path exists
            current_python = os.path.join(link_path, "venv", "bin", "python")
            if not (os.path.isfile(current_python) and os.access(current_python, os.X_OK)):
                # Roll back the link immediately to prevent service crash loops
                self.logger.error(f"Post-switch check failed: {current_python} not present/executable. Reverting link.")
                subprocess.run(["ln", "-sfn", previous_path, link_path], check=True)
                raise RuntimeError("Post-switch venv check failed; reverted to previous version")

            # 5. Restart the service to apply the update
            self.logger.info("Restarting service to apply update...")
            self._publish(f"bmtl/response/sw-update/{device_id}", {
                "response_type": "sw_update_result", "module_id": f"bmotion{device_id}",
                "success": True, "message": "Update successful. Restarting service.",
                "timestamp": datetime.now(timezone.utc).isoformat()
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
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

    def handle_sw_rollback(self, device_id, payload):
        # TODO: Additional update logic placeholder
        pass

    def handle_sw_version_request(self, device_id):
        try:
            version_value = 'unknown'
            try:
                result = subprocess.run(["git", "rev-parse", "HEAD"], cwd="/opt/bmtl-device", capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    version_value = result.stdout.strip()
            except Exception as exc:
                self.logger.warning(f"Failed to get git commit hash: {exc}")

            response = {'version': version_value}
            self._publish(f"bmtl/response/sw-version/{device_id}", response)
            self.logger.info(f"Sent SW version response: {version_value}")
        except Exception as exc:
            self.logger.error(f"Error handling SW version request: {exc}")


    def send_health_status(self, device_id):
        """
        Summarise recent capture information for diagnostics.
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
                "timestamp": datetime.now(timezone.utc).isoformat()
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
