# BMTL Device MQTT Client Daemon

라즈베리파이용 MQTT 클라이언트 데몬 프로그램입니다. GitHub에서 클론 후 자동으로 설치하고 실행할 수 있습니다.

## 기능

- MQTT 브로커에 자동 연결
- 장치 상태 및 하트비트 전송
- 원격 명령 수신 및 처리
- systemd 서비스로 자동 실행
- 설정 파일을 통한 쉬운 구성

## 설치 방법

### 1. 저장소 클론
```bash
git clone https://github.com/JuhyuckHong/bmtl-device.git
cd bmtl-device
```

### 2. 설치 스크립트 실행
```bash
chmod +x install.sh
./install.sh
```

### 3. 설정 파일 편집
```bash
sudo nano /etc/bmtl-device/config.ini
```

MQTT 브로커 정보를 설정하세요:
```ini
[mqtt]
host = your-mqtt-broker.com
port = 1883
username = your-username
password = your-password
```

### 4. 서비스 시작
```bash
sudo systemctl start bmtl-device
```

## 사용법

### 서비스 상태 확인
```bash
sudo systemctl status bmtl-device
```

### 로그 확인
```bash
sudo journalctl -u bmtl-device -f
```

### 서비스 중지
```bash
sudo systemctl stop bmtl-device
```

### 서비스 재시작
```bash
sudo systemctl restart bmtl-device
```

## MQTT 토픽

### 상태 토픽
- `bmtl/device/status/{device_id}` - 장치 상태 정보
- `bmtl/device/heartbeat/{device_id}` - 하트비트 메시지

### 명령 토픽
- `bmtl/device/command/{device_id}` - 원격 명령 수신

### 명령 예시

상태 요청:
```json
{
  "type": "status"
}
```

재시작 명령:
```json
{
  "type": "restart"
}
```

종료 명령:
```json
{
  "type": "shutdown"
}
```

## 설정 파일

`/etc/bmtl-device/config.ini` 파일을 통해 다음을 설정할 수 있습니다:

- MQTT 브로커 연결 정보
- 장치 정보 (ID, 위치)
- MQTT 토픽명
- 하트비트 및 상태 전송 간격

## 파일 구조

- `mqtt_daemon.py` - 메인 데몬 프로그램
- `install.sh` - 설치 스크립트
- `bmtl-device.service` - systemd 서비스 파일
- `config.ini` - 기본 설정 파일

## 로그

로그 파일은 다음 위치에 저장됩니다:
- `/var/log/bmtl-device/mqtt_daemon.log` - 데몬 동작 로그
- `/var/log/bmtl-device/mqtt_messages.log` - 수신된 모든 MQTT 메시지 로그

### 메시지 로그 형식
모든 수신된 MQTT 메시지는 JSON 형태로 저장됩니다:
```json
{
  "topic": "sensor/temperature",
  "payload": "23.5",
  "qos": 0,
  "retain": false,
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

시스템 로그는 journalctl을 통해 확인할 수 있습니다:
```bash
sudo journalctl -u bmtl-device
```

### 실시간 메시지 모니터링
```bash
# 모든 MQTT 메시지 실시간 확인
tail -f /var/log/bmtl-device/mqtt_messages.log

# 특정 토픽만 필터링
tail -f /var/log/bmtl-device/mqtt_messages.log | grep "sensor/temperature"
```