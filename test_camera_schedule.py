#!/usr/bin/env python3

"""Tests for camera daemon schedule normalization and execution."""

import os
import sys
from datetime import datetime


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from camera_daemon import BMTLCameraDaemon  # noqa: E402
from shared_config import config_manager, read_camera_schedule  # noqa: E402


class DummyCamera:
    """Simple camera stub that records capture invocations."""

    def __init__(self):
        self.captures = []

    def capture_photo(self, filename=None):
        timestamp = datetime.now().isoformat()
        self.captures.append(timestamp)
        return {
            'success': True,
            'filename': f'dummy_{len(self.captures)}.jpg',
            'filepath': f'/tmp/dummy_{len(self.captures)}.jpg',
            'timestamp': timestamp,
        }

    def update_capture_stats(self, success):  # pragma: no cover - stub method
        pass


def _delete_config_if_exists(filename):
    try:
        if config_manager.config_exists(filename):
            config_manager.delete_config(filename)
    except Exception:
        pass


def test_schedule_planning_flow():
    """Simulate a schedule update and ensure captures follow the plan."""
    print("Setting up camera daemon schedule planning test...")

    daemon = BMTLCameraDaemon()
    daemon.camera = DummyCamera()

    # Ensure a clean slate for schedule files
    _delete_config_if_exists('camera_schedule.json')
    _delete_config_if_exists('schedule_settings.json')
    config_manager.clear_cache()

    schedule_settings = {
        'start_time': '08:00',
        'end_time': '09:00',
        'capture_interval': '30',
    }

    # Simulate the config manager persisting new settings
    config_manager.write_config('schedule_settings.json', schedule_settings)

    base_time = datetime(2024, 5, 20, 7, 45)
    daemon.update_schedule(schedule_settings, current_time=base_time)

    schedule = read_camera_schedule()
    if not schedule:
        print("[FAIL] Schedule plan was not persisted")
        return False

    expected_first = datetime(2024, 5, 20, 8, 0)
    actual_first = datetime.fromisoformat(schedule['next_capture'])
    if actual_first != expected_first:
        print(f"[FAIL] Expected first capture at {expected_first}, got {actual_first}")
        return False
    print("[PASS] Initial schedule aligns next capture with window start")

    # Before the start of the window - no capture should occur
    daemon.check_and_execute_schedule(schedule, current_time=datetime(2024, 5, 20, 7, 50))
    if daemon.camera.captures:
        print("[FAIL] Capture triggered before the start window")
        return False
    schedule = read_camera_schedule()
    if datetime.fromisoformat(schedule['next_capture']) != expected_first:
        print("[FAIL] Next capture shifted unexpectedly before window start")
        return False
    print("[PASS] No capture scheduled prior to the active window")

    # Window start - should capture immediately
    daemon.check_and_execute_schedule(schedule, current_time=datetime(2024, 5, 20, 8, 0))
    schedule = read_camera_schedule()
    if len(daemon.camera.captures) != 1:
        print("[FAIL] Capture not triggered at window start")
        return False
    if datetime.fromisoformat(schedule['last_capture']) != datetime(2024, 5, 20, 8, 0):
        print("[FAIL] Last capture timestamp mismatch after first capture")
        return False
    if datetime.fromisoformat(schedule['next_capture']) != datetime(2024, 5, 20, 8, 30):
        print("[FAIL] Next capture not advanced to the next interval")
        return False
    print("[PASS] Capture triggered at window start and next interval planned")

    # Mid-window check - ensure no extra capture occurs
    daemon.check_and_execute_schedule(schedule, current_time=datetime(2024, 5, 20, 8, 20))
    schedule = read_camera_schedule()
    if len(daemon.camera.captures) != 1:
        print("[FAIL] Unexpected capture before interval elapsed")
        return False
    if datetime.fromisoformat(schedule['next_capture']) != datetime(2024, 5, 20, 8, 30):
        print("[FAIL] Next capture shifted before interval elapsed")
        return False
    print("[PASS] Interval enforcement prevents early captures")

    # Next interval - expect second capture
    daemon.check_and_execute_schedule(schedule, current_time=datetime(2024, 5, 20, 8, 30))
    schedule = read_camera_schedule()
    if len(daemon.camera.captures) != 2:
        print("[FAIL] Second capture not triggered at interval boundary")
        return False
    if datetime.fromisoformat(schedule['last_capture']) != datetime(2024, 5, 20, 8, 30):
        print("[FAIL] Last capture timestamp mismatch after second capture")
        return False
    if datetime.fromisoformat(schedule['next_capture']) != datetime(2024, 5, 20, 9, 0):
        print("[FAIL] Next capture not planned for window end")
        return False
    print("[PASS] Interval capture executed on schedule")

    # Window end - capture allowed at the final slot
    daemon.check_and_execute_schedule(schedule, current_time=datetime(2024, 5, 20, 9, 0))
    schedule = read_camera_schedule()
    if len(daemon.camera.captures) != 3:
        print("[FAIL] Final capture not triggered at window end")
        return False
    if datetime.fromisoformat(schedule['last_capture']) != datetime(2024, 5, 20, 9, 0):
        print("[FAIL] Last capture timestamp mismatch at window end")
        return False
    if datetime.fromisoformat(schedule['next_capture']) != datetime(2024, 5, 21, 8, 0):
        print("[FAIL] Next capture not rolled to the next day")
        return False
    print("[PASS] Capture performed at window close and plan advanced to next day")

    return True


def run_all_tests():
    print("Running camera schedule tests...\n")
    return test_schedule_planning_flow()


if __name__ == '__main__':
    if run_all_tests():
        sys.exit(0)
    sys.exit(1)
