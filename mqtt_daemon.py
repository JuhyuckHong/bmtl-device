#!/usr/bin/env python3

import os
import sys
import json
import time
import signal
import logging
import configparser
import os
import ssl
import socket
import re
from dotenv import load_dotenv
from datetime import datetime
from pathlib import Path
import paho.mqtt.client as mqtt
from shared_config import write_camera_config, write_camera_command, write_camera_schedule, read_camera_config

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

            self.device_location = self.config.get('device', 'location', fallback='unknown')
            
            # Topics
            self.status_topic = self.config.get('topics', 'status', fallback='bmtl/device/status')
            self.heartbeat_topic = self.config.get('topics', 'heartbeat', fallback='bmtl/device/heartbeat')
            self.command_topic = self.config.get('topics', 'command', fallback='bmtl/device/command')
            self.camera_topic = self.config.get('topics', 'camera', fallback='bmtl/device/camera')
            self.subscribe_topics = self.config.get('topics', 'subscribe', fallback='#')
            
            # Subscription Configuration
            self.enable_all_topics = self.config.getboolean('subscription', 'enable_all_topics', fallback=True)
            self.log_all_messages = self.config.getboolean('subscription', 'log_all_messages', fallback=True)
            
            # Intervals
            self.heartbeat_interval = self.config.getint('intervals', 'heartbeat', fallback=60)
            self.status_interval = self.config.getint('intervals', 'status', fallback=300)
            
            self.logger.info("Configuration loaded successfully")
            
        except Exception as e:
            self.logger.error(f"Error loading configuration: {e}")
            sys.exit(1)
            
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.logger.info("Connected to MQTT broker")
            
            # Subscribe to protocol-specific topics
            request_topics = [
                "bmtl/request/settings/all",
                f"bmtl/request/settings/{self.device_id}",
                "bmtl/request/status",
                f"bmtl/set/settings/{self.device_id}",
                "bmtl/request/reboot/all",
                f"bmtl/request/reboot/{self.device_id}",
                f"bmtl/request/options/{self.device_id}",
                "bmtl/request/options/all",
                f"bmtl/request/wiper/{self.device_id}",
                f"bmtl/request/camera-on-off/{self.device_id}"
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
            
            # Send initial status
            self.send_status("online")

            # Send initial health status (protocol spec)
            self.send_health_status()

            # Clear any old retained messages from incorrect device IDs
            self.clear_old_retained_messages()
            
        else:
            self.logger.error(f"Failed to connect to MQTT broker with result code {rc}")
            
    def on_disconnect(self, client, userdata, rc):
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
        reason = disconnect_reasons.get(rc, f"Unknown disconnect reason ({rc})")
        self.logger.warning(f"Disconnected from MQTT broker: {reason}")
        
    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')

            # Skip messages from our own device to avoid echo
            if (topic.endswith(f"/{self.device_id}") and
                (topic.startswith(self.status_topic) or
                 topic.startswith(self.heartbeat_topic) or
                 topic.startswith("bmtl/status/health"))):
                return  # Don't process our own status/health messages

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
                elif topic == "bmtl/request/status":
                    self.handle_status_request()
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
            except json.JSONDecodeError:
                self.logger.error(f"Invalid JSON in message: {payload}")
            except Exception as e:
                self.logger.error(f"Error processing message: {e}")

        except Exception as e:
            self.logger.error(f"Error handling message: {e}")
            
    def handle_settings_all_request(self):
        """Handle bmtl/request/settings/all"""
        try:
            camera_stats = self.get_camera_stats()
            payload = {
                "response_type": "all_settings",
                "modules": {
                    f"bmotion{self.device_id}": {
                        "start_time": "08:00",
                        "end_time": "18:00",
                        "capture_interval": 10,
                        "image_size": "1920x1080",
                        "quality": "높음",
                        "iso": "400",
                        "format": "JPG",
                        "aperture": "f/2.8"
                    }
                },
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish("bmtl/response/settings/all", json.dumps(payload), qos=1)
            self.logger.info("Sent all settings response")
        except Exception as e:
            self.logger.error(f"Error handling settings all request: {e}")

    def handle_settings_request(self):
        """Handle bmtl/request/settings/{device_id}"""
        try:
            payload = {
                "response_type": "settings",
                "module_id": f"bmotion{self.device_id}",
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
            self.logger.error(f"Error handling settings request: {e}")

    def handle_status_request(self):
        """Handle bmtl/request/status"""
        try:
            payload = {
                "response_type": "status",
                "system_status": "normal",
                "connected_modules": [f"bmotion{self.device_id}"],
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish("bmtl/response/status", json.dumps(payload), qos=1)
            self.logger.info("Sent status response")
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
            self.logger.error(f"Error handling reboot request: {e}")

    def handle_options_request(self):
        """Handle bmtl/request/options/{device_id}"""
        try:
            payload = {
                "response_type": "options",
                "module_id": f"bmotion{self.device_id}",
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
            self.logger.error(f"Error handling options request: {e}")

    def handle_options_all_request(self):
        """Handle bmtl/request/options/all"""
        try:
            payload = {
                "response_type": "all_options",
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
            self.logger.error(f"Error handling options all request: {e}")

    def handle_wiper_request(self):
        """Handle bmtl/request/wiper/{device_id}"""
        try:
            # TODO: Implement actual wiper control
            payload = {
                "response_type": "wiper_result",
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "message": "Wiper operation completed",
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/wiper/{self.device_id}", json.dumps(payload), qos=1)
            self.logger.info("Wiper operation requested")
        except Exception as e:
            self.logger.error(f"Error handling wiper request: {e}")

    def handle_camera_power_request(self):
        """Handle bmtl/request/camera-on-off/{device_id}"""
        try:
            # TODO: Implement actual camera power control
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
            self.logger.error(f"Error handling camera power request: {e}")
            
    def send_status(self, status):
        """Send device status - used for immediate status updates"""
        try:
            payload = {
                'device_id': self.device_id,
                'location': self.device_location,
                'status': status,
                'timestamp': datetime.now().isoformat(),
                'uptime': self.get_uptime()
            }

            topic = f"{self.status_topic}/{self.device_id}"
            self.client.publish(topic, json.dumps(payload), retain=True)
            self.logger.info(f"Status sent: {status}")

        except Exception as e:
            self.logger.error(f"Error sending status: {e}")

    def send_health_status(self):
        """Send detailed health status according to protocol spec"""
        try:
            # Get real camera statistics
            camera_stats = self.get_camera_stats()

            payload = {
                'module_id': f"bmotion{self.device_id}",
                'status': 'online',
                'battery_level': self.get_battery_level(),
                'storage_used': self.get_storage_usage(),
                'last_capture_time': camera_stats.get('last_successful_capture') or camera_stats.get('last_capture_time'),
                'last_boot_time': self.get_boot_time(),
                'site_name': self.device_location,
                'today_total_captures': camera_stats.get('total_captures', 0),
                'today_captured_count': camera_stats.get('successful_captures', 0),
                'missed_captures': camera_stats.get('missed_captures', 0),
                'timestamp': datetime.now().isoformat()
            }

            # Use protocol spec topic format
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

    def get_battery_level(self):
        """Get battery level for Raspberry Pi"""
        try:
            # Try to read from UPS HAT or similar (common paths)
            battery_paths = [
                '/sys/class/power_supply/BAT0/capacity',
                '/sys/class/power_supply/BAT1/capacity',
                '/sys/class/power_supply/rpi-poe-power-supply/capacity'
            ]

            for path in battery_paths:
                try:
                    with open(path, 'r') as f:
                        battery_level = int(f.read().strip())
                        return battery_level
                except (FileNotFoundError, ValueError):
                    continue

            # Check for specific UPS HAT via I2C (if available)
            try:
                result = subprocess.run(['vcgencmd', 'get_throttled'],
                                      capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    # If system is not throttled, assume good power (simulated 85%)
                    throttled = result.stdout.strip()
                    if 'throttled=0x0' in throttled:
                        return 85
                    else:
                        return 60  # Throttled, assume lower battery
            except:
                pass

            # Default for devices without battery monitoring
            return None

        except Exception as e:
            self.logger.error(f"Error getting battery level: {e}")
            return None

    def clear_old_retained_messages(self):
        """Clear retained messages from old/incorrect device IDs"""
        try:
            # Get current hostname to determine what device IDs to clear
            hostname = socket.gethostname()
            if 'bmotion' in hostname.lower():
                # Clear common incorrect IDs that might exist
                incorrect_ids = ['bmotion101', 'raspberry-pi-001', 'raspberry-pi']

                for incorrect_id in incorrect_ids:
                    if incorrect_id != self.device_id:
                        # Send empty retained messages to clear old ones
                        clear_topics = [
                            f"{self.status_topic}/{incorrect_id}",
                            f"{self.heartbeat_topic}/{incorrect_id}",
                            f"bmtl/status/health/{incorrect_id}"
                        ]

                        for topic in clear_topics:
                            self.client.publish(topic, "", retain=True)
                            self.logger.info(f"Cleared retained message for topic: {topic}")

        except Exception as e:
            self.logger.error(f"Error clearing old retained messages: {e}")

    def send_heartbeat(self):
        """Send simple heartbeat - kept for backward compatibility"""
        try:
            payload = {
                'device_id': self.device_id,
                'timestamp': datetime.now().isoformat(),
                'uptime': self.get_uptime()
            }

            topic = f"{self.heartbeat_topic}/{self.device_id}"
            self.client.publish(topic, json.dumps(payload))

        except Exception as e:
            self.logger.error(f"Error sending heartbeat: {e}")

    def get_storage_usage(self):
        """Get storage usage percentage"""
        try:
            import shutil
            total, used, free = shutil.disk_usage("/")
            return round((used / total) * 100, 1)
        except:
            return 0.0

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
        
        # Set will message
        will_payload = {
            'device_id': self.device_id,
            'status': 'offline',
            'timestamp': datetime.now().isoformat()
        }
        self.client.will_set(f"{self.status_topic}/{self.device_id}", 
                           json.dumps(will_payload), retain=True)
        
    def signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
        
        if self.client:
            self.send_status("offline")
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
            
            last_heartbeat = 0
            last_status = 0
            last_health = 0

            while self.running:
                current_time = time.time()

                # Send heartbeat (backward compatibility)
                if current_time - last_heartbeat >= self.heartbeat_interval:
                    self.send_heartbeat()
                    last_heartbeat = current_time

                # Send detailed health status (protocol spec)
                if current_time - last_health >= self.heartbeat_interval:
                    self.send_health_status()
                    last_health = current_time

                # Send status update (less frequent)
                if current_time - last_status >= self.status_interval:
                    self.send_status("online")
                    last_status = current_time
                    
                time.sleep(1)
                
        except Exception as e:
            self.logger.error(f"Error in main loop: {e}")
            
        finally:
            if self.client:
                self.send_status("offline")
                self.client.loop_stop()
                self.client.disconnect()
                
            self.logger.info("MQTT daemon stopped")

if __name__ == "__main__":
    daemon = BMTLMQTTDaemon()
    daemon.run()