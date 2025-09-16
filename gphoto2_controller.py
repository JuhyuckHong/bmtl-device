#!/usr/bin/env python3

import os
import json
import subprocess
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

class GPhoto2Controller:
    """gphoto2를 이용한 카메라 옵션 조회 및 제어"""

    def __init__(self):
        self.logger = logging.getLogger('GPhoto2Controller')
        self.camera_connected = False
        self.check_camera_connection()

    def check_camera_connection(self) -> bool:
        """카메라 연결 상태 확인"""
        try:
            result = subprocess.run(['gphoto2', '--auto-detect'],
                                  capture_output=True, text=True, timeout=10)
            self.camera_connected = result.returncode == 0 and 'usb:' in result.stdout
            if self.camera_connected:
                self.logger.info("Camera connected successfully")
            else:
                self.logger.warning("No camera detected")
            return self.camera_connected
        except Exception as e:
            self.logger.error(f"Error checking camera connection: {e}")
            self.camera_connected = False
            return False

    def get_camera_options(self) -> Dict[str, Any]:
        """카메라 지원 옵션 조회 (bmtl/request/options에 응답)"""
        try:
            if not self.check_camera_connection():
                return {
                    "success": False,
                    "error": "Camera not connected",
                    "timestamp": datetime.now().isoformat()
                }

            options = {}

            # 지원 해상도 조회
            options["supported_resolutions"] = self._get_image_formats()

            # ISO 범위 조회
            options["iso_range"] = self._get_iso_options()

            # 조리개 범위 조회
            options["aperture_range"] = self._get_aperture_options()

            # 셔터 속도 범위 조회
            options["shutterspeed_range"] = self._get_shutterspeed_options()

            # 화이트밸런스 옵션 조회
            options["whitebalance_options"] = self._get_whitebalance_options()

            # 이미지 포맷 조회
            options["supported_formats"] = ["JPG", "RAW"]

            return {
                "success": True,
                "options": options,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            self.logger.error(f"Error getting camera options: {e}")
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def _get_config_choices(self, config_name: str) -> List[str]:
        """특정 설정의 선택 가능한 값들 조회"""
        try:
            result = subprocess.run(['gphoto2', '--get-config', config_name],
                                  capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                return []

            choices = []
            lines = result.stdout.split('\n')
            in_choices = False

            for line in lines:
                line = line.strip()
                if line.startswith('Choice:'):
                    # Choice: 0 100 형태에서 값 추출
                    parts = line.split(' ', 2)
                    if len(parts) >= 3:
                        choices.append(parts[2])
                elif 'Choice' in line and 'Current' not in line:
                    in_choices = True
                elif in_choices and line.startswith('Current'):
                    break

            return choices
        except Exception as e:
            self.logger.error(f"Error getting config choices for {config_name}: {e}")
            return []

    def _get_iso_options(self) -> List[int]:
        """ISO 옵션 조회"""
        try:
            choices = self._get_config_choices('iso')
            iso_values = []
            for choice in choices:
                try:
                    # ISO 값만 추출 (예: "100", "200", "Auto" 등)
                    if choice.isdigit():
                        iso_values.append(int(choice))
                except:
                    continue
            return sorted(iso_values) if iso_values else [100, 200, 400, 800, 1600, 3200, 6400]
        except:
            return [100, 200, 400, 800, 1600, 3200, 6400]

    def _get_aperture_options(self) -> List[str]:
        """조리개 옵션 조회"""
        try:
            choices = self._get_config_choices('aperture')
            # f/ 형태의 조리개 값만 필터링
            aperture_values = [choice for choice in choices if choice.startswith('f/')]
            return aperture_values if aperture_values else ["f/1.4", "f/2.8", "f/4", "f/5.6", "f/8", "f/11", "f/16"]
        except:
            return ["f/1.4", "f/2.8", "f/4", "f/5.6", "f/8", "f/11", "f/16"]

    def _get_shutterspeed_options(self) -> List[str]:
        """셔터 속도 옵션 조회"""
        try:
            choices = self._get_config_choices('shutterspeed')
            return choices if choices else ["1/4000", "1/2000", "1/1000", "1/500", "1/250", "1/125", "1/60", "1/30"]
        except:
            return ["1/4000", "1/2000", "1/1000", "1/500", "1/250", "1/125", "1/60", "1/30"]

    def _get_whitebalance_options(self) -> List[str]:
        """화이트밸런스 옵션 조회"""
        try:
            choices = self._get_config_choices('whitebalance')
            return choices if choices else ["Auto", "Daylight", "Shade", "Cloudy", "Tungsten", "Fluorescent"]
        except:
            return ["Auto", "Daylight", "Shade", "Cloudy", "Tungsten", "Fluorescent"]

    def _get_image_formats(self) -> List[str]:
        """이미지 포맷 및 해상도 조회"""
        try:
            choices = self._get_config_choices('imageformat')
            # 해상도 정보 추출
            resolutions = []
            for choice in choices:
                if 'x' in choice and any(char.isdigit() for char in choice):
                    # "Large Fine JPEG (5184x3456)" 같은 형태에서 해상도 추출
                    import re
                    match = re.search(r'(\d+x\d+)', choice)
                    if match:
                        resolutions.append(match.group(1))

            return resolutions if resolutions else ["1920x1080", "1280x720", "5184x3456"]
        except:
            return ["1920x1080", "1280x720", "5184x3456"]

    def apply_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """카메라 설정 적용 (bmtl/set/settings에서 받은 설정)"""
        try:
            if not self.check_camera_connection():
                return {
                    "success": False,
                    "error": "Camera not connected",
                    "timestamp": datetime.now().isoformat()
                }

            applied_settings = {}
            errors = []

            # 설정 매핑
            setting_map = {
                'iso': 'iso',
                'aperture': 'aperture',
                'shutterspeed': 'shutterspeed',
                'whitebalance': 'whitebalance',
                'image_size': 'imageformat',  # 해상도는 imageformat으로 처리
                'quality': 'imagequality'
            }

            for key, value in settings.items():
                if key in setting_map:
                    gphoto_key = setting_map[key]
                    try:
                        # 특별한 처리가 필요한 설정들
                        if key == 'image_size':
                            # 해상도 설정은 더 복잡할 수 있으므로 로그만 남기고 넘어감
                            self.logger.info(f"Image size setting requested: {value}")
                            continue

                        result = subprocess.run(['gphoto2', '--set-config', f'{gphoto_key}={value}'],
                                              capture_output=True, text=True, timeout=30)

                        if result.returncode == 0:
                            applied_settings[key] = value
                            self.logger.info(f"Applied {key}={value}")
                        else:
                            error_msg = f"Failed to set {key}={value}: {result.stderr}"
                            errors.append(error_msg)
                            self.logger.error(error_msg)

                    except Exception as e:
                        error_msg = f"Error setting {key}={value}: {str(e)}"
                        errors.append(error_msg)
                        self.logger.error(error_msg)

            return {
                "success": len(errors) == 0,
                "applied_settings": applied_settings,
                "errors": errors,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            self.logger.error(f"Error applying camera settings: {e}")
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def get_current_settings(self) -> Dict[str, Any]:
        """현재 카메라 설정값 조회 (bmtl/request/settings에 응답)"""
        try:
            if not self.check_camera_connection():
                return {
                    "success": False,
                    "error": "Camera not connected",
                    "timestamp": datetime.now().isoformat()
                }

            settings = {}

            # 주요 설정값 조회
            configs_to_read = ['iso', 'aperture', 'shutterspeed', 'whitebalance', 'imageformat']

            for config in configs_to_read:
                try:
                    result = subprocess.run(['gphoto2', '--get-config', config],
                                          capture_output=True, text=True, timeout=10)
                    if result.returncode == 0:
                        # Current: 값 추출
                        for line in result.stdout.split('\n'):
                            if line.strip().startswith('Current:'):
                                value = line.split(':', 1)[1].strip()
                                settings[config] = value
                                break
                except Exception as e:
                    self.logger.error(f"Error reading {config}: {e}")

            return {
                "success": True,
                "settings": settings,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            self.logger.error(f"Error getting current settings: {e}")
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def camera_power_toggle(self) -> Dict[str, Any]:
        """카메라 전원 토글 (bmtl/request/camera-on-off에 응답)"""
        try:
            # gphoto2로는 실제 전원 제어가 어려우므로 연결 상태만 확인
            connected = self.check_camera_connection()

            return {
                "success": True,
                "message": "Camera power status checked",
                "current_state": "on" if connected else "off",
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

if __name__ == "__main__":
    # 테스트 코드
    logging.basicConfig(level=logging.INFO)

    controller = GPhoto2Controller()

    # 옵션 조회 테스트
    print("=== Camera Options ===")
    options = controller.get_camera_options()
    print(json.dumps(options, indent=2, ensure_ascii=False))

    # 현재 설정 조회 테스트
    print("\n=== Current Settings ===")
    settings = controller.get_current_settings()
    print(json.dumps(settings, indent=2, ensure_ascii=False))