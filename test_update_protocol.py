#!/usr/bin/env python3

"""
Test script to verify MQTT remote update protocol implementation
This script checks the code paths without actually triggering updates
"""

import json
import sys
import os

# Add the current directory to Python path to import modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_mqtt_daemon_update_handler():
    """Test that the MQTT daemon has proper update handling"""
    print("Testing MQTT daemon update protocol implementation...")

    try:
        # Simple file-based test instead of importing
        mqtt_file = "mqtt_daemon.py"
        if not os.path.exists(mqtt_file):
            print("[FAIL] MQTT daemon file not found")
            return False

        with open(mqtt_file, 'r', encoding='utf-8') as f:
            content = f.read()

        required_methods = ['handle_software_update', '_execute_software_update']
        required_topics = ['bmtl/sw-update']

        all_found = True
        for method in required_methods:
            if f"def {method}" in content:
                print(f"[PASS] Found method: {method}")
            else:
                print(f"[FAIL] Missing method: {method}")
                all_found = False

        for topic in required_topics:
            if topic in content:
                print(f"[PASS] Found topic handling: {topic}")
            else:
                print(f"[FAIL] Missing topic handling: {topic}")
                all_found = False

        if all_found:
            print("[PASS] MQTT daemon update methods are implemented")
        return all_found

    except Exception as e:
        print(f"[FAIL] Could not test MQTT daemon: {e}")
        return False

def test_install_script_safety():
    """Test that install.sh has safety features"""
    print("Testing install.sh safety features...")

    install_script = "install.sh"
    if not os.path.exists(install_script):
        print(f"[FAIL] Install script not found: {install_script}")
        return False

    with open(install_script, 'r', encoding='utf-8') as f:
        content = f.read()

    safety_features = [
        'create_backup',
        'rollback_on_failure',
        'trap',
        'UPDATE_MODE',
        'BACKUP_DIR'
    ]

    missing_features = []
    for feature in safety_features:
        if feature in content:
            print(f"[PASS] Found safety feature: {feature}")
        else:
            print(f"[FAIL] Missing safety feature: {feature}")
            missing_features.append(feature)

    if missing_features:
        print(f"[FAIL] Missing safety features: {missing_features}")
        return False

    print("[PASS] Install script has proper safety features")
    return True

def test_config_structure():
    """Test configuration file structure"""
    print("Testing configuration structure...")

    config_file = "config.ini"
    if not os.path.exists(config_file):
        print(f"[FAIL] Config file not found: {config_file}")
        return False

    import configparser
    config = configparser.ConfigParser()
    config.read(config_file)

    required_sections = ['mqtt', 'device', 'topics']
    for section in required_sections:
        if config.has_section(section):
            print(f"[PASS] Found config section: {section}")
        else:
            print(f"[FAIL] Missing config section: {section}")
            return False

    # Check if device section has required fields
    if config.has_option('device', 'id') and config.has_option('device', 'sitename'):
        print("[PASS] Device configuration is complete")
    else:
        print("[FAIL] Device configuration is incomplete")
        return False

    return True

def test_shared_config():
    """Test shared configuration system"""
    print("Testing shared configuration system...")

    try:
        # Simple file-based test
        shared_config_file = "shared_config.py"
        if not os.path.exists(shared_config_file):
            print("[FAIL] Shared config file not found")
            return False

        with open(shared_config_file, 'r', encoding='utf-8') as f:
            content = f.read()

        required_classes = ['SafeFileConfig']
        required_functions = ['write_camera_config', 'read_camera_config', 'write_camera_command']

        all_found = True
        for cls in required_classes:
            if f"class {cls}" in content:
                print(f"[PASS] Found class: {cls}")
            else:
                print(f"[FAIL] Missing class: {cls}")
                all_found = False

        for func in required_functions:
            if f"def {func}" in content:
                print(f"[PASS] Found function: {func}")
            else:
                print(f"[FAIL] Missing function: {func}")
                all_found = False

        if all_found:
            print("[PASS] Shared config system structure is correct")
        return all_found

    except Exception as e:
        print(f"[FAIL] Shared config test failed: {e}")
        return False

def run_all_tests():
    """Run all tests"""
    print("Running remote update protocol tests...\n")

    tests = [
        ("MQTT Daemon Update Handler", test_mqtt_daemon_update_handler),
        ("Install Script Safety", test_install_script_safety),
        ("Configuration Structure", test_config_structure),
        ("Shared Configuration", test_shared_config)
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