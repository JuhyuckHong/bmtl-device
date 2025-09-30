#!/usr/bin/env python3

import json
import subprocess
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List

# Module-level constants for consistency
GET_CONFIG_TIMEOUT_S = 10
SET_CONFIG_TIMEOUT_S = 30

# Standardized key mapping for gphoto2 config paths used by this project.
# Note: In this project, 'aperture' refers to exposure compensation.
GPHOTO_CONFIG_MAP: Dict[str, str] = {
    "resolution": "/main/imgsettings/imagesize",
    "iso": "/main/imgsettings/iso",
    "aperture": "/main/capturesettings/exposurecompensation",
    "image_quality": "/main/capturesettings/imagequality",
    "focus_mode": "/main/capturesettings/focusmode2",
}


class GPhoto2Controller:
    """gphoto2 camera controller with configuration helpers.

    Key conventions:
    - resolution: maps to gphoto2 '/main/imgsettings/imagesize'
    - image_quality: maps to '/main/capturesettings/imagequality'
    - aperture: project-defined mapping to exposure compensation
      ('/main/capturesettings/exposurecompensation')
    - iso, focus_mode: standard paths per camera profile
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger('GPhoto2Controller')
        self.camera_connected = False
        self.check_camera_connection()

    def check_camera_connection(self) -> bool:
        """Return True when a camera is detected by gphoto2."""
        try:
            result = subprocess.run(
                ['gphoto2', '--auto-detect'],
                capture_output=True,
                text=True,
                timeout=GET_CONFIG_TIMEOUT_S,
            )
            self.camera_connected = result.returncode == 0 and 'usb:' in result.stdout
            if self.camera_connected:
                self.logger.info("Camera connected successfully")
            else:
                self.logger.warning("No camera detected")
            return self.camera_connected
        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.error("Error checking camera connection: %s", exc)
            self.camera_connected = False
            return False

    def get_camera_options(self) -> Dict[str, Any]:
        """Return option metadata for standardized gphoto2 configuration keys."""
        try:
            if not self.check_camera_connection():
                return {
                    "success": False,
                    "error": "Camera not connected",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

            # Use standardized keys shared with DeviceWorker/UI
            requested_keys = [
                "resolution",
                "iso",
                "aperture",
                "image_quality",
                "focus_mode",
            ]

            options: Dict[str, Any] = {}
            errors = []
            success_flags: List[bool] = []

            for key in requested_keys:
                config_path = GPHOTO_CONFIG_MAP[key]
                details = self._get_config_details(config_path)
                options[key] = {
                    "label": details.get("label"),
                    "type": details.get("type"),
                    "read_only": details.get("read_only"),
                    "current": details.get("current"),
                    "choices": details.get("choices", []),
                }

                success_flags.append(details.get("success", False))

                if not details.get("success", False):
                    error_message = details.get("error", "Unknown error")
                    options[key]["error"] = error_message
                    errors.append({"key": key, "error": error_message})

            any_success = any(success_flags)
            all_success = all(success_flags) if success_flags else False

            response: Dict[str, Any] = {
                "success": any_success,
                "options": options,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if not any_success:
                response["error"] = "Failed to fetch camera options via gphoto2"

            if errors:
                response["errors"] = errors
                if any_success and not all_success:
                    response["partial_success"] = True

            return response

        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.error("Error getting camera options: %s", exc)
            return {
                "success": False,
                "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def _get_config_details(self, config_path: str) -> Dict[str, Any]:
        """Fetch label, type, current value, and choices for a gphoto2 config path."""
        try:
            result = subprocess.run(
                ['gphoto2', '--get-config', config_path],
                capture_output=True,
                text=True,
                timeout=GET_CONFIG_TIMEOUT_S,
            )

            if result.returncode != 0:
                error = result.stderr.strip() or f"gphoto2 returned {result.returncode}"
                self.logger.warning("Failed to read %s: %s", config_path, error)
                return {
                    "success": False,
                    "label": None,
                    "type": None,
                    "read_only": None,
                    "current": None,
                    "choices": [],
                    "error": error,
                }

            label = None
            config_type = None
            read_only = None
            current = None
            choices: List[str] = []

            for raw_line in result.stdout.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith('Label:'):
                    label = line.split(':', 1)[1].strip()
                elif line.startswith('Type:'):
                    config_type = line.split(':', 1)[1].strip().lower()
                elif line.startswith('Readonly:'):
                    value = line.split(':', 1)[1].strip().lower()
                    read_only = value in ('1', 'true')
                elif line.startswith('Current:'):
                    current = line.split(':', 1)[1].strip()
                elif line.startswith('Choice:'):
                    parts = line.split(' ', 2)
                    if len(parts) >= 3:
                        choices.append(parts[2].strip())

            return {
                "success": True,
                "label": label,
                "type": config_type,
                "read_only": read_only,
                "current": current,
                "choices": choices,
            }

        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.error("Error getting config details for %s: %s", config_path, exc)
            return {
                "success": False,
                "label": None,
                "type": None,
                "read_only": None,
                "current": None,
                "choices": [],
                "error": str(exc),
            }

    def apply_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Apply camera settings using gphoto2 for supported keys.

        Notes:
        - 'resolution' (image size) changes are not applied here to avoid
          device-specific pitfalls. Manage via persistent image settings if needed.
        """
        try:
            if not self.check_camera_connection():
                return {
                    "success": False,
                    "error": "Camera not connected",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

            applied_settings = {}
            errors = []

            # Reuse standardized mapping
            setting_map = GPHOTO_CONFIG_MAP

            for key, value in settings.items():
                if key in setting_map:
                    gphoto_key = setting_map[key]
                    try:
                        if key == 'resolution':
                            msg = (
                                f"Resolution change requested ({value}) not applied via apply_settings; "
                                f"manage via image settings"
                            )
                            errors.append(msg)
                            self.logger.info(msg)
                            continue

                        result = subprocess.run(
                            ['gphoto2', '--set-config', f'{gphoto_key}={value}'],
                            capture_output=True,
                            text=True,
                            timeout=SET_CONFIG_TIMEOUT_S,
                        )

                        if result.returncode == 0:
                            applied_settings[key] = value
                            self.logger.info("Applied %s=%s", key, value)
                        else:
                            error_msg = f"Failed to set {key}={value}: {result.stderr}"
                            errors.append(error_msg)
                            self.logger.error(error_msg)

                    except Exception as exc:  # pragma: no cover - defensive guard
                        error_msg = f"Error setting {key}={value}: {exc}"
                        errors.append(error_msg)
                        self.logger.error(error_msg)

            return {
                "success": len(errors) == 0,
                "applied_settings": applied_settings,
                "errors": errors,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.error("Error applying camera settings: %s", exc)
            return {
                "success": False,
                "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def get_current_settings(self) -> Dict[str, Any]:
        """Read a subset of current camera settings via gphoto2."""
        try:
            if not self.check_camera_connection():
                return {
                    "success": False,
                    "error": "Camera not connected",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

            requested_keys = [
                "resolution",
                "iso",
                "aperture",
                "image_quality",
                "focus_mode",
            ]

            options: Dict[str, Any] = {}
            settings: Dict[str, Any] = {}
            errors: List[Dict[str, str]] = []
            success_flags: List[bool] = []

            for key in requested_keys:
                details = self._get_config_details(GPHOTO_CONFIG_MAP[key])
                option_payload = {
                    "label": details.get("label"),
                    "type": details.get("type"),
                    "read_only": details.get("read_only"),
                    "current": details.get("current"),
                    "choices": details.get("choices", []),
                }
                options[key] = option_payload

                is_success = details.get("success", False)
                success_flags.append(is_success)

                if is_success and details.get("current") is not None:
                    settings[key] = details["current"]
                else:
                    error_message = details.get("error", "Unknown error")
                    option_payload["error"] = error_message
                    errors.append({"key": key, "error": error_message})



            any_success = any(success_flags)
            all_success = all(success_flags) if success_flags else False

            response: Dict[str, Any] = {
                "success": any_success,
                "settings": settings,
                "options": options,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if not any_success:
                response["error"] = "Failed to fetch current camera settings via gphoto2"

            if errors:
                response["errors"] = errors
                if any_success and not all_success:
                    response["partial_success"] = True

            return response

        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.error("Error getting current settings: %s", exc)
            return {
                "success": False,
                "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def camera_power_toggle(self) -> Dict[str, Any]:
        """Report camera power state (gphoto2 cannot toggle hardware power)."""
        try:
            connected = self.check_camera_connection()

            return {
                "success": True,
                "message": "Camera power status checked",
                "current_state": "on" if connected else "off",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.error("Error reporting camera power state: %s", exc)
            return {
                "success": False,
                "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    controller = GPhoto2Controller()
    demo_logger = logging.getLogger('GPhoto2ControllerDemo')

    demo_logger.info("=== Camera Options ===")
    options = controller.get_camera_options()
    demo_logger.info(json.dumps(options, indent=2))

    demo_logger.info("=== Current Settings ===")
    settings = controller.get_current_settings()
    demo_logger.info(json.dumps(settings, indent=2))
