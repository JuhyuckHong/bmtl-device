#!/usr/bin/env python3

from flask import Flask, render_template, request, jsonify
import json
import threading
import time
import ssl
import os
import paho.mqtt.client as mqtt
from datetime import datetime
from dotenv import load_dotenv

app = Flask(__name__)
app.secret_key = 'bmtl-device-simulator-key'

# Load environment variables
load_dotenv()

# 글로벌 상태 저장
simulator_state = {
    'device_id': '01',
    'mqtt_connected': False,
    'camera_connected': True,  # 시뮬레이션에서는 항상 연결됨
    'current_settings': {
        'iso': '400',
        'aperture': 'f/2.8',
        'shutterspeed': '1/125',
        'whitebalance': 'Auto',
        'image_size': '1920x1080',
        'quality': '높음',
        'format': 'JPG'
    },
    'camera_options': {
        'supported_resolutions': ['1920x1080', '1280x720', '5184x3456'],
        'iso_range': [100, 200, 400, 800, 1600, 3200, 6400],
        'aperture_range': ['f/1.4', 'f/2.8', 'f/4', 'f/5.6', 'f/8', 'f/11', 'f/16'],
        'shutterspeed_range': ['1/4000', '1/2000', '1/1000', '1/500', '1/250', '1/125', '1/60', '1/30'],
        'whitebalance_options': ['Auto', 'Daylight', 'Shade', 'Cloudy', 'Tungsten', 'Fluorescent'],
        'supported_formats': ['JPG', 'RAW']
    },
    'messages_log': [],
    'health_status': {
        'status': 'online',
        'battery_level': 85,
        'storage_used': 45.2,
        'today_captured_count': 15,
        'today_total_captures': 100,
        'missed_captures': 3
    },
    'mqtt_config': {
        'host': os.getenv('MQTT_HOST', 'localhost'),
        'port': int(os.getenv('MQTT_PORT', '1883')),
        'username': os.getenv('MQTT_USERNAME', ''),
        'password': os.getenv('MQTT_PASSWORD', ''),
        'use_tls': os.getenv('MQTT_USE_TLS', 'false').lower() == 'true'
    }
}

