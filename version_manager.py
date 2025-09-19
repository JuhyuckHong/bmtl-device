#!/usr/bin/env python3

"""
Version management utility for BMTL Device
Provides software version information using git
"""

import os
import shutil
import subprocess
import json
import logging
from datetime import datetime

class VersionManager:
    """Manages software version information"""

    def __init__(self, app_dir="/opt/bmtl-device"):
        self.app_dir = app_dir
        self.logger = logging.getLogger('VersionManager')
        self.git_cmd = self._find_git_command()

    def get_git_version(self):
        """Get version information from git"""
        try:
            # If git is not available or directory is not a git repo, skip to fallback
            if not self.git_cmd or not os.path.isdir(os.path.join(self.app_dir, '.git')):
                # No error in normal operation on devices without git
                return self.get_fallback_version()

            original_cwd = os.getcwd()
            os.chdir(self.app_dir)

            # Get git commit hash (short)
            result = subprocess.run([self.git_cmd, 'rev-parse', '--short', 'HEAD'],
                                  capture_output=True, text=True, timeout=10)
            git_hash = result.stdout.strip() if result.returncode == 0 else "unknown"

            # Get git commit hash (full)
            result = subprocess.run([self.git_cmd, 'rev-parse', 'HEAD'],
                                  capture_output=True, text=True, timeout=10)
            git_hash_full = result.stdout.strip() if result.returncode == 0 else "unknown"

            # Get latest git tag
            result = subprocess.run([self.git_cmd, 'describe', '--tags', '--abbrev=0'],
                                  capture_output=True, text=True, timeout=10)
            git_tag = result.stdout.strip() if result.returncode == 0 else "no-tag"

            # Get git describe (tag + commits since tag + hash)
            result = subprocess.run([self.git_cmd, 'describe', '--tags', '--dirty'],
                                  capture_output=True, text=True, timeout=10)
            git_describe = result.stdout.strip() if result.returncode == 0 else f"{git_tag}-{git_hash}"

            # Get commit date
            result = subprocess.run([self.git_cmd, 'log', '-1', '--format=%ci'],
                                  capture_output=True, text=True, timeout=10)
            commit_date = result.stdout.strip() if result.returncode == 0 else "unknown"

            # Get branch name
            result = subprocess.run([self.git_cmd, 'branch', '--show-current'],
                                  capture_output=True, text=True, timeout=10)
            branch = result.stdout.strip() if result.returncode == 0 else "unknown"

            os.chdir(original_cwd)

            return {
                "version": git_describe,  # Main version string
                "tag": git_tag,
                "commit_hash": git_hash,
                "commit_hash_full": git_hash_full,
                "commit_date": commit_date,
                "branch": branch,
                "timestamp": datetime.now().isoformat()
            }

        except Exception as e:
            self.logger.error(f"Error getting git version: {e}")
            return self.get_fallback_version()

    def _find_git_command(self):
        """Find git command in system PATH"""
        try:
            # Use shutil.which() for cross-platform compatibility and security
            git_path = shutil.which('git')
            if git_path:
                # Verify git is working
                try:
                    result = subprocess.run([git_path, '--version'],
                                          capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        return git_path
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    pass

            # Fallback: try common git paths
            git_paths = ['/usr/bin/git', '/usr/local/bin/git', '/bin/git']
            for git_path in git_paths:
                try:
                    result = subprocess.run([git_path, '--version'],
                                          capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        return git_path
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    continue

            return None
        except Exception as e:
            self.logger.error(f"Error finding git command: {e}")
            return None

    def get_fallback_version(self):
        """Get fallback version when git is not available"""
        return {
            "version": "unknown",
            "tag": "unknown",
            "commit_hash": "unknown",
            "commit_hash_full": "unknown",
            "commit_date": "unknown",
            "branch": "unknown",
            "timestamp": datetime.now().isoformat()
        }

    def get_file_version(self):
        """Get version from VERSION file if it exists"""
        version_file = os.path.join(self.app_dir, "VERSION")
        try:
            if os.path.exists(version_file):
                with open(version_file, 'r') as f:
                    return f.read().strip()
            return None
        except Exception as e:
            self.logger.error(f"Error reading VERSION file: {e}")
            return None

    def get_version_info(self):
        """Get comprehensive version information"""
        version_info = self.get_git_version()

        # Add file version if available; if git unknown, promote file version to main
        file_version = self.get_file_version()
        if file_version:
            if not version_info.get("version") or version_info.get("version") in ("unknown", "no-tag", ""):
                version_info["version"] = file_version
            else:
                version_info["file_version"] = file_version

        return version_info

    def save_version_info(self, filepath=None):
        """Save current version info to file"""
        if not filepath:
            filepath = os.path.join(self.app_dir, "current_version.json")

        version_info = self.get_version_info()

        try:
            with open(filepath, 'w') as f:
                json.dump(version_info, f, indent=2)
            self.logger.info(f"Version info saved to {filepath}")
            return True
        except Exception as e:
            self.logger.error(f"Error saving version info: {e}")
            return False

    def load_version_info(self, filepath=None):
        """Load version info from file"""
        if not filepath:
            filepath = os.path.join(self.app_dir, "current_version.json")

        try:
            if os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    return json.load(f)
            return None
        except Exception as e:
            self.logger.error(f"Error loading version info: {e}")
            return None

    def format_version_for_mqtt(self):
        """Format version info for MQTT transmission"""
        version_info = self.get_version_info()

        # Create compact version for MQTT
        return {
            "sw_version": version_info["version"],
            "commit_hash": version_info["commit_hash"],
            "branch": version_info["branch"],
            "update_time": version_info["timestamp"]
        }

# Global instance
version_manager = VersionManager()

# Convenience functions
def get_current_version():
    """Get current software version string"""
    return version_manager.get_version_info()["version"]

def get_version_for_mqtt():
    """Get version info formatted for MQTT"""
    return version_manager.format_version_for_mqtt()

if __name__ == "__main__":
    # Test version manager
    logging.basicConfig(level=logging.INFO)

    vm = VersionManager(".")  # Use current directory for testing

    print("=== Version Information ===")
    version_info = vm.get_version_info()
    print(json.dumps(version_info, indent=2))

    print("\n=== MQTT Format ===")
    mqtt_version = vm.format_version_for_mqtt()
    print(json.dumps(mqtt_version, indent=2))

    print(f"\n=== Simple Version ===")
    print(f"Version: {get_current_version()}")
