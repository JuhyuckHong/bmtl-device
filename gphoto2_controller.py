#!/usr/bin/env python3

import json
import subprocess
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List


class GPhoto2Controller:
    """gphoto2 camera controller with configuration helpers."""

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
                timeout=10,
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
        """Return option metadata for the requested gphoto2 configuration paths."""
        try:
            if not self.check_camera_connection():
                return {
                    "success": False,
                    "error": "Camera not connected",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

            requested_configs = {
                "resolution": "/main/imgsettings/imagesize",
                "iso": "/main/imgsettings/iso",
                "aperture": "/main/capturesettings/exposurecompensation",
                "image_quality": "/main/capturesettings/imagequality",
                "focus_mode": "/main/capturesettings/focusmode2",
            }

            options: Dict[str, Any] = {}
            errors = []
            success_flags: List[bool] = []

            for key, config_path in requested_configs.items():
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
                timeout=10,
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
        """Apply camera settings using gphoto2."""
        try:
            if not self.check_camera_connection():
                return {
                    "success": False,
                    "error": "Camera not connected",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

            applied_settings = {}
            errors = []

            setting_map = {
                'iso': 'iso',
                'aperture': 'aperture',
                'shutterspeed': 'shutterspeed',
                'whitebalance': 'whitebalance',
                'image_size': 'imageformat',  # Resolution changes require additional handling
                'quality': 'imagequality',
            }

            for key, value in settings.items():
                if key in setting_map:
                    gphoto_key = setting_map[key]
                    try:
                        if key == 'image_size':
                            self.logger.info("Image size setting requested: %s", value)
                            continue

                        result = subprocess.run(
                            ['gphoto2', '--set-config', f'{gphoto_key}={value}'],
                            capture_output=True,
                            text=True,
                            timeout=30,
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

            settings: Dict[str, Any] = {}
            configs_to_read = ['iso', 'aperture', 'shutterspeed', 'whitebalance', 'imageformat']

            for config in configs_to_read:
                try:
                    result = subprocess.run(
                        ['gphoto2', '--get-config', config],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if result.returncode == 0:
                        for line in result.stdout.split('\n'):
                            if line.strip().startswith('Current:'):
                                value = line.split(':', 1)[1].strip()
                                settings[config] = value
                                break
                except Exception as exc:  # pragma: no cover - defensive guard
                    self.logger.error("Error reading %s: %s", config, exc)

            return {
                "success": True,
                "settings": settings,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

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
            return {
                "success": False,
                "error": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    controller = GPhoto2Controller()

    print("=== Camera Options ===")
    options = controller.get_camera_options()
    print(json.dumps(options, indent=2))

    print("\n=== Current Settings ===")
    settings = controller.get_current_settings()
    print(json.dumps(settings, indent=2))
