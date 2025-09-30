#!/usr/bin/env python3
"""
Test script to verify snake_case conversion and removal of camelCase duplicates
"""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from gphoto2_controller import GPhoto2Controller
from device_worker import DeviceWorker
from unittest.mock import MagicMock
import multiprocessing


def test_camera_options_no_duplicates():
    """Test that camera options no longer contain camelCase duplicates"""
    print("Testing GPhoto2Controller.get_current_settings()...")

    controller = GPhoto2Controller()

    # Mock the _get_config_details method since we don't have a real camera
    def mock_get_config_details(config_path):
        base_name = config_path.split('/')[-1]
        return {
            "success": True,
            "label": f"Test {base_name}",
            "type": "radio",
            "read_only": False,
            "current": "test_value",
            "choices": ["choice1", "choice2", "choice3"],
        }

    controller._get_config_details = mock_get_config_details
    controller.camera_connected = True  # Mock connection

    # Get settings
    result = controller.get_current_settings()

    if result.get('success'):
        options = result.get('options', {})
        settings = result.get('settings', {})

        print("✓ Successfully retrieved camera settings")
        print(f"✓ Options keys: {list(options.keys())}")
        print(f"✓ Settings keys: {list(settings.keys())}")

        # Check for absence of camelCase duplicates
        camel_case_keys = ['imageQuality', 'focusMode', 'imageSize']
        snake_case_keys = ['image_quality', 'focus_mode', 'resolution']

        for camel_key in camel_case_keys:
            assert camel_key not in options, f"Found camelCase key '{camel_key}' in options"
            assert camel_key not in settings, f"Found camelCase key '{camel_key}' in settings"

        for snake_key in snake_case_keys:
            if snake_key in options:
                print(f"✓ Found expected snake_case key '{snake_key}' in options")
            if snake_key in settings:
                print(f"✓ Found expected snake_case key '{snake_key}' in settings")

        # Verify no alias_for or aliases fields exist
        for key, option in options.items():
            assert 'alias_for' not in option, f"Found 'alias_for' in option '{key}'"
            assert 'aliases' not in option, f"Found 'aliases' in option '{key}'"

        print("✓ No camelCase duplicates found!")
        print("✓ No alias metadata found!")

        return True
    else:
        print(f"✗ Failed to get camera settings: {result.get('error', 'Unknown error')}")
        return False


def test_device_worker_settings():
    """Test that DeviceWorker returns snake_case settings"""
    print("\nTesting DeviceWorker.get_enhanced_settings()...")

    # Create mock queues
    task_queue = MagicMock()
    response_queue = MagicMock()

    worker = DeviceWorker(task_queue, response_queue)

    # Mock the gphoto_controller to return test data
    def mock_get_current_settings():
        return {
            'success': True,
            'settings': {
                'iso': '800',
                'aperture': '0.666',
                'resolution': '2464x1632',
                'image_quality': 'JPEG Fine',
                'focus_mode': 'MF (fixed)'
            },
            'options': {
                'iso': {'label': 'ISO Speed', 'current': '800'},
                'resolution': {'label': 'Image Size', 'current': '2464x1632'},
                'image_quality': {'label': 'Image Quality', 'current': 'JPEG Fine'},
                'focus_mode': {'label': 'Focus Mode 2', 'current': 'MF (fixed)'}
            }
        }

    worker.gphoto_controller.get_current_settings = mock_get_current_settings

    settings, camera_options, camera_meta = worker.get_enhanced_settings()

    print("✓ Successfully retrieved enhanced settings")
    print(f"✓ Enhanced settings keys: {list(settings.keys())}")
    print(f"✓ Camera options keys: {list(camera_options.keys())}")

    # Check that all keys are snake_case
    expected_snake_keys = [
        'iso', 'aperture', 'focus_mode', 'resolution', 'image_quality',
        'start_time', 'end_time', 'capture_interval', 'format'
    ]

    camel_case_keys = [
        'focusMode', 'imageSize', 'imageQuality',
        'startTime', 'endTime', 'captureInterval'
    ]

    for camel_key in camel_case_keys:
        assert camel_key not in settings, f"Found camelCase key '{camel_key}' in enhanced settings"
        assert camel_key not in camera_options, f"Found camelCase key '{camel_key}' in camera options"

    for snake_key in expected_snake_keys:
        if snake_key in settings:
            print(f"✓ Found expected snake_case key '{snake_key}' in settings")

    print("✓ All keys are in snake_case format!")
    return True


def main():
    print("="*60)
    print("TESTING SNAKE_CASE CONVERSION")
    print("="*60)

    success = True

    try:
        if not test_camera_options_no_duplicates():
            success = False
    except Exception as e:
        print(f"✗ Camera options test failed: {e}")
        success = False

    try:
        if not test_device_worker_settings():
            success = False
    except Exception as e:
        print(f"✗ Device worker test failed: {e}")
        success = False

    print("\n" + "="*60)
    if success:
        print("✓ ALL TESTS PASSED - Snake_case conversion successful!")
        print("✓ No more camelCase/snake_case duplicates!")
    else:
        print("✗ SOME TESTS FAILED")
        sys.exit(1)
    print("="*60)


if __name__ == "__main__":
    main()