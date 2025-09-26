#!/usr/bin/env python3

import os
import json
import fcntl
import tempfile
import contextlib
import logging
from datetime import datetime

class SafeFileConfig:
    """
    Thread-safe file configuration manager with atomic writes and caching
    Uses tmpfs for performance and implements file locking for safety
    """

    def __init__(self, base_path='/opt/bmtl-device/tmp', persistent_path='/etc/bmtl-device'):
        self.base_path = base_path  # For temporary configs (camera commands)
        self.persistent_path = persistent_path  # For persistent configs (schedules)
        self._cache = {}
        self._last_modified = {}
        self.logger = logging.getLogger('SafeFileConfig')

        # Create directories if they don't exist; fall back to user temp if needed
        try:
            os.makedirs(self.base_path, exist_ok=True)
        except Exception as e:
            fallback_base = os.path.join(tempfile.gettempdir(), 'bmtl-config')
            try:
                os.makedirs(fallback_base, exist_ok=True)
                self.logger.warning(
                    "Base path %s not writeable (%s); falling back to %s",
                    self.base_path, e, fallback_base,
                )
                self.base_path = fallback_base
            except Exception:
                # Re-raise original if fallback also fails
                raise

        try:
            os.makedirs(self.persistent_path, exist_ok=True)
        except Exception as e:
            fallback_etc = os.path.join(tempfile.gettempdir(), 'bmtl-etc')
            try:
                os.makedirs(fallback_etc, exist_ok=True)
                self.logger.warning(
                    "Persistent path %s not writeable (%s); falling back to %s",
                    self.persistent_path, e, fallback_etc,
                )
                self.persistent_path = fallback_etc
            except Exception:
                raise

    def _get_file_path(self, filename):
        """Get appropriate file path based on file type"""
        # Persistent configs go to /etc/bmtl-device
        persistent_files = [
            'camera_schedule.json',
            'camera_default_config.json',
            'device_settings.json'
        ]

        if filename in persistent_files:
            return os.path.join(self.persistent_path, filename)
        else:
            # Temporary configs (commands, stats) go to base_path
            return os.path.join(self.base_path, filename)

    @contextlib.contextmanager
    def _file_lock(self, filepath):
        """Context manager for file locking"""
        lock_path = filepath + '.lock'
        try:
            with open(lock_path, 'w') as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                yield
        except Exception as e:
            self.logger.error(f"File lock error for {filepath}: {e}")
            raise
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                if os.path.exists(lock_path):
                    os.unlink(lock_path)
            except:
                pass

    def _atomic_write_json(self, filepath, data):
        """Atomic write operation to prevent data corruption"""
        dir_path = os.path.dirname(filepath)

        # Create temporary file in same directory for atomic rename
        temp_fd, temp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
        try:
            with os.fdopen(temp_fd, 'w') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())  # Force write to disk

            # Atomic rename operation
            os.rename(temp_path, filepath)
            self.logger.debug(f"Atomically wrote config to {filepath}")

        except Exception as e:
            # Clean up temp file on error
            try:
                os.unlink(temp_path)
            except:
                pass
            raise e

    def write_config(self, filename, data):
        """
        Thread-safe config write with atomic operation

        Args:
            filename (str): Config file name (e.g., 'camera_config.json')
            data (dict): Configuration data to write
        """
        filepath = self._get_file_path(filename)

        try:
            with self._file_lock(filepath):
                # Add metadata
                config_data = {
                    'timestamp': datetime.now().isoformat(),
                    'data': data
                }

                self._atomic_write_json(filepath, config_data)

                # Update cache
                self._cache[filename] = data
                self._last_modified[filename] = datetime.now()

                # Include resolved path in logs to aid debugging across tmp/persistent dirs
                self.logger.info(f"Config written: {filename} -> {filepath}")

        except Exception as e:
            # Include resolved path for easier diagnosis
            try:
                self.logger.error(f"Failed to write config {filename} at {filepath}: {e}")
            except Exception:
                self.logger.error(f"Failed to write config {filename}: {e}")
            raise

    def read_config(self, filename, use_cache=True):
        """
        Thread-safe config read with caching

        Args:
            filename (str): Config file name
            use_cache (bool): Whether to use cached data if available

        Returns:
            dict: Configuration data
        """
        filepath = self._get_file_path(filename)

        # Check cache first
        if use_cache and filename in self._cache:
            # Check if file was modified externally
            try:
                file_mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                cache_mtime = self._last_modified.get(filename)

                if cache_mtime and file_mtime <= cache_mtime:
                    return self._cache[filename]
            except OSError:
                pass  # File doesn't exist, will be handled below

        # Check if file exists before attempting to lock/read
        if not os.path.exists(filepath):
            self.logger.debug(f"Config file not found: {filename}")
            return {}

        # Read from file
        try:
            with self._file_lock(filepath):
                with open(filepath, 'r') as f:
                    config_data = json.load(f)

                # Extract actual data (skip metadata)
                if isinstance(config_data, dict) and 'data' in config_data:
                    data = config_data['data']
                else:
                    data = config_data  # Backward compatibility

                # Update cache
                self._cache[filename] = data
                self._last_modified[filename] = datetime.now()

                return data

        except FileNotFoundError:
            self.logger.debug(f"Config file disappeared during read: {filename}")
            return {}
        except Exception as e:
            self.logger.error(f"Failed to read config {filename}: {e}")
            raise

    def config_exists(self, filename):
        """Check if config file exists"""
        filepath = self._get_file_path(filename)
        return os.path.exists(filepath)

    def delete_config(self, filename):
        """Delete config file and clear from cache"""
        filepath = self._get_file_path(filename)

        try:
            with self._file_lock(filepath):
                if os.path.exists(filepath):
                    os.unlink(filepath)

                # Clear from cache
                self._cache.pop(filename, None)
                self._last_modified.pop(filename, None)

                self.logger.info(f"Config deleted: {filename}")

        except Exception as e:
            self.logger.error(f"Failed to delete config {filename}: {e}")
            raise

    def list_configs(self):
        """List all available config files from both directories"""
        try:
            files = []

            # List from temp directory
            try:
                for filename in os.listdir(self.base_path):
                    if filename.endswith('.json') and not filename.endswith('.lock'):
                        files.append(filename)
            except FileNotFoundError:
                pass

            # List from persistent directory
            try:
                for filename in os.listdir(self.persistent_path):
                    if filename.endswith('.json') and not filename.endswith('.lock'):
                        if filename not in files:  # Avoid duplicates
                            files.append(filename)
            except FileNotFoundError:
                pass

            return files
        except Exception as e:
            self.logger.error(f"Failed to list configs: {e}")
            return []

    def clear_cache(self):
        """Clear all cached configurations"""
        self._cache.clear()
        self._last_modified.clear()
        self.logger.info("Configuration cache cleared")


# Global instance for easy import
config_manager = SafeFileConfig()


# Convenience functions
def write_camera_config(config):
    """Write camera configuration"""
    config_manager.write_config('camera_config.json', config)

def read_camera_config():
    """Read camera configuration"""
    return config_manager.read_config('camera_config.json')

def write_camera_command(command):
    """Write camera command"""
    config_manager.write_config('camera_command.json', command)

def read_camera_command():
    """Read camera command"""
    return config_manager.read_config('camera_command.json')

def write_camera_schedule(schedule):
    """Write camera schedule"""
    config_manager.write_config('camera_schedule.json', schedule)

def read_camera_schedule():
    """Read camera schedule"""
    return config_manager.read_config('camera_schedule.json')


if __name__ == "__main__":
    # Test the configuration manager
    logging.basicConfig(level=logging.DEBUG)

    # Test write/read
    test_config = {
        'iso': 100,
        'shutter_speed': '1/60',
        'aperture': 'f/2.8'
    }

    write_camera_config(test_config)
    read_config = read_camera_config()

    print(f"Written: {test_config}")
    print(f"Read: {read_config}")
    print(f"Match: {test_config == read_config}")
