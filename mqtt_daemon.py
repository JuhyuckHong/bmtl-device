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
from dotenv import load_dotenv
from datetime import datetime
import paho.mqtt.client as mqtt

class MqttDaemon:
    """
    Handles MQTT connectivity tasks for the device.
    - Publishes worker responses to the MQTT broker.
    - Forwards subscribed MQTT messages to the worker process.
    """

    def __init__(self, task_queue, response_queue):
        self.task_queue = task_queue
        self.response_queue = response_queue

        self.config_path = "/etc/bmtl-device/config.ini"
        self.log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        self.client = None
        self.running = True
        self.connected = False
        self.reconnect_delay = 5  # Start with 5 seconds
        self.max_reconnect_delay = 300  # Max 5 minutes
        self.publish_ack_timeout = 5  # Seconds to wait for QoS acknowledgements
        # Initialize to current time so we don't spam immediate reconnects on startup
        self.last_disconnect_time = time.time()

        self.setup_logging()
        self.load_config()
        self.device_id = self.extract_device_id_from_hostname()

    def setup_logging(self):
        os.makedirs(self.log_dir, exist_ok=True)
        log_file = os.path.join(self.log_dir, "mqtt_daemon.log")

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger('MqttDaemon')

    def extract_device_id_from_hostname(self):
        try:
            hostname = socket.gethostname()
            match = re.search(r'bmotion(\d+)', hostname.lower())
            if match:
                device_num = match.group(1)
                self.logger.info(f"Extracted device ID '{device_num}' from hostname '{hostname}'")
                return device_num
            else:
                self.logger.warning(f"Could not extract device ID from hostname '{hostname}', using default '01'")
                return "01"
        except Exception as e:
            self.logger.error(f"Error extracting device ID from hostname: {e}, using default '01'")
            return "01"

    def load_config(self):
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
        load_dotenv(env_path)

        self.config = configparser.ConfigParser()
        if not os.path.exists(self.config_path):
            self.logger.error(f"Configuration file not found: {self.config_path}")
            sys.exit(1)

        try:
            self.config.read(self.config_path)
            self.mqtt_host = os.getenv('MQTT_HOST', self.config.get('mqtt', 'host', fallback='localhost'))
            self.mqtt_port = int(os.getenv('MQTT_PORT', self.config.getint('mqtt', 'port', fallback=1883)))
            self.mqtt_username = os.getenv('MQTT_USERNAME', self.config.get('mqtt', 'username', fallback=''))
            self.mqtt_password = os.getenv('MQTT_PASSWORD', self.config.get('mqtt', 'password', fallback=''))
            self.mqtt_use_tls = os.getenv('MQTT_USE_TLS', self.config.get('mqtt', 'use_tls', fallback='false')).lower() == 'true'
            self.mqtt_force_plain = os.getenv('MQTT_FORCE_PLAIN', self.config.get('mqtt', 'force_plain', fallback='false')).lower() == 'true'
            if not self.mqtt_use_tls and not self.mqtt_force_plain and self.mqtt_port in (8883, 8884):
                self.logger.warning(
                    "Port %s typically expects TLS; enabling TLS automatically. Set MQTT_FORCE_PLAIN=true or force_plain=true to override.",
                    self.mqtt_port,
                )
                self.mqtt_use_tls = True
            # Transport selection: 'tcp' (default) or 'websockets'
            self.mqtt_transport = os.getenv('MQTT_TRANSPORT', self.config.get('mqtt', 'transport', fallback='tcp')).lower()
            if self.mqtt_transport not in ('tcp', 'websockets'):
                self.logger.warning(f"Invalid MQTT_TRANSPORT '{self.mqtt_transport}', defaulting to 'tcp'")
                self.mqtt_transport = 'tcp'
            
            config_device_id = self.config.get('device', 'id', fallback=None)
            if config_device_id:
                self.device_id = config_device_id
            
            self.mqtt_client_id = f"bmtl-device-{self.device_id}"
            self.logger.info("MQTT configuration loaded successfully")
            if str(self.mqtt_port) in ("8884",) and self.mqtt_transport == 'tcp':
                self.logger.warning(
                    "Port 8884 is commonly used for secure WebSockets; consider setting MQTT_TRANSPORT=websockets if your broker requires WSS."
                )
        except Exception as e:
            self.logger.error(f"Error loading configuration: {e}")
            sys.exit(1)

    def on_connect(self, client, userdata, flags, reason_code=None, properties=None):
        if reason_code is None:
            reason_code = mqtt.MQTT_ERR_SUCCESS

        if self._reason_code_is_success(reason_code):
            self.connected = True
            self.reconnect_delay = 5  # Reset reconnect delay on successful connection
            self.logger.info("Connected to MQTT broker")
            topics_to_subscribe = [
                "bmtl/request/settings/all",
                f"bmtl/request/settings/{self.device_id}",
                "bmtl/request/status/all",
                f"bmtl/set/settings/{self.device_id}",
                f"bmtl/set/sitename/{self.device_id}",
                f"bmtl/sw-update/{self.device_id}",
                f"bmtl/sw-rollback/{self.device_id}",
                f"bmtl/request/sw-version/{self.device_id}",
                "bmtl/request/reboot/all",
                f"bmtl/request/reboot/{self.device_id}",
                f"bmtl/request/options/{self.device_id}",
                "bmtl/request/options/all",
                f"bmtl/request/wiper/{self.device_id}",
                f"bmtl/request/camera-on-off/{self.device_id}",
            ]
            for topic in topics_to_subscribe:
                client.subscribe(topic, qos=2)
                self.logger.info(f"Subscribed to {topic}")

            # Request initial state
            self.task_queue.put({'command': 'health_check', 'device_id': self.device_id})

            if properties:
                self.logger.debug('Connect properties: %s', properties)
        else:
            self.connected = False
            reason_text = self._format_reason_code(reason_code)
            self.logger.error(f"Failed to connect to MQTT broker with result code {reason_text}")

    def on_disconnect(self, client, userdata, disconnect_flags=None, reason_code=None, properties=None):
        self.connected = False
        self.last_disconnect_time = time.time()

        if reason_code is None and properties is None and isinstance(disconnect_flags, int):
            reason_code = disconnect_flags
            disconnect_flags = None

        from_server = getattr(disconnect_flags, 'is_disconnect_packet_from_server', None)
        reason_text = self._format_reason_code(reason_code)

        if from_server is None:
            self.logger.warning('Disconnected from MQTT broker (reason=%s)', reason_text)
        else:
            self.logger.warning(
                'Disconnected from MQTT broker (server_packet=%s, reason=%s)',
                from_server,
                reason_text,
            )

        is_failure = False
        if hasattr(reason_code, 'is_failure'):
            is_failure = reason_code.is_failure
        elif reason_code not in (None, 0, mqtt.MQTT_ERR_SUCCESS):
            is_failure = True

        if is_failure and self.running:
            self.logger.info(f'MQTT client will attempt to reconnect in {self.reconnect_delay} seconds.')

        if properties:
            self.logger.debug('Disconnect properties: %s', properties)

    def _reason_code_is_success(self, reason_code):
        if reason_code is None:
            return False

        if hasattr(reason_code, "is_failure"):
            return not reason_code.is_failure

        return reason_code in (0, mqtt.MQTT_ERR_SUCCESS)

    def _format_reason_code(self, reason_code):
        if reason_code is None:
            return 'unknown'

        if hasattr(reason_code, 'getName'):
            return reason_code.getName()

        if hasattr(reason_code, 'name'):
            return reason_code.name

        if hasattr(reason_code, 'value'):
            return str(reason_code.value)

        return str(reason_code)

    def reconnect_to_broker(self):
        """Attempt to reconnect to MQTT broker with exponential backoff"""
        if not self.running or self.connected:
            return

        current_time = time.time()
        # Respect backoff window
        if current_time - self.last_disconnect_time < self.reconnect_delay:
            return

        try:
            # Schedule next opportunity before attempting (prevents rapid loops even on non-raising failures)
            self.last_disconnect_time = current_time
            self.logger.info(f"Attempting to reconnect to MQTT broker at {self.mqtt_host}:{self.mqtt_port}")
            if self.client:
                result = self.client.reconnect()
                # If reconnect() returns an error code without raising, treat as failure for backoff
                if isinstance(result, int) and result != mqtt.MQTT_ERR_SUCCESS:
                    self.logger.warning(f"Reconnect returned non-success code: {result}")
                    self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
                    self.logger.info(f"Next reconnection attempt in {self.reconnect_delay} seconds")
            else:
                self.setup_mqtt_client()
                self.client.connect(self.mqtt_host, self.mqtt_port, 120)

        except Exception as e:
            self.logger.error(f"Reconnection attempt failed: {e}")
            # Exponential backoff with jitter
            self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
            self.logger.info(f"Next reconnection attempt in {self.reconnect_delay} seconds")
            self.last_disconnect_time = current_time

    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            self.logger.info(f"Received message on {topic}")

            task = {'payload': payload, 'device_id': self.device_id}
            
            if topic == "bmtl/request/settings/all":
                task['command'] = 'settings_request_all'
            elif topic == f"bmtl/request/settings/{self.device_id}":
                task['command'] = 'settings_request_individual'
            elif topic == "bmtl/request/status/all":
                task['command'] = 'status_request'
            elif topic == f"bmtl/set/settings/{self.device_id}":
                task['command'] = 'settings_change'
            elif topic == f"bmtl/set/sitename/{self.device_id}":
                task['command'] = 'set_sitename'
            elif topic == f"bmtl/sw-update/{self.device_id}":
                task['command'] = 'sw_update'
            elif topic == f"bmtl/sw-rollback/{self.device_id}":
                task['command'] = 'sw_rollback'
            elif topic == f"bmtl/request/sw-version/{self.device_id}":
                task['command'] = 'sw_version_request'
            elif topic == "bmtl/request/reboot/all":
                task['command'] = 'reboot_all'
            elif topic == f"bmtl/request/reboot/{self.device_id}":
                task['command'] = 'reboot_individual'
            elif topic == f"bmtl/request/options/{self.device_id}":
                task['command'] = 'options_request_individual'
            elif topic == "bmtl/request/options/all":
                task['command'] = 'options_request_all'
            elif topic == f"bmtl/request/wiper/{self.device_id}":
                task['command'] = 'wiper_request'
            elif topic == f"bmtl/request/camera-on-off/{self.device_id}":
                task['command'] = 'camera_power_request'
            else:
                # Ignore topics that are not handled
                return

            self.task_queue.put(task)

        except Exception as e:
            self.logger.error(f"Error handling message: {e}")

    def setup_mqtt_client(self):
        callback_api_version = mqtt.CallbackAPIVersion.VERSION2
        try:
            self.client = mqtt.Client(client_id=self.mqtt_client_id, callback_api_version=callback_api_version, transport=self.mqtt_transport)
        except (AttributeError, TypeError):
            # Older paho versions
            self.client = mqtt.Client(client_id=self.mqtt_client_id, transport=self.mqtt_transport)

        # Forward paho-mqtt internal logs to our logger for better diagnostics
        try:
            self.client.enable_logger(self.logger)
        except Exception:
            pass

        # Basic auth
        if self.mqtt_username and self.mqtt_password:
            self.client.username_pw_set(self.mqtt_username, self.mqtt_password)
        if self.mqtt_use_tls:
            # Prefer a modern TLS configuration; fall back if not available
            tls_version = getattr(ssl, 'PROTOCOL_TLS_CLIENT', None) or getattr(ssl, 'PROTOCOL_TLSv1_2', ssl.PROTOCOL_TLS)
            try:
                self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=tls_version)
            except Exception as e:
                # Log and retry with default settings to avoid immediate crash
                self.logger.warning(f"TLS configuration failed ({e}); retrying with default TLS")
                try:
                    self.client.tls_set()
                except Exception as e2:
                    self.logger.error(f"TLS reconfiguration failed: {e2}")

        # Configure automatic reconnect delay inside paho as well
        try:
            self.client.reconnect_delay_set(min_delay=self.reconnect_delay, max_delay=self.max_reconnect_delay)
        except Exception:
            pass

        self.logger.info(
            f"MQTT setup: host={self.mqtt_host}:{self.mqtt_port}, tls={self.mqtt_use_tls}, transport={self.mqtt_transport}, client_id={self.mqtt_client_id}"
        )

        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    def signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
        if self.client:
            self.client.disconnect()

    def run(self):
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        self.setup_mqtt_client()

        try:
            self.logger.info(f"Connecting to MQTT broker at {self.mqtt_host}:{self.mqtt_port}")
            self.client.connect(self.mqtt_host, self.mqtt_port, 120)  # Increased keepalive to 120s
            self.client.loop_start()

            last_health_update = 0
            health_interval = 60  # Request device health once per minute

            while self.running:
                # Check connection and attempt reconnection if needed
                if not self.connected:
                    self.reconnect_to_broker()
                # Publish worker responses back to MQTT (only if connected)
                if not self.response_queue.empty() and self.connected:
                    try:
                        response = self.response_queue.get()
                        result = self.client.publish(
                            response['topic'],
                            response['payload'],
                            qos=response.get('qos', 1)
                        )
                        if result.rc != mqtt.MQTT_ERR_SUCCESS:
                            self.logger.warning(f"Failed to publish message to {response['topic']} (rc={result.rc})")
                            continue
                        if not result.is_published():
                            result.wait_for_publish(timeout=self.publish_ack_timeout)
                        if not result.is_published():
                            self.logger.warning(f"Publish acknowledgement timed out for {response['topic']}")
                    except Exception as e:
                        self.logger.error(f"Error publishing message: {e}")
                # Trigger periodic device health request (only if connected)
                if self.connected and time.time() - last_health_update >= health_interval:
                    self.task_queue.put({'command': 'health_check', 'device_id': self.device_id})
                    last_health_update = time.time()

                time.sleep(0.1)

        except Exception as e:
            self.logger.error(f"Error in main loop: {e}")
        finally:
            if self.client:
                self.client.loop_stop()
                self.client.disconnect()
            self.logger.info("MQTT daemon stopped")
# Example invocation from main.py kept for local testing only
# if __name__ == "__main__":
#     # This part is for testing purposes only
#     from multiprocessing import Queue
#     task_q = Queue()
#     response_q = Queue()
#     daemon = MqttDaemon(task_q, response_q)
#     daemon.run()
