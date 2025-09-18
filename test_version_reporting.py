#!/usr/bin/env python3

"""
Test script for version reporting functionality
Tests the version manager and MQTT version reporting
"""

import json
import sys
import os

# Add the current directory to Python path to import modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_version_manager():
    """Test the version manager functionality"""
    print("Testing version manager...")

    try:
        from version_manager import version_manager, get_current_version, get_version_for_mqtt

        # Test basic version info
        version_info = version_manager.get_version_info()
        print(f"[PASS] Version manager loaded")
        print(f"  Full version info: {json.dumps(version_info, indent=2)}")

        # Test MQTT format
        mqtt_version = get_version_for_mqtt()
        print(f"[PASS] MQTT version format works")
        print(f"  MQTT format: {json.dumps(mqtt_version, indent=2)}")

        # Test simple version string
        simple_version = get_current_version()
        print(f"[PASS] Simple version string: {simple_version}")

        # Test save/load functionality
        save_path = "test_version.json"
        if version_manager.save_version_info(save_path):
            print(f"[PASS] Version info saved to {save_path}")

            loaded_info = version_manager.load_version_info(save_path)
            if loaded_info:
                print(f"[PASS] Version info loaded successfully")
                # Cleanup
                os.remove(save_path)
            else:
                print(f"[FAIL] Could not load version info")
                return False
        else:
            print(f"[FAIL] Could not save version info")
            return False

        return True

    except Exception as e:
        print(f"[FAIL] Version manager test failed: {e}")
        return False

def test_mqtt_integration():
    """Test MQTT daemon integration with version reporting"""
    print("Testing MQTT daemon version integration...")

    try:
        # Test that MQTT daemon can import version functions
        mqtt_file = "mqtt_daemon.py"
        if not os.path.exists(mqtt_file):
            print("[FAIL] MQTT daemon file not found")
            return False

        with open(mqtt_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check for version-related imports and methods
        version_features = [
            'from version_manager import',
            'get_version_for_mqtt',
            'send_version_info',
            'sw_version',
            'commit_hash'
        ]

        missing_features = []
        for feature in version_features:
            if feature in content:
                print(f"[PASS] Found version feature: {feature}")
            else:
                print(f"[FAIL] Missing version feature: {feature}")
                missing_features.append(feature)

        if missing_features:
            print(f"[FAIL] Missing features: {missing_features}")
            return False

        print("[PASS] MQTT daemon has proper version integration")
        return True

    except Exception as e:
        print(f"[FAIL] MQTT integration test failed: {e}")
        return False

def test_git_version_detection():
    """Test git version detection"""
    print("Testing git version detection...")

    try:
        from version_manager import VersionManager

        vm = VersionManager(".")  # Use current directory

        # Test git version detection
        git_version = vm.get_git_version()
        print(f"[INFO] Git version info: {json.dumps(git_version, indent=2)}")

        # Check if we got meaningful version info
        if git_version["version"] != "unknown":
            print("[PASS] Git version detection works")
        else:
            print("[WARN] Git version detection returned 'unknown' (may be normal if not in git repo)")

        return True

    except Exception as e:
        print(f"[FAIL] Git version test failed: {e}")
        return False

def test_version_file_creation():
    """Test reading VERSION file without touching repo's VERSION"""
    print("Testing VERSION file handling in temp dir...")

    try:
        import tempfile
        from version_manager import VersionManager

        test_version = "v2.1.0-test"
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a VERSION file in a temporary directory
            version_path = os.path.join(tmpdir, "VERSION")
            with open(version_path, 'w') as f:
                f.write(test_version)

            vm = VersionManager(tmpdir)
            file_version = vm.get_file_version()

            if file_version == test_version:
                print(f"[PASS] VERSION file reading works: {file_version}")
                return True
            else:
                print(f"[FAIL] VERSION file reading failed. Expected: {test_version}, Got: {file_version}")
                return False

    except Exception as e:
        print(f"[FAIL] VERSION file test failed: {e}")
        return False

def run_all_tests():
    """Run all version reporting tests"""
    print("Running version reporting tests...\n")

    tests = [
        ("Version Manager", test_version_manager),
        ("MQTT Integration", test_mqtt_integration),
        ("Git Version Detection", test_git_version_detection),
        ("VERSION File Handling", test_version_file_creation)
    ]

    results = {}
    for test_name, test_func in tests:
        print(f"\n{'='*50}")
        print(f"Testing: {test_name}")
        print('='*50)
        results[test_name] = test_func()

    print(f"\n{'='*50}")
    print("TEST SUMMARY")
    print('='*50)

    all_passed = True
    for test_name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"{test_name}: {status}")
        if not passed:
            all_passed = False

    print(f"\nOverall result: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")
    return all_passed

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