# 실제 MQTT 브로커에 연결하는 디바이스 시뮬레이터
class DeviceSimulator:
    def __init__(self):
        self.device_id = simulator_state['device_id']
        self.client = None
        self.connected = False

    def log_message(self, source, message):
        simulator_state['messages_log'].append({
            'timestamp': datetime.now().strftime('%H:%M:%S'),
            'source': source,
            'message': message
        })
        # 로그 최대 100개로 제한
        if len(simulator_state['messages_log']) > 100:
            simulator_state['messages_log'].pop(0)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            simulator_state['mqtt_connected'] = True
            self.log_message('MQTT', f'Connected to {simulator_state["mqtt_config"]["host"]}')

            # 디바이스가 구독해야 할 토픽들
            topics_to_subscribe = [
                "bmtl/request/settings/all",
                f"bmtl/request/settings/{self.device_id}",
                "bmtl/request/status",
                f"bmtl/set/settings/{self.device_id}",
                "bmtl/request/reboot/all",
                f"bmtl/request/reboot/{self.device_id}",
                f"bmtl/request/options/{self.device_id}",
                "bmtl/request/options/all",
                f"bmtl/request/wiper/{self.device_id}",
                f"bmtl/request/camera-on-off/{self.device_id}",
            ]

            for topic in topics_to_subscribe:
                client.subscribe(topic, qos=2)
                self.log_message('MQTT', f'Subscribed to {topic}')

            # 초기 헬스 상태 전송
            self.send_health_status()

        else:
            self.connected = False
            simulator_state['mqtt_connected'] = False
            self.log_message('MQTT', f'Connection failed with code {rc}')

    def on_disconnect(self, client, userdata, rc):
        self.connected = False
        simulator_state['mqtt_connected'] = False
        self.log_message('MQTT', 'Disconnected from broker')

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode('utf-8') if msg.payload else ''

        self.log_message('SERVER', f'← {topic}')
        if payload:
            self.log_message('SERVER', f'  📦 {payload}')

        # 메시지 처리 및 응답
        try:
            response = self.handle_message(topic, payload)
            if response:
                response_topic, response_payload = response
                self.client.publish(response_topic, response_payload, qos=1)
                self.log_message('DEVICE', f'→ {response_topic}')
                self.log_message('DEVICE', f'  📤 {response_payload}')
        except Exception as e:
            self.log_message('ERROR', f'Failed to handle message: {str(e)}')

    def handle_message(self, topic, payload):
        """메시지 처리 및 응답 생성"""
        if topic == "bmtl/request/settings/all":
            response_payload = {
                "response_type": "all_settings",
                "modules": {
                    f"bmotion{self.device_id}": simulator_state['current_settings']
                },
                "timestamp": datetime.now().isoformat()
            }
            return ("bmtl/response/settings/all", json.dumps(response_payload))

        elif topic == f"bmtl/request/settings/{self.device_id}":
            response_payload = {
                "response_type": "settings",
                "module_id": f"bmotion{self.device_id}",
                "settings": simulator_state['current_settings'],
                "timestamp": datetime.now().isoformat()
            }
            return (f"bmtl/response/settings/{self.device_id}", json.dumps(response_payload))

        elif topic == "bmtl/request/status":
            response_payload = {
                "response_type": "status",
                "system_status": "normal",
                "connected_modules": [f"bmotion{self.device_id}"],
                "timestamp": datetime.now().isoformat()
            }
            return ("bmtl/response/status", json.dumps(response_payload))

        elif topic == f"bmtl/set/settings/{self.device_id}":
            try:
                settings = json.loads(payload) if payload else {}
                simulator_state['current_settings'].update(settings)
                response_payload = {
                    "response_type": "set_settings_result",
                    "module_id": f"bmotion{self.device_id}",
                    "success": True,
                    "message": "Settings applied successfully",
                    "applied_settings": settings,
                    "timestamp": datetime.now().isoformat()
                }
                return (f"bmtl/response/set/settings/{self.device_id}", json.dumps(response_payload))
            except Exception as e:
                response_payload = {
                    "response_type": "set_settings_result",
                    "module_id": f"bmotion{self.device_id}",
                    "success": False,
                    "message": f"Settings failed: {str(e)}",
                    "timestamp": datetime.now().isoformat()
                }
                return (f"bmtl/response/set/settings/{self.device_id}", json.dumps(response_payload))

        elif topic == f"bmtl/request/options/{self.device_id}":
            response_payload = {
                "response_type": "options",
                "module_id": f"bmotion{self.device_id}",
                "options": simulator_state['camera_options'],
                "timestamp": datetime.now().isoformat()
            }
            return (f"bmtl/response/options/{self.device_id}", json.dumps(response_payload))

        elif topic == "bmtl/request/options/all":
            response_payload = {
                "response_type": "all_options",
                "modules": {
                    f"bmotion{self.device_id}": simulator_state['camera_options']
                },
                "timestamp": datetime.now().isoformat()
            }
            return ("bmtl/response/options/all", json.dumps(response_payload))

        elif topic == "bmtl/request/reboot/all":
            response_payload = {
                "response_type": "reboot_all_result",
                "success": True,
                "message": "Global reboot initiated successfully (simulated)",
                "affected_modules": [f"bmotion{self.device_id}"],
                "timestamp": datetime.now().isoformat()
            }
            return ("bmtl/response/reboot/all", json.dumps(response_payload))

        elif topic == f"bmtl/request/reboot/{self.device_id}":
            response_payload = {
                "response_type": "reboot_result",
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "message": "Reboot initiated successfully (simulated)",
                "timestamp": datetime.now().isoformat()
            }
            return (f"bmtl/response/reboot/{self.device_id}", json.dumps(response_payload))

        elif topic == f"bmtl/request/wiper/{self.device_id}":
            response_payload = {
                "response_type": "wiper_result",
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "message": "Wiper operation completed (simulated)",
                "timestamp": datetime.now().isoformat()
            }
            return (f"bmtl/response/wiper/{self.device_id}", json.dumps(response_payload))

        elif topic == f"bmtl/request/camera-on-off/{self.device_id}":
            response_payload = {
                "response_type": "camera_power_result",
                "module_id": f"bmotion{self.device_id}",
                "success": True,
                "message": "Camera power toggled successfully (simulated)",
                "new_state": "on",
                "timestamp": datetime.now().isoformat()
            }
            return (f"bmtl/response/camera-on-off/{self.device_id}", json.dumps(response_payload))

        return None

    def send_health_status(self):
        """헬스 상태 전송"""
        try:
            payload = {
                "module_id": f"bmotion{self.device_id}",
                "status": simulator_state['health_status']['status'],
                "battery_level": simulator_state['health_status']['battery_level'],
                "storage_used": simulator_state['health_status']['storage_used'],
                "last_capture_time": datetime.now().isoformat(),
                "last_boot_time": datetime.now().isoformat(),
                "site_name": "시뮬레이션 현장",
                "today_total_captures": simulator_state['health_status']['today_total_captures'],
                "today_captured_count": simulator_state['health_status']['today_captured_count'],
                "missed_captures": simulator_state['health_status']['missed_captures'],
                "timestamp": datetime.now().isoformat()
            }

            if self.client and self.connected:
                self.client.publish(f"bmtl/status/health/{self.device_id}", json.dumps(payload), qos=1)
                self.log_message('DEVICE', f'→ bmtl/status/health/{self.device_id}')
        except Exception as e:
            self.log_message('ERROR', f'Failed to send health status: {str(e)}')

    def connect_mqtt(self):
        """실제 MQTT 브로커에 연결"""
        try:
            config = simulator_state['mqtt_config']
            self.client = mqtt.Client(client_id=f"bmtl-device-simulator-{self.device_id}")

            if config['username'] and config['password']:
                self.client.username_pw_set(config['username'], config['password'])

            if config['use_tls']:
                self.client.tls_set()

            self.client.on_connect = self.on_connect
            self.client.on_disconnect = self.on_disconnect
            self.client.on_message = self.on_message

            self.log_message('MQTT', f'🔄 Connecting to {config["host"]}:{config["port"]}...')
            self.client.connect(config['host'], config['port'], 60)
            self.client.loop_start()

            return True
        except Exception as e:
            self.log_message('ERROR', f'MQTT connection failed: {str(e)}')
            return False

    def disconnect_mqtt(self):
        """MQTT 연결 해제"""
        if self.client:
            try:
                # 오프라인 상태 전송
                payload = {
                    "module_id": f"bmotion{self.device_id}",
                    "status": "offline",
                    "timestamp": datetime.now().isoformat()
                }
                self.client.publish(f"bmtl/status/health/{self.device_id}", json.dumps(payload), qos=1)

                self.client.loop_stop()
                self.client.disconnect()
                self.log_message('MQTT', '🔴 Disconnected from broker')
            except Exception as e:
                self.log_message('ERROR', f'Disconnect error: {str(e)}')
            finally:
                self.client = None
                self.connected = False
                simulator_state['mqtt_connected'] = False

