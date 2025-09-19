# BMTL MQTT API Specification (Revised)

본 문서는 BMTL 제어패널과 디바이스 간의 MQTT 통신 프로토콜을 정의합니다.

## 1. 기본 정보

- **Device ID Format**: 두 자리 숫자 (예: "01", "02")
- **Module ID Format**: `bmotion{device_id}` (예: "bmotion01")
- **QoS Level**: 대부분의 메시지는 QoS 1 사용
- **Retain**: 상태 정보와 버전 정보는 retain 플래그 사용

## 2. 제어패널 → 디바이스 (Commands)

### 2.1 설정 관련

#### 개별 설정 조회
```
Topic: bmtl/request/settings/{device_id}
Payload: {}
Response: bmtl/response/settings/{device_id}
```

#### 전체 설정 조회
```
Topic: bmtl/request/settings/all
Payload: {}
Response: 각 디바이스가 bmtl/status/health/{device_id}로 개별 응답
```

#### 설정 적용
```
Topic: bmtl/set/settings/{device_id}
Payload: {
  "start_time": "08:00",
  "end_time": "18:00",
  "capture_interval": 10,
  "image_size": "1920x1080",
  "quality": "85",
  "iso": "400",
  "format": "JPG",
  "aperture": "f/2.8"
}
Response: bmtl/response/set/settings/{device_id}
```

#### 옵션 조회
```
Topic: bmtl/request/options/{device_id}
Payload: {}
Response: bmtl/response/options/{device_id}

Topic: bmtl/request/options/all
Payload: {}
Response: bmtl/response/options/all
```

### 2.2 제어 명령

#### 재부팅
```
Topic: bmtl/request/reboot/{device_id}
Topic: bmtl/request/reboot/all
Payload: {}
Response: bmtl/response/reboot/{device_id} 또는 bmtl/response/reboot/all
```

#### 와이퍼 제어
```
Topic: bmtl/request/wiper/{device_id}
Payload: {}
Response: bmtl/response/wiper/{device_id}
```

#### 카메라 전원 제어
```
Topic: bmtl/request/camera-on-off/{device_id}
Payload: {}
Response: bmtl/response/camera-on-off/{device_id}
```

#### 사이트명 설정
```
Topic: bmtl/set/sitename/{device_id}
Payload: {
  "sitename": "서울_1공구"
}
Response: bmtl/response/sitename/{device_id}
```

### 2.3 소프트웨어 관리

#### 소프트웨어 업데이트
```
Topic: bmtl/sw-update/{device_id}
Payload: {}
Note: 응답 없음. 업데이트 완료 후 자동 재시작되면 최신 버전이 bmtl/response/sw-version/{device_id}로 전송됨
```

#### 버전 정보 조회
```
Topic: bmtl/request/sw-version/{device_id}
Payload: {}
Response: bmtl/response/sw-version/{device_id}
```

## 3. 디바이스 → 제어패널 (Responses)

### 3.1 헬스 상태 (주기적 전송)

```
Topic: bmtl/status/health/{device_id}
QoS: 1, Retain: false
Payload: {
  "module_id": "bmotion{device_id}",
  "storage_used": 47.2,
  "temperature": 42.3,
  "last_capture_time": "2024-09-19T02:58:12Z",
  "today_total_captures": 120,
  "today_captured_count": 45,
  "missed_captures": 2
}
```

### 3.2 설정 응답

#### 개별 설정 응답
```
Topic: bmtl/response/settings/{device_id}
Payload: {
  "response_type": "settings",
  "settings": {
    "start_time": "08:00",
    "end_time": "18:00",
    "capture_interval": 10,
    "image_size": "1920x1080",
    "quality": "85",
    "iso": "400",
    "format": "JPG",
    "aperture": "f/2.8"
  },
  "timestamp": "2024-09-19T03:00:00Z"
}
```

#### 설정 적용 응답
```
Topic: bmtl/response/set/settings/{device_id}
Payload: {
  "success": true,
  "message": "Settings updated successfully"
}
```

