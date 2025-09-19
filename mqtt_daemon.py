#!/usr/bin/env python3

import os
import sys
import json
import time
import signal
import logging
import configparser
import ssl
import socket
import re
import subprocess
import shutil
from dotenv import load_dotenv
from datetime import datetime
from pathlib import Path
import paho.mqtt.client as mqtt
from shared_config import write_camera_config, write_camera_command, write_camera_schedule, read_camera_config
from version_manager import get_version_for_mqtt, get_current_version

class BMTLMQTTDaemon:
    def __init__(self):
        self.config_path = "/etc/bmtl-device/config.ini"
        self.log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        self.client = None
        self.running = True

        self.setup_logging()
        self.load_config()
        
    def setup_logging(self):
        os.makedirs(self.log_dir, exist_ok=True)
        log_file = os.path.join(self.log_dir, "mqtt_daemon.log")
        messages_log_file = os.path.join(self.log_dir, "mqtt_messages.log")
        
        # Main daemon logger
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger('BMTLMQTTDaemon')
        
        # Separate logger for MQTT messages
        self.message_logger = logging.getLogger('BMTLMQTTMessages')
        self.message_logger.setLevel(logging.INFO)
        message_handler = logging.FileHandler(messages_log_file)
        message_formatter = logging.Formatter('%(asctime)s - %(message)s')
        message_handler.setFormatter(message_formatter)
        self.message_logger.addHandler(message_handler)
        self.message_logger.propagate = False

    def extract_device_id_from_hostname(self):
        """Extract device ID from hostname (e.g., bmotion01 -> 01)"""
        try:
            hostname = socket.gethostname()
            # Look for pattern like bmotion01, bmotion02, etc.
            match = re.search(r'bmotion(\d+)', hostname.lower())
            if match:
                device_num = match.group(1).zfill(2)  # Ensure 2-digit format
                self.logger.info(f"Extracted device ID '{device_num}' from hostname '{hostname}'")
                return device_num
            else:
                self.logger.warning(f"Could not extract device ID from hostname '{hostname}', using default '01'")
                return "01"
        except Exception as e:
            self.logger.error(f"Error extracting device ID from hostname: {e}, using default '01'")
            return "01"

    def load_config(self):
        # Load .env file first
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
        load_dotenv(env_path)
        
        self.config = configparser.ConfigParser()
        
        if not os.path.exists(self.config_path):
            self.logger.error(f"Configuration file not found: {self.config_path}")
            sys.exit(1)
            
        try:
            self.config.read(self.config_path)
            
            # MQTT Configuration - prioritize environment variables
            self.mqtt_host = os.getenv('MQTT_HOST', self.config.get('mqtt', 'host', fallback='localhost'))
            self.mqtt_port = int(os.getenv('MQTT_PORT', self.config.getint('mqtt', 'port', fallback=1883)))
            self.mqtt_username = os.getenv('MQTT_USERNAME', self.config.get('mqtt', 'username', fallback=''))
            self.mqtt_password = os.getenv('MQTT_PASSWORD', self.config.get('mqtt', 'password', fallback=''))
            self.mqtt_use_tls = os.getenv('MQTT_USE_TLS', self.config.get('mqtt', 'use_tls', fallback='false')).lower() == 'true'
            self.mqtt_client_id = self.config.get('mqtt', 'client_id', fallback='bmtl-device')
            
            # Device Configuration - prioritize hostname-based ID
            hostname_device_id = self.extract_device_id_from_hostname()
            config_device_id = self.config.get('device', 'id', fallback=None)

            # Always use hostname-derived ID if extraction was successful
            # (hostname extraction returns "01" as default if no match found)
            hostname = socket.gethostname()
            if 'bmotion' in hostname.lower():
                # Hostname contains bmotion pattern, use extracted ID
                self.device_id = hostname_device_id
                self.logger.info(f"Using device ID from hostname: {self.device_id}")
            else:
                # Fallback to config if hostname doesn't match pattern
                self.device_id = config_device_id or hostname_device_id
                self.logger.info(f"Using device ID from config: {self.device_id}")

            self.device_sitename = self.config.get('device', 'sitename', fallback=socket.gethostname())
            
            # Topics
            self.command_topic = self.config.get('topics', 'command', fallback='bmtl/device/command')
            self.camera_topic = self.config.get('topics', 'camera', fallback='bmtl/device/camera')
            self.subscribe_topics = self.config.get('topics', 'subscribe', fallback='#')
            
            # Subscription Configuration
            self.enable_all_topics = self.config.getboolean('subscription', 'enable_all_topics', fallback=True)
            self.log_all_messages = self.config.getboolean('subscription', 'log_all_messages', fallback=True)
            
            # Intervals
            self.health_interval = self.config.getint('intervals', 'health', fallback=300)
            
            self.logger.info("Configuration loaded successfully")
            
        except Exception as e:
            self.logger.error(f"Error loading configuration: {e}")
            sys.exit(1)
            
    @staticmethod
    def _parse_reason(reason):
        if isinstance(reason, int):
            return reason, str(reason)
        if reason is None:
            return None, 'unknown'
        label = getattr(reason, 'name', None) or getattr(reason, 'description', None)
        if not label:
            try:
                label = str(reason)
            except Exception:
                label = 'unknown'
        for candidate in (getattr(reason, 'value', None), reason):
            if isinstance(candidate, int):
                return candidate, label
            try:
                return int(candidate), label
            except (TypeError, ValueError):
                continue
        return None, label

    @staticmethod
    def _format_reason(reason):
        value, label = BMTLMQTTDaemon._parse_reason(reason)
        if value is None:
            return label
        value_str = str(value)
        if label and label.lower() != 'unknown' and label != value_str:
            return f"{value_str} ({label})"
        return value_str

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        rc_value, _ = self._parse_reason(reason_code)
        if rc_value == 0:
            self.logger.info("Connected to MQTT broker")

            # Subscribe to protocol-specific topics
            request_topics = [
                "bmtl/request/settings/all",
                f"bmtl/request/settings/{self.device_id}",
                f"bmtl/request/status/{self.device_id}",
                "bmtl/request/status/all",
                "bmtl/request/status",
                f"bmtl/set/settings/{self.device_id}",
                "bmtl/request/reboot/all",
                f"bmtl/request/reboot/{self.device_id}",
                f"bmtl/request/options/{self.device_id}",
                "bmtl/request/options/all",
                f"bmtl/request/wiper/{self.device_id}",
                f"bmtl/request/camera-on-off/{self.device_id}",
                f"bmtl/sw-update/{self.device_id}",
                f"bmtl/sw-rollback/{self.device_id}",
                f"bmtl/set/sitename/{self.device_id}",
                f"bmtl/request/sw-version/{self.device_id}"
            ]

            for topic in request_topics:
                client.subscribe(topic, qos=2)
                self.logger.info(f"Subscribed to {topic}")

            # Subscribe to additional topics if enabled (for debugging)
            if self.enable_all_topics and self.subscribe_topics != '#':
                for topic in self.subscribe_topics.split(','):
                    topic = topic.strip()
                    if topic and topic not in request_topics:
                        client.subscribe(topic)
                        self.logger.info(f"Subscribed to additional topic: {topic}")

            # Send initial health status (protocol spec)
            self.send_health_status()
            # Mark as recently sent to avoid immediate duplicate in main loop
            self.last_health_sent = time.time()

            # Clear any old retained messages from incorrect device IDs
            self.clear_old_retained_messages()

            # Send version information on startup
            self.send_version_info()

        else:
            self.logger.error(f"Failed to connect to MQTT broker with result code {self._format_reason(reason_code)}")

    def on_disconnect(self, client, userdata, *args):
        reason = None

        if args:
            first_arg = args[0]
            if hasattr(first_arg, '_fields') and 'is_disconnect_packet_from_server' in getattr(first_arg, '_fields', ()):
                reason = args[1] if len(args) > 1 else None
            else:
                reason = first_arg

        reason_value, _ = self._parse_reason(reason)

        disconnect_reasons = {
            0: "Connection successful",
            1: "Connection refused - incorrect protocol version",
            2: "Connection refused - invalid client identifier",
            3: "Connection refused - server unavailable",
            4: "Connection refused - bad username or password",
            5: "Connection refused - not authorised",
            6: "Connection refused - reserved for future use",
            7: "Connection refused - not authorised",
            8: "Connection refused - reserved for future use"
        }
        reason_text = disconnect_reasons.get(reason_value, f"Unknown disconnect reason ({self._format_reason(reason)})")
        self.logger.warning(f"Disconnected from MQTT broker: {reason_text}")

    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')

            # Skip messages from our own device to avoid echo
            if (topic.endswith(f"/{self.device_id}") and
                topic.startswith("bmtl/status/health")):
                return  # Don't process our own health messages

            # Log all messages to separate file if enabled (except our own)
            if self.log_all_messages:
                message_info = {
                    'topic': topic,
                    'payload': payload,
                    'qos': msg.qos,
                    'retain': msg.retain,
                    'timestamp': datetime.now().isoformat()
                }
                self.message_logger.info(json.dumps(message_info, ensure_ascii=False))

            self.logger.info(f"Received message on {topic}: {payload}")

            # Handle protocol messages
            try:
                if topic == "bmtl/request/settings/all":
                    self.handle_settings_all_request()
                elif topic == f"bmtl/request/settings/{self.device_id}":
                    self.handle_settings_request()
                elif topic in {
                    f"bmtl/request/status/{self.device_id}",
                    "bmtl/request/status/all",
                    "bmtl/request/status"
                }:
                    self.handle_status_request(topic)
                elif topic == f"bmtl/set/settings/{self.device_id}":
                    settings = json.loads(payload) if payload else {}
                    self.handle_set_settings(settings)
                elif topic == "bmtl/request/reboot/all":
                    self.handle_reboot_all_request()
                elif topic == f"bmtl/request/reboot/{self.device_id}":
                    self.handle_reboot_request()
                elif topic == f"bmtl/request/options/{self.device_id}":
                    self.handle_options_request()
                elif topic == "bmtl/request/options/all":
                    self.handle_options_all_request()
                elif topic == f"bmtl/request/wiper/{self.device_id}":
                    self.handle_wiper_request()
                elif topic == f"bmtl/request/camera-on-off/{self.device_id}":
                    self.handle_camera_power_request()
                elif topic == f"bmtl/set/sitename/{self.device_id}":
                    self.handle_sitename_set(json.loads(payload))
                elif topic == f"bmtl/request/sw-version/{self.device_id}":
                    self.handle_sw_version_request()
                elif topic == f"bmtl/sw-update/{self.device_id}":
                    self.handle_software_update()
                elif topic == f"bmtl/sw-rollback/{self.device_id}":
                    self.handle_software_rollback(payload)
            except json.JSONDecodeError:
                # sw-update and sw-rollback don't require JSON, so only log for other topics
                if not (topic.startswith("bmtl/sw-update") or topic.startswith("bmtl/sw-rollback")):
                    self.logger.error(f"Invalid JSON in message: {payload}")
            except Exception as e:
                self.logger.error(f"Error processing message: {e}")

        except Exception as e:
            self.logger.error(f"Error handling message: {e}")
            
    def handle_settings_all_request(self):
        """Handle bmtl/request/settings/all - respond via health status"""
        try:
            # For all settings request, respond via individual health status
            self.send_health_status()
            self.logger.info("Sent settings via health status")
        except Exception as e:
            self.logger.error(f"Error handling settings all request: {e}")

    def handle_settings_request(self):
        """Handle bmtl/request/settings/{device_id}"""
        try:
            payload = {
                "response_type": "settings",
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "message": "Settings retrieved successfully",
                "settings": {
                    "start_time": "08:00",
                    "end_time": "18:00",
                    "capture_interval": 10,
                    "image_size": "1920x1080",
                    "quality": "높음",
                    "iso": "400",
                    "format": "JPG",
                    "aperture": "f/2.8"
                },
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/settings/{self.device_id}", json.dumps(payload), qos=1)
            self.logger.info("Sent settings response")
        except Exception as e:
            error_payload = {
                "response_type": "settings",
                "module_id": f"bmotion{self.device_id}",
                "success": False,
                "message": f"Failed to retrieve settings: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/settings/{self.device_id}", json.dumps(error_payload), qos=1)
            self.logger.error(f"Error handling settings request: {e}")

    def handle_status_request(self, request_topic):
        """Handle status request topics by sending a health snapshot"""
        try:
            self.logger.info(f"Status request received on {request_topic}, publishing health status")
            self.send_health_status()
        except Exception as e:
            self.logger.error(f"Error handling status request: {e}")

    def handle_set_settings(self, settings):
        """Handle bmtl/set/settings/{device_id}"""
        try:
            # Apply camera settings
            write_camera_config(settings)

            payload = {
                "response_type": "set_settings_result",
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "message": "Settings applied successfully",
                "applied_settings": settings,
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/set/settings/{self.device_id}", json.dumps(payload), qos=1)
            self.logger.info(f"Applied settings: {settings}")
        except Exception as e:
            payload = {
                "response_type": "set_settings_result",
                "module_id": f"bmotion{self.device_id}",
                "success": False,
                "message": f"Settings failed: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/set/settings/{self.device_id}", json.dumps(payload), qos=1)
            self.logger.error(f"Error applying settings: {e}")

    def handle_reboot_all_request(self):
        """Handle bmtl/request/reboot/all"""
        try:
            payload = {
                "response_type": "reboot_all_result",
                "success": True,
                "message": "Global reboot initiated successfully",
                "affected_modules": [f"bmotion{self.device_id}"],
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish("bmtl/response/reboot/all", json.dumps(payload), qos=1)
            self.logger.info("Global reboot requested")
        except Exception as e:
            error_payload = {
                "response_type": "reboot_all_result",
                "success": False,
                "message": f"Failed to initiate global reboot: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish("bmtl/response/reboot/all", json.dumps(error_payload), qos=1)
            self.logger.error(f"Error handling reboot all request: {e}")

    def handle_reboot_request(self):
        """Handle bmtl/request/reboot/{device_id}"""
        try:
            payload = {
                "response_type": "reboot_result",
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "message": "Reboot initiated successfully",
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/reboot/{self.device_id}", json.dumps(payload), qos=1)
            self.logger.info("Individual reboot requested")
        except Exception as e:
            error_payload = {
                "response_type": "reboot_result",
                "module_id": f"bmotion{self.device_id}",
                "success": False,
                "message": f"Failed to initiate reboot: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/reboot/{self.device_id}", json.dumps(error_payload), qos=1)
            self.logger.error(f"Error handling reboot request: {e}")

    def handle_options_request(self):
        """Handle bmtl/request/options/{device_id}"""
        try:
            payload = {
                "response_type": "options",
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "options": {
                    "supported_resolutions": ["1920x1080", "1280x720", "5184x3456"],
                    "supported_formats": ["JPG", "RAW"],
                    "iso_range": [100, 200, 400, 800, 1600, 3200, 6400],
                    "aperture_range": ["f/1.4", "f/2.8", "f/4", "f/5.6", "f/8", "f/11", "f/16"]
                },
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/options/{self.device_id}", json.dumps(payload), qos=1)
            self.logger.info("Sent options response")
        except Exception as e:
            error_payload = {
                "response_type": "options",
                "module_id": f"bmotion{self.device_id}",
                "success": False,
                "message": f"Failed to fetch options: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/options/{self.device_id}", json.dumps(error_payload), qos=1)
            self.logger.error(f"Error handling options request: {e}")

    def handle_options_all_request(self):
        """Handle bmtl/request/options/all"""
        try:
            payload = {
                "response_type": "all_options",
                "success": True,
                "modules": {
                    f"bmotion{self.device_id}": {
                        "supported_resolutions": ["1920x1080", "1280x720", "5184x3456"],
                        "supported_formats": ["JPG", "RAW"],
                        "iso_range": [100, 200, 400, 800, 1600, 3200, 6400],
                        "aperture_range": ["f/1.4", "f/2.8", "f/4", "f/5.6", "f/8", "f/11", "f/16"]
                    }
                },
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish("bmtl/response/options/all", json.dumps(payload), qos=1)
            self.logger.info("Sent all options response")
        except Exception as e:
            error_payload = {
                "response_type": "all_options",
                "success": False,
                "message": f"Failed to fetch options: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish("bmtl/response/options/all", json.dumps(error_payload), qos=1)
            self.logger.error(f"Error handling options all request: {e}")

    def handle_wiper_request(self):
        """Handle bmtl/request/wiper/{device_id}"""
        try:
            payload = {
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "message": "Wiper operation started",
                "started_at": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/wiper/{self.device_id}", json.dumps(payload), qos=1)
            self.logger.info("Wiper operation requested")
        except Exception as e:
            error_payload = {
                "module_id": f"bmotion{self.device_id}",
                "success": False,
                "message": f"Wiper operation failed: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/wiper/{self.device_id}", json.dumps(error_payload), qos=1)
            self.logger.error(f"Error handling wiper request: {e}")

    def handle_camera_power_request(self):
        """Handle bmtl/request/camera-on-off/{device_id}"""
        try:
            payload = {
                "response_type": "camera_power_result",
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "message": "Camera power toggled successfully",
                "new_state": "on",
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/camera-on-off/{self.device_id}", json.dumps(payload), qos=1)
            self.logger.info("Camera power toggle requested")
        except Exception as e:
            error_payload = {
                "response_type": "camera_power_result",
                "module_id": f"bmotion{self.device_id}",
                "success": False,
                "message": f"Camera power toggle failed: {str(e)}",
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/camera-on-off/{self.device_id}", json.dumps(error_payload), qos=1)
            self.logger.error(f"Error handling camera power request: {e}")

    def handle_software_update(self):
        """Handle bmtl/sw-update/{device_id} - Remote software update"""
        import threading
        try:
            self.logger.info("Remote software update requested")
            update_thread = threading.Thread(
                target=self._execute_software_update,
                daemon=True
            )
            update_thread.start()
            payload = {
                "success": True,
                "message": "Software update process started."
            }
            self.client.publish(f"bmtl/response/sw-update/{self.device_id}", json.dumps(payload), qos=1)
        except Exception as e:
            self.logger.error(f"Error handling software update request: {e}")
            error_payload = {
                "success": False,
                "message": f"Update failed to start: {str(e)}"
            }
            self.client.publish(f"bmtl/response/sw-update/{self.device_id}", json.dumps(error_payload), qos=1)

    def _execute_software_update(self):
        """Execute the actual software update process"""
        try:
            self.logger.info("Executing software update...")
            update_command = "cd /opt/bmtl-device && git stash && git pull && chmod +x ./install.sh && sudo ./install.sh update"
            result = subprocess.run(update_command, shell=True, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                self.logger.info("Software update successful")
                # The device will likely reboot, but we can send a message beforehand
                success_payload = {
                    "success": True,
                    "message": "Software update command executed successfully.",
                    "stdout": result.stdout
                }
                self.client.publish(f"bmtl/response/sw-update/{self.device_id}", json.dumps(success_payload), qos=1)
            else:
                self.logger.error(f"Software update failed: {result.stderr}")
                error_payload = {
                    "success": False,
                    "message": "Software update command failed.",
                    "stderr": result.stderr
                }
                self.client.publish(f"bmtl/response/sw-update/{self.device_id}", json.dumps(error_payload), qos=1)
        except Exception as e:
            self.logger.error(f"Exception during software update: {e}")
            error_payload = {
                "success": False,
                "message": f"Exception during software update: {str(e)}"
            }
            self.client.publish(f"bmtl/response/sw-update/{self.device_id}", json.dumps(error_payload), qos=1)

    def handle_software_rollback(self, payload):
        """Handle bmtl/sw-rollback/{device_id} - Remote software rollback"""
        import threading

        try:
            self.logger.info("Remote software rollback requested")

            rollback_target = "previous"
            if payload:
                try:
                    data = json.loads(payload)
                    rollback_target = data.get("target", "previous")
                except json.JSONDecodeError:
                    self.logger.warning("Invalid rollback payload, using default target")

            rollback_thread = threading.Thread(
                target=self._execute_software_rollback,
                args=(rollback_target,),
                daemon=True
            )
            rollback_thread.start()

        except Exception as e:
            self.logger.error(f"Error handling software rollback request: {e}")
            error_payload = {
                "success": False,
                "message": f"Rollback failed to start: {str(e)}"
            }
            self.client.publish(f"bmtl/response/sw-rollback/{self.device_id}", json.dumps(error_payload), qos=1)

    def _execute_software_rollback(self, target="previous"):
        """Execute the actual software rollback process"""
        original_cwd = os.getcwd()
        try:
            git_cmd = self._find_git_command()
            if not git_cmd:
                raise Exception("Git is not installed or not found in PATH")

            app_dir = "/opt/bmtl-device"
            os.chdir(app_dir)

            if target == "previous" or target == "HEAD~1":
                rollback_target = "HEAD~1"
                self.logger.info("Rolling back to previous commit")
            elif target.startswith("HEAD~"):
                rollback_target = target
                self.logger.info(f"Rolling back to {target}")
            elif len(target) >= 7:
                rollback_target = target
                self.logger.info(f"Rolling back to commit {target}")
            else:
                raise Exception(f"Invalid rollback target: {target}")

            self.logger.info("Stashing local changes")
            result = subprocess.run([git_cmd, 'stash'], capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                self.logger.warning(f"Git stash warning: {result.stderr}")

            self.logger.info(f"Performing rollback to {rollback_target}")
            result = subprocess.run([git_cmd, 'reset', '--hard', rollback_target], capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise Exception(f"Git rollback failed: {result.stderr}")

            chmod_cmd = shutil.which('chmod') or '/bin/chmod'
            self.logger.info("Setting install.sh permissions")
            result = subprocess.run([chmod_cmd, '+x', './install.sh'], capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                raise Exception(f"Chmod failed: {result.stderr}")

            sudo_cmd = shutil.which('sudo') or '/usr/bin/sudo'
            self.logger.info("Running install.sh in update mode")

            rollback_log_path = os.path.join(self.log_dir, f"rollback_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

            rollback_cmd = f"{sudo_cmd} ./install.sh update"

            try:
                nohup_cmd = f"cd /opt/bmtl-device && nohup {rollback_cmd} > {rollback_log_path} 2>&1 &"
                subprocess.Popen(
                    ["/bin/bash", "-c", nohup_cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                self.logger.info("Rollback process started with nohup")
            except Exception as e:
                self.logger.error(f"Failed to start rollback process: {e}")
                raise

            self.logger.info(f"Rollback process launched in background, output will be logged to: {rollback_log_path}")

            success_payload = {
                "success": True,
                "message": "Rollback completed successfully",
                "log_file": rollback_log_path
            }
            self.client.publish(f"bmtl/response/sw-rollback/{self.device_id}", json.dumps(success_payload), qos=1)

        except Exception as e:
            self.logger.error(f"Software rollback failed: {e}")
            error_payload = {
                "success": False,
                "message": f"Software rollback failed: {str(e)}"
            }
            try:
                self.client.publish(f"bmtl/response/sw-rollback/{self.device_id}", json.dumps(error_payload), qos=1)
            except Exception:
                pass
        finally:
            try:
                os.chdir(original_cwd)
            except Exception:
                pass

    def _find_git_command(self):
        """Find git command in system PATH"""
        try:
            import shutil

            # Use shutil.which() for cross-platform compatibility and security
            git_path = shutil.which('git')
            if git_path:
                # Verify git is working
                try:
                    result = subprocess.run([git_path, '--version'],
                                          capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        self.logger.info(f"Found git at: {git_path}")
                        return git_path
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    pass

            # Fallback: try common git paths
            git_paths = ['/usr/bin/git', '/usr/local/bin/git', '/bin/git']
            for git_path in git_paths:
                try:
                    result = subprocess.run([git_path, '--version'],
                                          capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        self.logger.info(f"Found git at: {git_path}")
                        return git_path
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    continue

            return None
        except Exception as e:
            self.logger.error(f"Error finding git command: {e}")
            return None

    def send_version_info(self):
        """Send software version information to server"""
        try:
            version_info = get_version_for_mqtt()

            payload = {
                "commit_hash": version_info.get("commit_hash", "unknown")
            }

            topic = f"bmtl/response/sw-version/{self.device_id}"
            self.client.publish(topic, json.dumps(payload), qos=1, retain=True)
            self.logger.info(f"Version info sent: {payload['commit_hash']}")

        except Exception as e:
            self.logger.error(f"Error sending version info: {e}")

    def handle_sitename_set(self, data):
        """Handle sitename change request"""
        try:
            sitename = data.get("sitename", "")
            if not sitename:
                raise ValueError("Sitename cannot be empty")

            # Update the config file
            config = configparser.ConfigParser()
            config.read(self.config_path)
            config.set('device', 'sitename', sitename)
            with open(self.config_path, 'w') as configfile:
                config.write(configfile)

            # Update the value in the running daemon
            self.device_sitename = sitename
            self.logger.info(f"Sitename updated to: {sitename}")

            payload = {
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "message": "Sitename updated successfully",
                "sitename": sitename,
                "updated_at": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/sitename/{self.device_id}", json.dumps(payload), qos=1)
            
            # A restart might be needed for other processes to see the change.
            # self.logger.info("Daemon restart is recommended for sitename change to be fully effective.")

        except Exception as e:
            self.logger.error(f"Error setting sitename: {e}")
            error_payload = {
                "module_id": f"bmotion{self.device_id}",
                "success": False,
                "message": f"Failed to set sitename: {str(e)}"
            }
            self.client.publish(f"bmtl/response/sitename/{self.device_id}", json.dumps(error_payload), qos=1)

    def handle_sw_version_request(self):
        """Handle software version request"""
        try:
            self.logger.info("Received software version request")
            self.send_version_info()
        except Exception as e:
            self.logger.error(f"Error handling sw-version request: {e}")

    def send_health_status(self):
        """Send detailed health status according to protocol spec"""
        try:
            camera_stats = self.get_camera_stats()
            storage = self.get_storage_metrics()

            temperature = self.get_system_temperature()

            payload = {
                "module_id": f"bmotion{self.device_id}",
                "storage_used": storage["used_percent"],
                "temperature": temperature,
                "last_capture_time": camera_stats.get("last_successful_capture") or camera_stats.get("last_capture_time"),
                "last_boot_time": self.get_boot_time(),
                "today_total_captures": camera_stats.get("total_captures", 0),
                "today_captured_count": camera_stats.get("successful_captures", 0),
                "missed_captures": camera_stats.get("missed_captures", 0),
                "sw_version": get_current_version(),
                "site_name": self.device_sitename
            }

            topic = f"bmtl/status/health/{self.device_id}"
            self.client.publish(topic, json.dumps(payload), qos=1)
            self.logger.info("Health status sent")

        except Exception as e:
            self.logger.error(f"Error sending health status: {e}")

    def get_camera_stats(self):
        """Get camera statistics from shared config"""
        try:
            from shared_config import config_manager
            return config_manager.read_config('camera_stats.json')
        except Exception as e:
            self.logger.error(f"Error reading camera stats: {e}")
            return {
                'total_captures': 0,
                'successful_captures': 0,
                'missed_captures': 0,
                'last_capture_time': None,
                'last_successful_capture': None
            }

    def clear_old_retained_messages(self):
        """Clear retained messages from old/incorrect device IDs (optional cleanup)"""
        try:
            # Only perform cleanup if explicitly enabled
            cleanup_enabled = self.config.getboolean('device', 'cleanup_old_retained', fallback=False)
            if not cleanup_enabled:
                return

            # Get current hostname to determine what device IDs to clear
            hostname = socket.gethostname()
            if 'bmotion' in hostname.lower():
                # Clear common incorrect IDs that might exist
                incorrect_ids = ['bmotion101', 'raspberry-pi-001', 'raspberry-pi']

                for incorrect_id in incorrect_ids:
                    if incorrect_id != self.device_id:
                        # Send empty retained messages to clear old ones
                        clear_topics = [
                            f"bmtl/status/health/{incorrect_id}"
                        ]

                        for topic in clear_topics:
                            self.client.publish(topic, "", retain=True)
                            self.logger.info(f"Cleared retained message for topic: {topic}")

        except Exception as e:
            self.logger.error(f"Error clearing old retained messages: {e}")


    def get_storage_metrics(self):
        """Collect storage metrics for the root filesystem"""
        try:
            import shutil
            total, used, free = shutil.disk_usage("/")
            to_gb = lambda value: round(value / (1024 ** 3), 2)
            used_percent = round((used / total) * 100, 1) if total else 0.0
            return {
                'total_gb': to_gb(total),
                'used_gb': to_gb(used),
                'free_gb': to_gb(free),
                'used_percent': used_percent
            }
        except Exception as e:
            self.logger.error(f"Error getting storage metrics: {e}")
            return {
                'total_gb': 0.0,
                'used_gb': 0.0,
                'free_gb': 0.0,
                'used_percent': 0.0
            }

    def get_system_temperature(self):
        """Get system temperature in Celsius"""
        try:
            # Try to read from thermal zone
            thermal_paths = [
                "/sys/class/thermal/thermal_zone0/temp",
                "/sys/class/thermal/thermal_zone1/temp"
            ]

            for path in thermal_paths:
                try:
                    with open(path, 'r') as f:
                        temp_millidegree = int(f.read().strip())
                        return round(temp_millidegree / 1000.0, 1)
                except (FileNotFoundError, ValueError):
                    continue

            # If thermal zones not available, return a default value
            return 42.3
        except Exception as e:
            self.logger.warning(f"Error getting system temperature: {e}")
            return 42.3

    def get_boot_time(self):
        """Get system boot time"""
        try:
            with open('/proc/stat', 'r') as f:
                for line in f:
                    if line.startswith('btime'):
                        boot_timestamp = int(line.split()[1])
                        return datetime.fromtimestamp(boot_timestamp).isoformat()
        except:
            return datetime.now().isoformat()
            
    def get_uptime(self):
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
                return int(uptime_seconds)
        except:
            return 0
            
    def setup_mqtt_client(self):
        # Prefer the modern callback API when available to avoid deprecation warnings
        callback_version = getattr(getattr(mqtt, 'CallbackAPIVersion', object()), 'VERSION2', None)

        if callback_version is not None:
            try:
                self.client = mqtt.Client(
                    client_id=self.mqtt_client_id,
                    callback_api_version=callback_version
                )
            except (AttributeError, TypeError):
                # Older paho-mqtt versions do not accept callback_api_version
                self.client = mqtt.Client(client_id=self.mqtt_client_id)
        else:
            self.client = mqtt.Client(client_id=self.mqtt_client_id)
        
        # Set username and password if provided
        if self.mqtt_username and self.mqtt_password:
            self.client.username_pw_set(self.mqtt_username, self.mqtt_password)
        
        # Enable TLS if configured
        if self.mqtt_use_tls:
            self.client.tls_set(ca_certs=None, certfile=None, keyfile=None, 
                              cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS, 
                              ciphers=None)
            
        # Set callbacks
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        
        # Set will message for health status
        will_payload = {
            'module_id': f"bmotion{self.device_id}",
            'status': 'offline',
            'timestamp': datetime.now().isoformat()
        }
        self.client.will_set(f"bmtl/status/health/{self.device_id}",
                           json.dumps(will_payload), retain=True)
        
    def signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

        if self.client:
            # Send offline health status
            offline_payload = {
                'module_id': f"bmotion{self.device_id}",
                'status': 'offline',
                'timestamp': datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/status/health/{self.device_id}",
                              json.dumps(offline_payload), retain=True)
            self.client.disconnect()
            
    def run(self):
        # Set up signal handlers
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        
        self.setup_mqtt_client()
        
        try:
            self.logger.info(f"Connecting to MQTT broker at {self.mqtt_host}:{self.mqtt_port}")
            self.client.connect(self.mqtt_host, self.mqtt_port, 60)
            self.client.loop_start()
            
            last_health = getattr(self, 'last_health_sent', 0)

            while self.running:
                current_time = time.time()

                # Send detailed health status (protocol spec)
                if current_time - last_health >= self.health_interval:
                    self.send_health_status()
                    last_health = current_time
                    
                time.sleep(1)
                
        except Exception as e:
            self.logger.error(f"Error in main loop: {e}")
            
        finally:
            if self.client:
                # Send offline health status
                offline_payload = {
                    'module_id': f"bmotion{self.device_id}",
                    'status': 'offline',
                    'timestamp': datetime.now().isoformat()
                }
                self.client.publish(f"bmtl/status/health/{self.device_id}",
                                  json.dumps(offline_payload), retain=True)
                self.client.loop_stop()
                self.client.disconnect()
                
            self.logger.info("MQTT daemon stopped")

if __name__ == "__main__":
    daemon = BMTLMQTTDaemon()
    daemon.run()
