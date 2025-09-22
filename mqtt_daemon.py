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
    MQTT 브로커와의 통신을 전담하는 데몬.
    - Worker 프로세스로부터 받은 메시지를 MQTT 브로커에 발행.
    - 구독 중인 토픽에서 메시지를 수신하면 Worker 프로세스에 작업을 전달.
    """

    def __init__(self, task_queue, response_queue):
        self.task_queue = task_queue
        self.response_queue = response_queue

        self.config_path = "/etc/bmtl-device/config.ini"
        self.log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        self.client = None
        self.running = True
        
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
            
            config_device_id = self.config.get('device', 'id', fallback=None)
            if config_device_id:
                self.device_id = config_device_id
            
            self.mqtt_client_id = f"bmtl-device-{self.device_id}"
            self.logger.info("MQTT configuration loaded successfully")
        except Exception as e:
            self.logger.error(f"Error loading configuration: {e}")
            sys.exit(1)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
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
            
            # 초기 헬스 상태 요청
            self.task_queue.put({'command': 'health_check', 'device_id': self.device_id})
        else:
            self.logger.error(f"Failed to connect to MQTT broker with result code {rc}")

    def on_disconnect(self, client, userdata, disconnect_flags=None, reason_code=None, properties=None):
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

        if is_failure:
            self.logger.info('MQTT client will attempt to reconnect if configured.')

        if properties:
            self.logger.debug('Disconnect properties: %s', properties)

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
                # 처리할 수 없는 토픽은 무시
                return

            self.task_queue.put(task)

        except Exception as e:
            self.logger.error(f"Error handling message: {e}")

    def setup_mqtt_client(self):
        callback_api_version = mqtt.CallbackAPIVersion.VERSION2
        try:
            self.client = mqtt.Client(client_id=self.mqtt_client_id, callback_api_version=callback_api_version)
        except (AttributeError, TypeError):
            self.client = mqtt.Client(client_id=self.mqtt_client_id)

        # Forward paho-mqtt internal logs to our logger for better diagnostics
        try:
            self.client.enable_logger(self.logger)
        except Exception:
            pass

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
            self.client.connect(self.mqtt_host, self.mqtt_port, 60)
            self.client.loop_start()

            last_health_update = 0
            health_interval = 60  # 1분마다 헬스 상태 전송 요청

            while self.running:
                # Worker가 보낸 응답 메시지를 MQTT로 발행
                if not self.response_queue.empty():
                    response = self.response_queue.get()
                    self.client.publish(
                        response['topic'],
                        response['payload'],
                        qos=response.get('qos', 1)
                    )

                # 주기적 헬스 상태 전송 요청
                if time.time() - last_health_update >= health_interval:
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

# main.py에서 직접 클래스를 가져와 사용하므로, 이 파일 자체는 직접 실행되지 않음
# if __name__ == "__main__":
#     # This part is for testing purposes only
#     from multiprocessing import Queue
#     task_q = Queue()
#     response_q = Queue()
#     daemon = MqttDaemon(task_q, response_q)
#     daemon.run()
