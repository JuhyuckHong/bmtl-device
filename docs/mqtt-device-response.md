# MQTT Device Response Protocol

This document defines the MQTT communication protocol for BMTL devices, including expected control messages and device responses.

## Device Information
- **Device ID Format**: `{device_id}` (e.g., "001", "002")
- **Module ID Format**: `bmotion{device_id}` (e.g., "bmotion001")

## 1. Settings Management

### 1.1 Get All Settings
**Control → Device (Expected)**
```
Topic: bmtl/request/settings/all
Payload: {} (empty JSON or simple request)
```

**Device → Control (Current)**
```
Topic: bmtl/response/settings/all
Payload: {
  "module_id": "bmotion{device_id}",
  "settings": {
    // Current device settings
  },
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

### 1.2 Get Device Specific Settings
**Control → Device (Expected)**
```
Topic: bmtl/request/settings/{device_id}
Payload: {} (empty JSON or simple request)
```

**Device → Control (Current)**
```
Topic: bmtl/response/settings/{device_id}
Payload: {
  "module_id": "bmotion{device_id}",
  "settings": {
    // Current device settings
  },
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

### 1.3 Set Device Settings
**Control → Device (Expected)**
```
Topic: bmtl/set/settings/{device_id}
Payload: {
  // Settings to update
}
```

**Device → Control (Current)**
```
Topic: bmtl/response/set/settings/{device_id}
Payload: {
  "module_id": "bmotion{device_id}",
  "status": "success" | "error",
  "message": "Settings updated successfully" | "Error message",
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

## 2. Status and Health

### 2.1 Get Status
**Control → Device (Expected)**
```
Topic: bmtl/request/status
Payload: {} (empty JSON or simple request)
```

**Device → Control (Current)**
```
Topic: bmtl/response/status
Payload: {
  "module_id": "bmotion{device_id}",
  "status": "online" | "offline",
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

### 2.2 Health Status (Automatic)
**Device → Control (Current)**
```
Topic: bmtl/status/health/{device_id}
Payload: {
  "module_id": "bmotion{device_id}",
  "status": "online",
  "cpu_percent": 45.2,
  "memory_percent": 32.1,
  "disk_percent": 78.5,
  "temperature": 42.3,
  "uptime": 3600,
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

## 3. Device Control

### 3.1 Reboot All Devices
**Control → Device (Expected)**
```
Topic: bmtl/request/reboot/all
Payload: {} (empty JSON or simple request)
```

**Device → Control (Current)**
```
Topic: bmtl/response/reboot/all
Payload: {
  "module_id": "bmotion{device_id}",
  "status": "rebooting",
  "message": "Device is rebooting...",
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

### 3.2 Reboot Specific Device
**Control → Device (Expected)**
```
Topic: bmtl/request/reboot/{device_id}
Payload: {} (empty JSON or simple request)
```

**Device → Control (Current)**
```
Topic: bmtl/response/reboot/{device_id}
Payload: {
  "module_id": "bmotion{device_id}",
  "status": "rebooting",
  "message": "Device is rebooting...",
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

### 3.3 Wiper Control
**Control → Device (Expected)**
```
Topic: bmtl/request/wiper/{device_id}
Payload: {
  "action": "start" | "stop"
}
```

**Device → Control (Current)**
```
Topic: bmtl/response/wiper/{device_id}
Payload: {
  "module_id": "bmotion{device_id}",
  "status": "success" | "error",
  "action": "start" | "stop",
  "message": "Wiper operation completed" | "Error message",
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

### 3.4 Camera Control
**Control → Device (Expected)**
```
Topic: bmtl/request/camera-on-off/{device_id}
Payload: {
  "action": "on" | "off"
}
```

**Device → Control (Current)**
```
Topic: bmtl/response/camera-on-off/{device_id}
Payload: {
  "module_id": "bmotion{device_id}",
  "status": "success" | "error",
  "action": "on" | "off",
  "message": "Camera operation completed" | "Error message",
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

## 4. Options Management

### 4.1 Get Device Options
**Control → Device (Expected)**
```
Topic: bmtl/request/options/{device_id}
Payload: {} (empty JSON or simple request)
```

**Device → Control (Current)**
```
Topic: bmtl/response/options/{device_id}
Payload: {
  "module_id": "bmotion{device_id}",
  "options": {
    // Device-specific options
  },
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

### 4.2 Get All Options
**Control → Device (Expected)**
```
Topic: bmtl/request/options/all
Payload: {} (empty JSON or simple request)
```

**Device → Control (Current)**
```
Topic: bmtl/response/options/all
Payload: {
  "module_id": "bmotion{device_id}",
  "options": {
    // Device-specific options
  },
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

## 5. Software Management

### 5.1 Software Update
**Control → Device (Expected)**
```
Topic: bmtl/sw-update/{device_id}
Payload: {
  "action": "update",
  "version": "optional_target_version"
}
```

**Device → Control (Current)**
```
Topic: bmtl/response/sw-update/{device_id}
Payload: {
  "response_type": "sw_update_result",
  "module_id": "bmotion{device_id}",
  "status": "started" | "completed" | "failed",
  "message": "Update description or error message",
  "log_file": "/path/to/update_log.log",
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

### 5.2 Software Rollback
**Control → Device (Expected)**
```
Topic: bmtl/sw-rollback/{device_id}
Payload: {
  "action": "rollback",
  "target": "previous" | "specific_commit_hash"
}
```

**Device → Control (Current)**
```
Topic: bmtl/response/sw-rollback/{device_id}
Payload: {
  "response_type": "sw_rollback_result",
  "module_id": "bmotion{device_id}",
  "status": "started" | "completed" | "failed",
  "message": "Rollback description or error message",
  "log_file": "/path/to/rollback_log.log",
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

### 5.3 Version Information (Automatic)
**Device → Control (Current)**
```
Topic: bmtl/response/sw-version/{device_id}
Payload: {
  "module_id": "bmotion{device_id}",
  "sw_version": "v1.0.0",
  "commit_hash": "abc123def456",
  "branch": "main",
  "update_time": "2024-01-01T12:00:00.000000",
  "timestamp": "2024-01-01T12:00:00.000000"
}
QoS: 1, Retain: true
```

## 6. Connection Management

### 6.1 Device Online/Offline Status
**Device → Control (Current)**
```
Topic: bmtl/status/health/{device_id}
Payload: {
  "module_id": "bmotion{device_id}",
  "status": "offline",
  "timestamp": "2024-01-01T12:00:00.000000"
}
QoS: 1, Retain: true (for offline status)
```

## Protocol Notes

### QoS Levels
- Most messages use **QoS 1** (at least once delivery)
- Health status and version info use **QoS 1** with **retain flag**

### Error Handling
All responses include:
- `status`: "success", "error", "started", "completed", "failed"
- `message`: Human-readable description
- `timestamp`: ISO format timestamp

### Logging
- Update and rollback operations create log files
- Log file paths are included in responses
- Logs are stored in device's log directory with timestamp

### Device Identification
- `device_id`: Used in topic routing (e.g., "001")
- `module_id`: Used in payload identification (e.g., "bmotion001")

## Implementation Commands
The software update process executes:
1. `git stash` (60s timeout)
2. `git pull` (120s timeout)
3. `chmod +x ./install.sh` (15s timeout)
4. `./install.sh` (900s timeout)

All operations are logged and executed in `/opt/bmtl-device` directory.