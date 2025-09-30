# Repository Guidelines

## Project Structure & Module Organization

-   Core runtime `mqtt_daemon.py`: MQTT connectivity, heartbeat, remote commands.
-   Supporting modules: `device_mqtt_handler.py`, `shared_config.py`, `version_manager.py`.
-   Camera helper: `camera_daemon.py`.
-   (Simulator removed) Manual QA via device logs and MQTT topics.
-   Tests at repo root: `test_update_protocol.py`, `test_version_reporting.py`. Services/scripts: `bmtl-device.service`, `bmtl-camera.service`, `install.sh`. Logs under `logs/`.

## Build, Test, and Development Commands

-   Create venv: `python -m venv venv`; activate `venv\Scripts\activate` (Windows) or `source venv/bin/activate` (Linux/macOS).
-   Install deps: `pip install -r requirements.txt`.
-   Run daemon locally: `python mqtt_daemon.py` (logs to `logs/`).
-   Run tests: `python test_update_protocol.py` and `python test_version_reporting.py` (non-zero on failure, detailed PASS/FAIL output).

## Coding Style & Naming Conventions

-   PEP 8; four-space indentation.
-   Names: snake_case for functions/variables; PascalCase for classes.
-   Use module-level constants for MQTT topics and filenames.
-   Keep shebangs in executable scripts.
-   Use `logging`; avoid `print` outside tests.
-   Add concise docstrings for new classes and command handlers.

## Testing Guidelines

-   Extend existing test scripts; mirror current import/introspection style.
-   Add assertions for new MQTT topics/commands in `test_update_protocol.py`.
-   Version behavior belongs in `test_version_reporting.py`.
-   Run both scripts before PR; include PASS/FAIL snippets in PR.

## Commit & Pull Request Guidelines

-   Conventional commits: `feat:`, `fix:`, `refactor:`, `chore:`; subject ≤ 70 chars.
-   PRs include description, linked issues, and test outputs or rationale.
-   Include screenshots for UI changes (if any).
-   Call out deployment impacts when touching `config.ini`, service files, or `install.sh`.

## Configuration & Security Tips

-   Secrets live in `/etc/bmtl-device/config.ini`; never commit credentials.
-   Use `.env` only for local experiments; keep it untracked.
-   When adding config keys, update sample `config.ini` and helpers for compatibility.
-   Validate permissions on generated logs and backups.
-   Avoid privileged operations without safeguards and clear prompts.

# Post-Generation Review Rules

After every code generation, perform the following checks:

1. **Syntax**

    - Verify all parentheses/braces/brackets are correctly opened and closed.
    - Verify all quotes (' , " , """ ) are properly closed.

2. **Clean Code Principles**

    - Follow existing project patterns and folder structures.
    - Apply the **One Source of Truth** rule (no duplicated definitions).
    - Replace magic numbers/strings with constants.
    - Implement proper error and exception handling.
    - Respect the **Single Responsibility Principle** (one function = one purpose).
    - Place reusable code in shared modules.

3. **Review Mindset**
    - Conduct the review from a zero-base perspective, not assuming correctness.
    - Do not hide or omit suggested fixes — always propose necessary improvements clearly.

After review, always **output a checklist result**.
