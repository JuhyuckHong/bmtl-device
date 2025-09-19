# 제어패널 MQTT 메시지 요약 (개정 버전)

제어패널이 MQTT 브로커와 주고받는 메시지를 정리했습니다. 모듈 ID는 항상 두 자리(예: `01`, `12`) 형태지만, 본 문서에서는 `{device_id}` 플레이스홀더로 표기합니다.

## 주요 변경사항

1. **전체 설정 응답 방식**: `bmtl/response/settings/all` → 각 모듈이 `bmtl/status/health/{device_id}`로 개별 응답
2. **SW 업데이트 프로토콜**: 복잡한 응답 구조 제거, 업데이트 완료 후 버전 정보만 자동 전송
3. **헬스체크 페이로드**: 카메라 작업 중심으로 통합 (시스템 정보 대신)
4. **SW 버전 정보**: `commit_hash`만 포함하는 간소한 구조
5. **사이트명 설정**: 디바이스에서 sitename 처리 완전 지원

## 발행: 제어패널 → 디바이스

모든 발행은 QoS 1로 전송합니다. 전체 명령은 `global` 탭에서, 개별 명령은 각 모듈 행에서 호출됩니다.

### 개별 모듈 대상 토픽

- **설정 조회**
  - 토픽: `bmtl/request/settings/{device_id}`
  - 페이로드: `{}`
  - 용도: 최신 촬영 설정 스냅샷 요청. 응답은 `bmtl/response/settings/{device_id}`.

- **옵션 조회**
  - 토픽: `bmtl/request/options/{device_id}`
  - 페이로드: `{}`
  - 용도: 해당 모듈이 지원하는 선택지 목록 요청. 응답은 `bmtl/response/options/{device_id}`.

- **설정 적용**
  - 토픽: `bmtl/set/settings/{device_id}`
  - 페이로드 예시:
    ```json
    {
        "start_time": "08:00",
        "end_time": "18:00",
        "capture_interval": 10,
        "image_size": "1920x1080",
        "quality": "85",
        "iso": "400",
        "format": "JPG",
        "aperture": "f/2.8"
    }
    ```
  - 용도: UI에서 편집한 촬영 파라미터 저장. 성공 여부는 `bmtl/response/set/settings/{device_id}`에서 확인합니다.

- **재부팅**
  - 토픽: `bmtl/request/reboot/{device_id}`
  - 페이로드: `{}`
  - 용도: 모듈 강제 재기동. 결과는 `bmtl/response/reboot/{device_id}`.

- **와이퍼 구동**
  - 토픽: `bmtl/request/wiper/{device_id}`
  - 페이로드: `{}`
  - 용도: 약 30초간 와이퍼 동작 지시. 결과는 `bmtl/response/wiper/{device_id}`.

- **카메라 전원 토글**
  - 토픽: `bmtl/request/camera-on-off/{device_id}`
  - 페이로드: `{}`
  - 용도: 전원 On/Off 토글. 응답(`bmtl/response/camera-on-off/{device_id}`)에서 `new_state` 확인.

- **사이트명 변경**
  - 토픽: `bmtl/set/sitename/{device_id}`
  - 페이로드 예시: `{ "sitename": "서울_1공구" }`
  - 용도: 모듈 표시 이름 변경. 성공 시 `bmtl/response/sitename/{device_id}`.

- **SW 업데이트**
  - 토픽: `bmtl/sw-update/{device_id}`
  - 페이로드: `{}`
  - 용도: 펌웨어/소프트웨어 업데이트 요청. _업데이트 완료 후 모듈이 재시작되면 최신 버전은 `bmtl/response/sw-version/{device_id}`로 자동 보고됩니다._
  - **중요**: 제어패널은 업데이트 진행 중 응답을 기대하지 않습니다.

- **SW 버전 조회**
  - 토픽: `bmtl/request/sw-version/{device_id}` _(필요 시 호출)_
  - 페이로드: `{}`
  - 용도: 최신 커밋 해시 정보 요청. 응답은 `bmtl/response/sw-version/{device_id}`.

### 전체(브로드캐스트) 대상 토픽

- **전체 재부팅** : `bmtl/request/reboot/all` with `{}`
- **전체 설정 조회** : `bmtl/request/settings/all` with `{}`
- **전체 옵션 조회** : `bmtl/request/options/all` with `{}`

각 브로드캐스트 응답은 `bmtl/response/reboot/all`, `bmtl/response/options/all`에서 전달됩니다. **전체 설정 요청의 경우 각 모듈이 개별적으로 `bmtl/status/health/{device_id}`로 응답합니다.**

## 구독: 디바이스 → 제어패널

아래 토픽을 모두 구독하며, QoS 1을 사용합니다. 버전 정보는 retain 플래그가 설정됩니다.