### 3.3 옵션 응답

#### 개별 옵션 응답
```
Topic: bmtl/response/options/{device_id}
Payload: {
  "response_type": "options",
  "options": {
    "capture_interval": ["5", "10", "15"],
    "image_size": ["1280x720", "1920x1080", "3840x2160"],
    "quality": ["70", "85", "95"]
  }
}
```

#### 전체 옵션 응답
```
Topic: bmtl/response/options/all
Payload: {
  "response_type": "all_options",
  "modules": {
    "bmotion{device_id}": {
      "capture_interval": ["5", "10"],
      "image_size": ["1920x1080", "2560x1440"]
    }
  }
}
```

### 3.4 제어 응답

#### 재부팅 응답
```
Topic: bmtl/response/reboot/{device_id}
Payload: {
  "success": true,
  "requested_at": "2024-09-19T03:01:10Z",
  "message": "Reboot sequence started"
}
```

#### 와이퍼 응답
```
Topic: bmtl/response/wiper/{device_id}
Payload: {
  "success": true,
  "started_at": "2024-09-19T03:01:45Z"
}
```

#### 카메라 전원 응답
```
Topic: bmtl/response/camera-on-off/{device_id}
Payload: {
  "success": true,
  "new_state": "on",
  "previous_state": "off",
  "requested_at": "2024-09-19T03:02:00Z"
}
```

#### 사이트명 설정 응답
```
Topic: bmtl/response/sitename/{device_id}
Payload: {
  "success": true,
  "sitename": "서울_1공구",
  "updated_at": "2024-09-19T03:02:30Z"
}
```

### 3.5 소프트웨어 관리 응답

#### 버전 정보 응답
```
Topic: bmtl/response/sw-version/{device_id}
QoS: 1, Retain: true
Payload: {
  "commit_hash": "9f2a45c"
}
```

## 4. 프로토콜 변경 사항

### 4.1 전체 설정 요청 응답 방식 변경
- **이전**: `bmtl/response/settings/all`로 통합 응답
- **변경**: 각 디바이스가 `bmtl/status/health/{device_id}`로 개별 응답

### 4.2 소프트웨어 업데이트 프로토콜 단순화
- **이전**: `response_type`, `status`, `log_file` 등 복잡한 응답 구조
- **변경**: 응답 없음. 업데이트 완료 후 재시작 시 자동으로 버전 정보 전송

### 4.3 헬스체크 페이로드 통합
- **이전**: 시스템 정보 중심 (CPU, Memory, Disk 등)
- **변경**: 카메라 작업 중심 정보로 통합
  - `module_id`: `bmotion{device_id}` 형태로 통일
  - 핵심 정보만 포함: storage_used, temperature, capture 관련 정보

### 4.4 SW 버전 정보 간소화
- **이전**: 복잡한 메타데이터 포함 (sw_version, branch, update_time 등)
- **변경**: `commit_hash`만 포함하는 간소한 구조

## 5. 에러 처리

모든 응답 메시지는 다음 구조를 따릅니다:

### 성공 응답
```json
{
  "success": true,
  "message": "Operation completed successfully",
  // ... other fields
}
```

### 실패 응답
```json
{
  "success": false,
  "message": "Error description"
}
```

## 6. 운영 고려사항

### 6.1 연결 관리
- 디바이스는 5분 간격으로 헬스 비트 전송
- 제어패널은 5분간 헬스 비트 미수신 시 오프라인 처리

### 6.2 메시지 보존
- 버전 정보(`bmtl/response/sw-version/*`)는 retain 플래그 사용
- 상태 정보(`bmtl/status/health/*`), 제어 명령, 일반 응답은 retain 플래그 사용하지 않음

### 6.3 장애 복구
- 디바이스 재시작 시 자동으로 최신 상태 정보 및 버전 정보 전송
- Will 메시지를 통한 비정상 종료 감지