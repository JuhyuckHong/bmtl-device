# Repository Guidelines

## Project Structure & Module Organization
Core runtime code lives in `mqtt_daemon.py`, which coordinates MQTT connectivity, heartbeat publishing, and remote commands. Supporting modules such as `device_mqtt_handler.py`, `shared_config.py`, and `version_manager.py` encapsulate device command handling, safe file I/O, and version metadata. The `camera_daemon.py` helper keeps camera-specific logic isolated, while `web_simulator.py` plus `templates/simulator.html` provide a lightweight browser simulator for manual QA. Tests reside at the repository root (`test_update_protocol.py`, `test_version_reporting.py`) and focus on safeguarding update, configuration, and versioning workflows. Service definitions (`bmtl-device.service`, `bmtl-camera.service`) and deployment scripts (`install.sh`) sit alongside the code for quick packaging.

## Build, Test, and Development Commands
Create a local environment with `python -m venv venv` and activate it via `venv\Scripts\activate` (or `source venv/bin/activate` on Linux). Install dependencies with `pip install -r requirements.txt`. Run the daemon locally using `python mqtt_daemon.py`; logs will collect under `logs/`. Execute regression checks with `python test_update_protocol.py` and `python test_version_reporting.py`; both scripts exit non-zero on failure and print detailed PASS/FAIL sections.

## Coding Style & Naming Conventions
Match the existing PEP 8 style: four-space indentation, descriptive snake_case for functions and module-level variables, and PascalCase for classes. Keep top-of-file shebangs for executable scripts and prefer module-level constants for MQTT topics or filenames. Inline logging should rely on the configured `logging` module; avoid bare `print` outside tests. Add concise docstrings when introducing new classes or command handlers.

## Testing Guidelines
Extend the current test scripts rather than introducing ad-hoc checks; each scenario uses file introspection and lightweight imports, so mirror that approach when adding coverage. New MQTT topics or commands should gain explicit assertions in `test_update_protocol.py`, while version-related logic belongs in `test_version_reporting.py`. Run both scripts before opening a PR, and document any manual simulator checks you performed.

## Commit & Pull Request Guidelines
Follow the observed conventional-commit prefixes (`feat:`, `fix:`, `refactor:`, `chore:`) and keep the summary under 70 characters. Provide context in the body when behavior changes or configuration formats evolve. Pull requests should include: a short description of the change, linked issue IDs when available, test output snippets (or reasoning if tests are skipped), and screenshots for simulator or UI updates. Highlight deployment impacts, especially when touching `config.ini`, service files, or `install.sh`.

## Configuration & Security Tips
Sensitive MQTT credentials live in `/etc/bmtl-device/config.ini`; never hard-code secrets or commit environment-specific files. Use `.env` only for local experimentation and keep it out of version control. When adding new configuration keys, update both the sample `config.ini` and the shared config helpers to preserve backward compatibility. Validate file permissions on generated logs and backups, and avoid commands in scripts that require elevated privileges without clear safeguards.