| 토픽 패턴                       | 예상 페이로드 필드                                                                    | 처리 내용                                                                                               |
| ------------------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `bmtl/status/health/+`          | `module_id`, `storage_used`, `temperature`, `last_capture_time`, `today_total_captures`, `today_captured_count`, `missed_captures` | 모듈을 온라인으로 표시하고 `moduleStatuses`를 갱신. 5분간 헬스 비트 미수신 시 오프라인 처리.            |
| `bmtl/response/settings/+`      | `response_type`, `settings`                                                           | 개별 설정 응답을 `moduleSettings`에 반영.                                                               |
| `bmtl/response/set/settings/+`  | `success`, `message?`                                                                 | 설정 저장 결과를 로그로 남기고, 실패 시 콘솔 경고.                                                      |
| `bmtl/response/reboot/+`        | `success`, `message?`                                                                 | 개별/전체 재부팅 결과를 로그. 상태 변화는 헬스 비트 재수신에 의존.                                      |
| `bmtl/response/options/+`       | `response_type`, `options`, `modules?`                                                | 개별 옵션 응답은 `moduleOptions`에 반영, 전체 응답은 `modules` 사전을 순회.                             |
| `bmtl/response/wiper/+`         | `success`, `message?`                                                                 | 와이퍼 명령 결과를 로그.                                                                                |
| `bmtl/response/camera-on-off/+` | `success`, `new_state`, `previous_state?`                                             | 전원 토글 결과를 로그.                                                                                  |
| `bmtl/response/sitename/+`      | `success`, `sitename`                                                                 | 성공 시 `moduleStatuses`의 `siteName` 업데이트.                                                         |
| `bmtl/response/sw-version/+`    | `commit_hash`                                                                         | 모듈 재시작 또는 버전 조회 결과. `swVersion` 필드를 최신 값으로 갱신.                                   |

**제거된 토픽**: `bmtl/response/sw-update/+` - 소프트웨어 업데이트는 더 이상 진행 상황 응답을 보내지 않습니다.

## 메시지 예시

### 요청에 대한 응답

- `bmtl/response/settings/{device_id}`

```json
{
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

- `bmtl/response/options/{device_id}`

```json
{
    "response_type": "options",
    "options": {
        "capture_interval": ["5", "10", "15"],
        "image_size": ["1280x720", "1920x1080", "3840x2160"],
        "quality": ["70", "85", "95"]
    }
}
```

- `bmtl/response/options/all`

```json
{
    "response_type": "all_options",
    "modules": {
        "bmotion{device_id}": {
            "capture_interval": ["5", "10"],
            "image_size": ["1920x1080", "2560x1440"]
        }
    }
}
```

- `bmtl/response/reboot/{device_id}`

```json
{
    "success": true,
    "requested_at": "2024-09-19T03:01:10Z",
    "message": "Reboot sequence started"
}
```

- `bmtl/response/reboot/all`

```json
{
    "success": false,
    "message": "One or more modules did not acknowledge"
}
```

- `bmtl/response/wiper/{device_id}`

```json
{
    "success": true,
    "started_at": "2024-09-19T03:01:45Z"
}
```

- `bmtl/response/camera-on-off/{device_id}`

```json
{
    "success": true,
    "new_state": "on",
    "previous_state": "off",
    "requested_at": "2024-09-19T03:02:00Z"
}
```

- `bmtl/response/sitename/{device_id}`

```json
{
    "success": true,
    "sitename": "서울_1공구",
    "updated_at": "2024-09-19T03:02:30Z"
}
```

- `bmtl/response/sw-version/{device_id}`

```json
{
    "commit_hash": "9f2a45c"
}
```

### 주기 발행(Health Beat) - 개정됨

- `bmtl/status/health/{device_id}`

```json
{
    "module_id": "bmotion{device_id}",
    "storage_used": 47.2,
    "temperature": 42.3,
    "last_capture_time": "2024-09-19T02:58:12Z",
    "today_total_captures": 120,
    "today_captured_count": 45,
    "missed_captures": 2
}
```

**변경사항**:
- `module_id` 형태가 `camera_{device_id}`에서 `bmotion{device_id}`로 변경
- `site_name`, `last_boot_time`, `sw_version` 필드 제거
- 시스템 정보(`cpu_percent`, `memory_percent`, `disk_percent`, `uptime`) 대신 카메라 작업 정보에 집중
- `temperature` 필드 추가

## 운용 메모

- 모든 발행 요청은 `recordPublish` 훅을 통해 로컬 로그로 남습니다. 이상 징후가 있으면 해당 로그를 우선 확인하세요.
- 상태가 갱신되지 않으면 `bmtl/status/health/+` 헬스 비트 수신 여부를 먼저 확인하고, 필요 시 `설정 조회`, `옵션 조회`를 재요청합니다.
- 새 토픽을 도입할 때는 `useCameraStatus`의 `CAMERA_CONTROL_TOPICS` 배열, `handleMessage` 분기, 그리고 본 문서를 함께 업데이트하세요.
- **소프트웨어 업데이트**: 제어패널은 업데이트 진행 상황 응답을 기다리지 않습니다. 업데이트 완료 후 디바이스가 재시작되면 자동으로 최신 버전 정보가 전송됩니다.
- **메시지 보존**: 버전 정보(`bmtl/response/sw-version/*`)는 retain 플래그를 사용하여 MQTT 브로커에 보존됩니다. 상태 정보(`bmtl/status/health/*`)는 주기적 전송으로 retain을 사용하지 않습니다.

## 호환성 참고사항

이 개정 버전은 제어패널과 디바이스 간의 프로토콜을 통일하여 다음과 같은 이점을 제공합니다:

1. **단순화된 소프트웨어 업데이트**: 복잡한 상태 추적 없이 업데이트 완료 후 자동 버전 정보 제공
2. **통합된 상태 정보**: 카메라 작업에 집중한 헬스체크 정보
3. **일관된 모듈 식별**: `bmotion{device_id}` 형태로 통일
4. **효율적인 전체 설정 조회**: 개별 헬스 비트를 통한 분산 응답