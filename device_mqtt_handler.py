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
                f"bmtl/request/sw-version/{self.device_id}",  # SW 버전 요청
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
            elif topic == f"bmtl/request/sw-version/{self.device_id}":
                self.handle_sw_version_request()
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

    def get_enhanced_settings(self):
        """제어 시스템이 기대하는 형태의 설정 반환"""
        try:
            # gphoto2 설정 조회
            gphoto_settings = self.gphoto_controller.get_current_settings()
            base_settings = gphoto_settings.get('settings', {}) if gphoto_settings.get('success') else {}

            # 제어 시스템 호환 설정 생성
            enhanced_settings = {
                # 기존 gphoto2 설정
                "iso": base_settings.get("iso", "auto"),
                "aperture": base_settings.get("aperture", "f/2.8"),

                # shutter_speed를 제어 시스템이 이해할 수 있는 형태로 변환
                "shutter_speed": base_settings.get("shutter_speed", "1/60"),

                # 제어 시스템이 기대하는 추가 필드들 (기본값 제공)
                "startTime": "08:00",  # 촬영 시작 시간
                "endTime": "18:00",    # 촬영 종료 시간
                "captureInterval": "10",  # 촬영 간격 (분)
                "imageSize": "1920x1080",  # 이미지 크기
                "quality": "85",       # 이미지 품질 (0-100)
                "format": "jpeg"       # 이미지 포맷
            }

            return enhanced_settings

        except Exception as e:
            self.logger.error(f"Error getting enhanced settings: {e}")
            # 기본 설정 반환
            return {
                "startTime": "08:00",
                "endTime": "18:00",
                "captureInterval": "10",
                "imageSize": "1920x1080",
                "quality": "85",
                "iso": "auto",
                "format": "jpeg",
                "aperture": "f/2.8"
            }

    def handle_settings_request_all(self):
        """전체 설정 요청 처리"""
        try:
            enhanced_settings = self.get_enhanced_settings()

            response = {
                "response_type": "all_settings",
                "modules": {
                    f"bmotion{self.device_id}": enhanced_settings
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
            enhanced_settings = self.get_enhanced_settings()

            response = {
                "response_type": "settings",
                "module_id": f"bmotion{self.device_id}",
                "settings": enhanced_settings,
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
        """설정 변경 처리 (확장된 설정 지원)"""
        try:
            settings_data = json.loads(payload) if payload else {}
            self.logger.info(f"Received settings change request: {settings_data}")

            # 제어 시스템에서 온 설정을 gphoto2 형태로 변환
            gphoto_settings = {}
            schedule_settings = {}
            image_settings = {}

            # 카메라 설정 매핑
            if "iso" in settings_data:
                gphoto_settings["iso"] = settings_data["iso"]
            if "aperture" in settings_data:
                gphoto_settings["aperture"] = settings_data["aperture"]
            if "shutter_speed" in settings_data:
                gphoto_settings["shutter_speed"] = settings_data["shutter_speed"]

            # 스케줄 설정 분리
            if "startTime" in settings_data:
                schedule_settings["start_time"] = settings_data["startTime"]
            if "endTime" in settings_data:
                schedule_settings["end_time"] = settings_data["endTime"]
            if "captureInterval" in settings_data:
                schedule_settings["capture_interval"] = settings_data["captureInterval"]

            # 이미지 설정 분리
            if "imageSize" in settings_data:
                image_settings["image_size"] = settings_data["imageSize"]
            if "quality" in settings_data:
                image_settings["quality"] = settings_data["quality"]
            if "format" in settings_data:
                image_settings["format"] = settings_data["format"]

            # 각 설정 카테고리별로 처리
            results = {
                "gphoto_settings": {"success": True, "applied": {}, "errors": []},
                "schedule_settings": {"success": True, "applied": {}, "errors": []},
                "image_settings": {"success": True, "applied": {}, "errors": []}
            }

            # gphoto2 설정 적용
            if gphoto_settings:
                gphoto_result = self.gphoto_controller.apply_settings(gphoto_settings)
                results["gphoto_settings"] = gphoto_result

            # 스케줄 설정 저장 (config.ini 또는 별도 파일)
            if schedule_settings:
                try:
                    config_manager.write_config('schedule_settings.json', schedule_settings)
                    results["schedule_settings"]["applied"] = schedule_settings
                    self.logger.info(f"Saved schedule settings: {schedule_settings}")
                except Exception as e:
                    results["schedule_settings"]["success"] = False
                    results["schedule_settings"]["errors"].append(str(e))

            # 이미지 설정 저장
            if image_settings:
                try:
                    config_manager.write_config('image_settings.json', image_settings)
                    results["image_settings"]["applied"] = image_settings
                    self.logger.info(f"Saved image settings: {image_settings}")
                except Exception as e:
                    results["image_settings"]["success"] = False
                    results["image_settings"]["errors"].append(str(e))

            # 전체 설정 저장 (백업용)
            config_manager.write_config('camera_settings.json', settings_data)

            # 전체 성공 여부 계산
            overall_success = all([
                results["gphoto_settings"]["success"],
                results["schedule_settings"]["success"],
                results["image_settings"]["success"]
            ])

            # 적용된 설정 통합
            all_applied = {}
            all_applied.update(results["gphoto_settings"].get("applied_settings", {}))
            all_applied.update(results["schedule_settings"].get("applied", {}))
            all_applied.update(results["image_settings"].get("applied", {}))

            # 모든 에러 수집
            all_errors = []
            all_errors.extend(results["gphoto_settings"].get("errors", []))
            all_errors.extend(results["schedule_settings"].get("errors", []))
            all_errors.extend(results["image_settings"].get("errors", []))

            response = {
                "response_type": "set_settings_result",
                "module_id": f"bmotion{self.device_id}",
                "success": overall_success,
                "message": "Settings applied successfully" if overall_success else "Some settings failed to apply",
                "applied_settings": all_applied,
                "errors": all_errors,
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish(f"bmtl/response/set/settings/{self.device_id}", json.dumps(response), qos=1)
            self.logger.info(f"Applied enhanced settings and sent response. Success: {overall_success}")

        except Exception as e:
            self.logger.error(f"Error handling settings change: {e}")
            error_response = {
                "response_type": "set_settings_result",
                "module_id": f"bmotion{self.device_id}",
                "success": False,
                "message": f"Settings application failed: {str(e)}",
                "applied_settings": {},
                "errors": [str(e)],
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/set/settings/{self.device_id}", json.dumps(error_response), qos=1)

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

    def handle_sw_version_request(self):
        """소프트웨어 버전 요청 처리"""
        try:
            # Git 커밋 해시 조회
            commit_hash = "unknown"
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd="/opt/bmtl-device",
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    commit_hash = result.stdout.strip()[:12]  # 앞 12자리만
            except Exception as e:
                self.logger.warning(f"Failed to get git commit hash: {e}")

            response = {
                "response_type": "sw_version_result",
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "commit_hash": commit_hash,
                "timestamp": datetime.now().isoformat()
            }

            self.client.publish(f"bmtl/response/sw-version/{self.device_id}", json.dumps(response), qos=1)
            self.logger.info(f"Sent SW version response: {commit_hash}")

        except Exception as e:
            self.logger.error(f"Error handling SW version request: {e}")
            error_response = {
                "response_type": "sw_version_result",
                "module_id": f"bmotion{self.device_id}",
                "success": False,
                "commit_hash": "error",
                "message": str(e),
                "timestamp": datetime.now().isoformat()
            }
            self.client.publish(f"bmtl/response/sw-version/{self.device_id}", json.dumps(error_response), qos=1)

    def send_health_status(self):
        """헬스 상태 주기적 전송"""
        try:
            # 저장 공간 사용량 계산 (백분율로 변환)
            storage_path = '/opt/bmtl-device/photos'
            storage_used_percentage = 0
            if os.path.exists(storage_path):
                # 디스크 사용량 계산
                try:
                    import shutil
                    total, used, free = shutil.disk_usage(storage_path)
                    storage_used_percentage = round((used / total) * 100, 2)
                except Exception as e:
                    self.logger.warning(f"Failed to get disk usage: {e}")
                    # 폴더 내 파일 크기로 대체 (대략적 계산)
                    total_size = sum(os.path.getsize(os.path.join(storage_path, f))
                                   for f in os.listdir(storage_path)
                                   if os.path.isfile(os.path.join(storage_path, f)))
                    # 가정: 전체 용량 10GB
                    storage_used_percentage = round((total_size / (10 * 1024 * 1024 * 1024)) * 100, 2)

            # 온도 정보 조회 (예시: CPU 온도)
            temperature = self.get_temperature()

            # SW 버전 정보 조회
            sw_version = self.get_current_sw_version()

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
                "storage_used": storage_used_percentage,  # 백분율로 변경
                "temperature": temperature,  # 온도 추가
                "last_capture_time": self.get_last_capture_time(),
                "last_boot_time": self.get_boot_time(),
                "site_name": self.device_location,
                "today_total_captures": 100,  # 계획된 촬영 수
                "today_captured_count": today_captures,
                "missed_captures": 3,  # 실제 누락 계산 필요
                "sw_version": sw_version,  # SW 버전 추가
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

    def get_temperature(self):
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
                    # 간단한 파싱 (첫 번째 온도 값 추출)
                    import re
                    temps = re.findall(r'(\d+\.\d+)°C', result.stdout)
                    if temps:
                        return float(temps[0])
            except:
                pass

            # 기본값 반환
            return 25.0  # 상온으로 가정
        except Exception as e:
            self.logger.warning(f"Failed to get temperature: {e}")
            return 25.0

    def get_current_sw_version(self):
        """현재 SW 버전 조회"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd="/opt/bmtl-device",
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()[:12]  # 앞 12자리만
            else:
                return "unknown"
        except Exception as e:
            self.logger.warning(f"Failed to get SW version: {e}")
            return "unknown"

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