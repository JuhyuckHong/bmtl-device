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
import threading
from dotenv import load_dotenv
from datetime import datetime
from pathlib import Path
import paho.mqtt.client as mqtt
from shared_config import config_manager
from gphoto2_controller import GPhoto2Controller

class BMTLDeviceMQTTHandler:
    """디바이스용 MQTT 핸들러 - 서버 메시지에 대응하여 처리"""

    def __init__(self):
        self.config_path = "/etc/bmtl-device/config.ini"
        self.log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        self.client = None
        self.running = True
        # 호스트네임에서 디바이스 번호 추출
        self.device_id = self.extract_device_id_from_hostname()

        # Controllers
        self.gphoto_controller = GPhoto2Controller()

        self.setup_logging()
        self.load_config()

    def setup_logging(self):
        os.makedirs(self.log_dir, exist_ok=True)
        log_file = os.path.join(self.log_dir, "device_mqtt_handler.log")

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger('BMTLDeviceMQTTHandler')

    def extract_device_id_from_hostname(self):
        """호스트네임에서 bmotion 뒤의 숫자 추출"""
        try:
            hostname = socket.gethostname()
            # bmotion01, bmotion02 등에서 숫자 부분만 추출
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
        # Load .env file first
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
        load_dotenv(env_path)

        self.config = configparser.ConfigParser()

        if not os.path.exists(self.config_path):
            self.logger.error(f"Configuration file not found: {self.config_path}")
            sys.exit(1)

        try:
            self.config.read(self.config_path)

            # MQTT Configuration
            self.mqtt_host = os.getenv('MQTT_HOST', self.config.get('mqtt', 'host', fallback='localhost'))
            self.mqtt_port = int(os.getenv('MQTT_PORT', self.config.getint('mqtt', 'port', fallback=1883)))
            self.mqtt_username = os.getenv('MQTT_USERNAME', self.config.get('mqtt', 'username', fallback=''))
            self.mqtt_password = os.getenv('MQTT_PASSWORD', self.config.get('mqtt', 'password', fallback=''))
            self.mqtt_use_tls = os.getenv('MQTT_USE_TLS', self.config.get('mqtt', 'use_tls', fallback='false')).lower() == 'true'
            self.mqtt_client_id = f"bmtl-device-{self.device_id}"

            # Device Configuration (호스트네임에서 추출한 값을 설정파일로 덮어쓸 수 있도록)
            config_device_id = self.config.get('device', 'id', fallback=None)
            if config_device_id:
                self.device_id = config_device_id
                self.logger.info(f"Using device ID from config: {self.device_id}")
            # else: 이미 호스트네임에서 추출한 값 사용

            self.device_location = self.config.get('device', 'location', fallback='현장명')

            self.logger.info("Configuration loaded successfully")

        except Exception as e:
            self.logger.error(f"Error loading configuration: {e}")
            sys.exit(1)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.logger.info("Connected to MQTT broker")

            # 서버가 발행하는 메시지들을 구독 (all 요청과 자신의 번호만)
            topics_to_subscribe = [
                "bmtl/request/settings/all",  # 전체 설정 요청
                f"bmtl/request/settings/{self.device_id}",  # 개별 설정 요청 (01, 02 등)
                "bmtl/request/status/all",  # 전체 상태 요청
                f"bmtl/set/settings/{self.device_id}",  # 설정 변경
                f"bmtl/set/sitename/{self.device_id}",  # 현장 이름 변경
                f"bmtl/sw-update/{self.device_id}",  # SW 업데이트
                f"bmtl/sw-rollback/{self.device_id}",  # SW 롤백
                "bmtl/request/reboot/all",  # 전체 재부팅
                f"bmtl/request/reboot/{self.device_id}",  # 개별 재부팅
                f"bmtl/request/options/{self.device_id}",  # 개별 options 요청
                "bmtl/request/options/all",  # 전체 options 요청
                f"bmtl/request/wiper/{self.device_id}",  # 와이퍼 동작
                f"bmtl/request/camera-on-off/{self.device_id}",  # 카메라 전원
            ]

            for topic in topics_to_subscribe:
                client.subscribe(topic, qos=2)
                self.logger.info(f"Subscribed to {topic}")

            # 초기 헬스 상태 전송
            self.send_health_status()

        else:
            self.logger.error(f"Failed to connect to MQTT broker with result code {rc}")

    def on_disconnect(self, client, userdata, rc):
        disconnect_reasons = {
            0: "Connection successful",
            1: "Connection refused - incorrect protocol version",
            2: "Connection refused - invalid client identifier",
            3: "Connection refused - server unavailable",
            4: "Connection refused - bad username or password",
            5: "Connection refused - not authorised"
        }
        reason = disconnect_reasons.get(rc, f"Unknown disconnect reason ({rc})")
        self.logger.warning(f"Disconnected from MQTT broker: {reason}")

    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')

            self.logger.info(f"Received message on {topic}")

            # 토픽별 메시지 처리
            if topic == "bmtl/request/settings/all":
                self.handle_settings_request_all()
            elif topic == f"bmtl/request/settings/{self.device_id}":
                self.handle_settings_request_individual()
            elif topic == "bmtl/request/status/all":
                self.handle_status_request()
            elif topic == f"bmtl/set/settings/{self.device_id}":
                self.handle_settings_change(payload)
            elif topic == f"bmtl/set/sitename/{self.device_id}":
                self.handle_set_sitename(payload)
            elif topic == f"bmtl/sw-update/{self.device_id}":
                self.handle_sw_update(payload)
            elif topic == f"bmtl/sw-rollback/{self.device_id}":
                self.handle_sw_rollback(payload)
            elif topic == "bmtl/request/reboot/all":
                self.handle_reboot_all()
            elif topic == f"bmtl/request/reboot/{self.device_id}":
                self.handle_reboot_individual()
            elif topic == f"bmtl/request/options/{self.device_id}":
                self.handle_options_request_individual()
            elif topic == "bmtl/request/options/all":
                self.handle_options_request_all()
            elif topic == f"bmtl/request/wiper/{self.device_id}":
                self.handle_wiper_request()
            elif topic == f"bmtl/request/camera-on-off/{self.device_id}":
                self.handle_camera_power_request()

        except Exception as e:
            self.logger.error(f"Error handling message: {e}")

    def handle_settings_request_all(self):
        """전체 설정 요청 처리"""
        try:
            settings = self.gphoto_controller.get_current_settings()

            response = {
                "response_type": "all_settings",
                "modules": {
                    f"bmotion{self.device_id}": settings.get('settings', {}) if settings.get('success') else {}
                },
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish("bmtl/response/settings/all", json.dumps(response), qos=1)
            self.logger.info("Sent all settings response")

        except Exception as e:
            self.logger.error(f"Error handling all settings request: {e}")

    def handle_settings_request_individual(self):
        """개별 설정 요청 처리"""
        try:
            settings_result = self.gphoto_controller.get_current_settings()

            response = {
                "response_type": "settings",
                "module_id": f"bmotion{self.device_id}",
                "settings": settings_result.get('settings', {}) if settings_result.get('success') else {},
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish(f"bmtl/response/settings/{self.device_id}", json.dumps(response), qos=1)
            self.logger.info("Sent individual settings response")

        except Exception as e:
            self.logger.error(f"Error handling individual settings request: {e}")

    def handle_status_request(self):
        """전체 상태 요청 처리"""
        try:
            response = {
                "response_type": "status",
                "system_status": "normal",
                "connected_modules": [f"bmotion{self.device_id}"],
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish("bmtl/response/status", json.dumps(response), qos=1)
            self.logger.info("Sent status response")

        except Exception as e:
            self.logger.error(f"Error handling status request: {e}")

    def handle_settings_change(self, payload):
        """설정 변경 처리"""
        try:
            settings_data = json.loads(payload) if payload else {}

            # 설정을 파일에 저장
            config_manager.write_config('camera_settings.json', settings_data)

            # gphoto2로 실제 설정 적용
            result = self.gphoto_controller.apply_settings(settings_data)

            response = {
                "response_type": "set_settings_result",
                "module_id": f"bmotion{self.device_id}",
                "success": result.get('success', False),
                "message": "Settings applied successfully" if result.get('success') else "Settings application failed",
                "applied_settings": result.get('applied_settings', {}),
                "errors": result.get('errors', []),
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish(f"bmtl/response/set/settings/{self.device_id}", json.dumps(response), qos=1)
            self.logger.info(f"Applied settings and sent response: {result}")

        except Exception as e:
            self.logger.error(f"Error handling settings change: {e}")

    def handle_reboot_all(self):
        """전체 재부팅 처리"""
        try:
            response = {
                "response_type": "reboot_all_result",
                "success": True,
                "message": "Global reboot initiated successfully",
                "affected_modules": [f"bmotion{self.device_id}"],
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish("bmtl/response/reboot/all", json.dumps(response), qos=1)
            self.logger.info("Sent reboot all response")

            # 실제 재부팅 (주의: 테스트 시에는 주석 처리)
            # os.system("sudo reboot")

        except Exception as e:
            self.logger.error(f"Error handling reboot all: {e}")

    def handle_reboot_individual(self):
        """개별 재부팅 처리"""
        try:
            response = {
                "response_type": "reboot_result",
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "message": "Reboot initiated successfully",
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish(f"bmtl/response/reboot/{self.device_id}", json.dumps(response), qos=1)
            self.logger.info("Sent individual reboot response")

            # 실제 재부팅 (주의: 테스트 시에는 주석 처리)
            # os.system("sudo reboot")

        except Exception as e:
            self.logger.error(f"Error handling individual reboot: {e}")

    def handle_options_request_individual(self):
        """개별 옵션 요청 처리"""
        try:
            options_result = self.gphoto_controller.get_camera_options()

            response = {
                "response_type": "options",
                "module_id": f"bmotion{self.device_id}",
                "options": options_result.get('options', {}) if options_result.get('success') else {},
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish(f"bmtl/response/options/{self.device_id}", json.dumps(response), qos=1)
            self.logger.info("Sent individual options response")

        except Exception as e:
            self.logger.error(f"Error handling individual options request: {e}")

    def handle_options_request_all(self):
        """전체 옵션 요청 처리"""
        try:
            options_result = self.gphoto_controller.get_camera_options()

            response = {
                "response_type": "all_options",
                "modules": {
                    f"bmotion{self.device_id}": options_result.get('options', {}) if options_result.get('success') else {}
                },
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish("bmtl/response/options/all", json.dumps(response), qos=1)
            self.logger.info("Sent all options response")

        except Exception as e:
            self.logger.error(f"Error handling all options request: {e}")

    def handle_wiper_request(self):
        """와이퍼 동작 요청 처리"""
        try:
            # 실제 와이퍼 제어 로직이 필요 (GPIO나 외부 명령)
            # 여기서는 시뮬레이션

            response = {
                "response_type": "wiper_result",
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "message": "Wiper operation completed",
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish(f"bmtl/response/wiper/{self.device_id}", json.dumps(response), qos=1)
            self.logger.info("Sent wiper response")

        except Exception as e:
            self.logger.error(f"Error handling wiper request: {e}")

    def handle_camera_power_request(self):
        """카메라 전원 제어 요청 처리"""
        try:
            result = self.gphoto_controller.camera_power_toggle()

            response = {
                "response_type": "camera_power_result",
                "module_id": f"bmotion{self.device_id}",
                "success": result.get('success', False),
                "message": result.get('message', ''),
                "new_state": result.get('current_state', 'unknown'),
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish(f"bmtl/response/camera-on-off/{self.device_id}", json.dumps(response), qos=1)
            self.logger.info("Sent camera power response")

        except Exception as e:
            self.logger.error(f"Error handling camera power request: {e}")

    def handle_set_sitename(self, payload):
        """현장 이름 변경 처리"""
        try:
            data = json.loads(payload) if payload else {}
            new_sitename = data.get('site_name', '')

            if not new_sitename:
                response = {
                    "response_type": "set_sitename_result",
                    "module_id": f"bmotion{self.device_id}",
                    "success": False,
                    "message": "site_name parameter is required",
                    "timestamp": datetime.now().isoformat()
                }
                self.client.publish(f"bmtl/response/set/sitename/{self.device_id}", json.dumps(response), qos=1)
                return

            # config.ini 파일 업데이트
            try:
                config = configparser.ConfigParser()
                config.read(self.config_path)

                if not config.has_section('device'):
                    config.add_section('device')

                config.set('device', 'location', new_sitename)

                with open(self.config_path, 'w') as configfile:
                    config.write(configfile)

                # 메모리 상의 설정도 업데이트
                self.device_location = new_sitename

                response = {
                    "response_type": "set_sitename_result",
                    "module_id": f"bmotion{self.device_id}",
                    "success": True,
                    "message": f"Site name updated to '{new_sitename}'. Daemon will restart to apply changes.",
                    "new_sitename": new_sitename,
                    "timestamp": datetime.now().isoformat()
                }

                self.client.publish(f"bmtl/response/set/sitename/{self.device_id}", json.dumps(response), qos=1)
                self.logger.info(f"Site name updated to '{new_sitename}'")

                # 설정 변경 후 데몬 재시작 (백그라운드에서 실행)
                def restart_daemon():
                    time.sleep(2)  # 응답 전송 후 잠시 대기
                    self.logger.info("Restarting daemon to apply site name changes...")
                    os.system("sudo systemctl restart bmtl-device-mqtt-handler")

                threading.Thread(target=restart_daemon, daemon=True).start()

            except Exception as e:
                self.logger.error(f"Error updating config file: {e}")
                response = {
                    "response_type": "set_sitename_result",
                    "module_id": f"bmotion{self.device_id}",
                    "success": False,
                    "message": f"Failed to update config file: {str(e)}",
                    "timestamp": datetime.now().isoformat()
                }
                self.client.publish(f"bmtl/response/set/sitename/{self.device_id}", json.dumps(response), qos=1)

        except Exception as e:
            self.logger.error(f"Error handling set sitename: {e}")

    def handle_sw_update(self, payload):
        """소프트웨어 업데이트 처리"""
        try:
            response = {
                "response_type": "sw_update_result",
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "message": "Software update initiated. System will restart after update.",
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish(f"bmtl/response/sw-update/{self.device_id}", json.dumps(response), qos=1)
            self.logger.info("Software update initiated")

            # 백그라운드에서 업데이트 실행
            def run_sw_update():
                time.sleep(2)  # 응답 전송 후 잠시 대기
                self.logger.info("Starting software update process...")
                try:
                    # 업데이트 명령 실행
                    update_cmd = "cd /opt/bmtl-device && git stash && git pull && chmod +x ./install.sh && ./install.sh"
                    subprocess.Popen(update_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception as e:
                    self.logger.error(f"Error during software update: {e}")

            threading.Thread(target=run_sw_update, daemon=True).start()

        except Exception as e:
            self.logger.error(f"Error handling software update: {e}")

    def handle_sw_rollback(self, payload):
        """소프트웨어 롤백 처리 (미구현)"""
        try:
            response = {
                "response_type": "sw_rollback_result",
                "module_id": f"bmotion{self.device_id}",
                "success": False,
                "message": "Software rollback feature is not implemented yet",
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish(f"bmtl/response/sw-rollback/{self.device_id}", json.dumps(response), qos=1)
            self.logger.info("Software rollback request received but not implemented")

        except Exception as e:
            self.logger.error(f"Error handling software rollback: {e}")

    def send_health_status(self):
        """헬스 상태 주기적 전송"""
        try:
            # 저장 공간 사용량 계산
            storage_path = '/opt/bmtl-device/photos'
            storage_used = 0
            if os.path.exists(storage_path):
                total_size = sum(os.path.getsize(os.path.join(storage_path, f))
                               for f in os.listdir(storage_path)
                               if os.path.isfile(os.path.join(storage_path, f)))
                storage_used = round(total_size / (1024*1024), 2)  # MB 단위

            # 오늘 촬영 수 계산
            today_captures = 0
            if os.path.exists(storage_path):
                today = datetime.now().strftime("%Y%m%d")
                today_captures = len([f for f in os.listdir(storage_path)
                                    if f.startswith(f"photo_{today}")])

            payload = {
                "module_id": f"bmotion{self.device_id}",
                "status": "online",
                "battery_level": 85,  # 실제 배터리 모니터링 로직 필요
                "storage_used": storage_used,
                "last_capture_time": self.get_last_capture_time(),
                "last_boot_time": self.get_boot_time(),
                "site_name": self.device_location,
                "today_total_captures": 100,  # 계획된 촬영 수
                "today_captured_count": today_captures,
                "missed_captures": 3,  # 실제 누락 계산 필요
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish(f"bmtl/status/health/{self.device_id}", json.dumps(payload), qos=1)

        except Exception as e:
            self.logger.error(f"Error sending health status: {e}")

    def get_last_capture_time(self):
        """마지막 촬영 시간 조회"""
        try:
            storage_path = '/opt/bmtl-device/photos'
            if not os.path.exists(storage_path):
                return None

            files = [f for f in os.listdir(storage_path) if f.endswith('.jpg')]
            if not files:
                return None

            latest_file = max(files, key=lambda f: os.path.getmtime(os.path.join(storage_path, f)))
            mtime = os.path.getmtime(os.path.join(storage_path, latest_file))
            return datetime.fromtimestamp(mtime).isoformat()
        except:
            return None

    def get_boot_time(self):
        """부팅 시간 조회"""
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
                boot_time = datetime.now().timestamp() - uptime_seconds
                return datetime.fromtimestamp(boot_time).isoformat()
        except:
            return datetime.now().isoformat()

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

    def signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

        if self.client:
            # 오프라인 상태 전송
            payload = {
                "module_id": f"bmotion{self.device_id}",
                "status": "offline",
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/status/health/{self.device_id}", json.dumps(payload), qos=1, retain=True)
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

            last_health_update = 0
            health_interval = 60  # 1분마다 헬스 상태 전송

            while self.running:
                current_time = time.time()

                # 주기적 헬스 상태 전송
                if current_time - last_health_update >= health_interval:
                    self.send_health_status()
                    last_health_update = current_time

                time.sleep(1)

        except Exception as e:
            self.logger.error(f"Error in main loop: {e}")

        finally:
            if self.client:
                self.client.loop_stop()
                self.client.disconnect()

            self.logger.info("Device MQTT handler stopped")

if __name__ == "__main__":
    handler = BMTLDeviceMQTTHandler()
    handler.run()