# 글로벌 시뮬레이터 인스턴스
device_simulator = DeviceSimulator()

# 헬스 상태 주기 전송 스레드
def health_status_sender():
    while True:
        time.sleep(60)  # 1분마다
        if device_simulator.connected:
            device_simulator.send_health_status()

# 백그라운드 스레드 시작
health_thread = threading.Thread(target=health_status_sender, daemon=True)
health_thread.start()

# Flask 라우트들
@app.route('/')
def index():
    # 환경 변수를 템플릿에 직접 전달
    template_data = {
        'state': simulator_state,
        'env_mqtt_host': os.getenv('MQTT_HOST', ''),
        'env_mqtt_port': os.getenv('MQTT_PORT', ''),
        'env_mqtt_username': os.getenv('MQTT_USERNAME', ''),
        'env_mqtt_password': os.getenv('MQTT_PASSWORD', ''),
        'env_mqtt_use_tls': os.getenv('MQTT_USE_TLS', 'false')
    }
    return render_template('simulator.html', **template_data)

@app.route('/api/connect', methods=['POST'])
def connect_mqtt():
    success = device_simulator.connect_mqtt()
    return jsonify({'success': success, 'message': 'MQTT Connection initiated'})

@app.route('/api/disconnect', methods=['POST'])
def disconnect_mqtt():
    device_simulator.disconnect_mqtt()
    return jsonify({'success': True, 'message': 'MQTT Disconnected'})

@app.route('/api/get_status')
def get_status():
    # 디바이스 ID가 변경되었다면 시뮬레이터에 반영
    device_simulator.device_id = simulator_state['device_id']
    return jsonify(simulator_state)

@app.route('/api/clear_log', methods=['POST'])
def clear_log():
    simulator_state['messages_log'].clear()
    return jsonify({'success': True})

@app.route('/api/set_device_id', methods=['POST'])
def set_device_id():
    device_id = request.json.get('device_id', '01').zfill(2)  # 01, 02 형태로 zero-pad
    simulator_state['device_id'] = device_id
    device_simulator.device_id = device_id
    device_simulator.log_message('SYSTEM', f'🔧 Device ID changed to bmotion{device_id}')
    return jsonify({'success': True, 'device_id': device_id})

@app.route('/api/update_mqtt_config', methods=['POST'])
def update_mqtt_config():
    config_data = request.json
    simulator_state['mqtt_config'].update(config_data)
    device_simulator.log_message('SYSTEM', '⚙️ MQTT config updated')
    return jsonify({'success': True})

@app.route('/api/send_health', methods=['POST'])
def send_health_status():
    if device_simulator.connected:
        device_simulator.send_health_status()
        return jsonify({'success': True, 'message': 'Health status sent'})
    else:
        return jsonify({'success': False, 'message': 'Not connected to MQTT'})

if __name__ == '__main__':
    print("🚀 BMTL Device Simulator Starting...")
    print(f"📡 MQTT Config: {simulator_state['mqtt_config']['host']}:{simulator_state['mqtt_config']['port']}")
    print(f"🏭 Device: bmotion{simulator_state['device_id']}")
    print("🌐 Web Interface: http://localhost:5000")
    app.run(debug=True, host='0.0.0.0', port=5000)