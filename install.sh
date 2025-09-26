#!/bin/bash

# BMTL Device MQTT Client Daemon Installation Script
# For Raspberry Pi

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

SLOT_PRIMARY="v1"
SLOT_SECONDARY="v2"

determine_slots() {
    local current_link="$1"

    if [ -L "$current_link" ]; then
        local target
        target=$(readlink -f "$current_link")
        local basename_target
        basename_target=$(basename "$target")

        case "$basename_target" in
            "$SLOT_PRIMARY")
                ACTIVE_SLOT="$SLOT_PRIMARY"
                INACTIVE_SLOT="$SLOT_SECONDARY"
                ;;
            "$SLOT_SECONDARY")
                ACTIVE_SLOT="$SLOT_SECONDARY"
                INACTIVE_SLOT="$SLOT_PRIMARY"
                ;;
            *)
                echo "Unknown active slot '$basename_target'. Please repair /opt/bmtl-device/current." >&2
                exit 1
                ;;
        esac
    else
        ACTIVE_SLOT="$SLOT_PRIMARY"
        INACTIVE_SLOT="$SLOT_SECONDARY"
    fi

    ACTIVE_DIR="$APP_DIR/$ACTIVE_SLOT"
    INACTIVE_DIR="$APP_DIR/$INACTIVE_SLOT"
}

sync_release() {
    local source_dir="$1"
    local target_dir="$2"

    if [ "$(readlink -f "$source_dir")" = "$(readlink -f "$target_dir")" ]; then
        echo "⚠️  Source ($source_dir) and target ($target_dir) are the same directory."
        echo "    Please run this installer from a separate release checkout (e.g., git clone into /tmp or ~/bmtl-device)."
        exit 1
    fi

    echo "📁 Syncing application files to $target_dir"
    rm -rf "$target_dir"
    mkdir -p "$target_dir"

    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete \
            --exclude 'venv/' \
            --exclude '__pycache__/' \
            --exclude '*.pyc' \
            "$source_dir"/ "$target_dir"/
    else
        (cd "$source_dir" && tar cf - . --exclude='venv' --exclude='__pycache__' --exclude='*.pyc') | (cd "$target_dir" && tar xf -)
    fi
}

# Enable safety mode for updates
BACKUP_DIR="/opt/bmtl-device-backup"
UPDATE_MODE="${1:-install}"  # install, update, or rollback

echo "🔧 BMTL Device MQTT Client Daemon - Mode: $UPDATE_MODE"

# Function to create backup before update
create_backup() {
    if [ "$UPDATE_MODE" = "update" ] && [ -d "/opt/bmtl-device" ]; then
        echo "💾 Creating backup before update..."
        sudo rm -rf "$BACKUP_DIR" 2>/dev/null || true
        sudo cp -r "/opt/bmtl-device" "$BACKUP_DIR"
        echo "✅ Backup created at $BACKUP_DIR"
    fi
}

# Function to rollback if update fails
rollback_on_failure() {
    if [ "$UPDATE_MODE" = "update" ] && [ -d "$BACKUP_DIR" ]; then
        echo "🔄 Rolling back to previous version..."
        sudo systemctl stop bmtl-device bmtl-camera 2>/dev/null || true
        sudo rm -rf "/opt/bmtl-device"
        sudo mv "$BACKUP_DIR" "/opt/bmtl-device"
        sudo systemctl start bmtl-device bmtl-camera 2>/dev/null || true
        echo "✅ Rollback completed"
        exit 1
    fi
}

# Trap to handle failures during update
trap 'if [ "$UPDATE_MODE" = "update" ]; then rollback_on_failure; fi' ERR

# Check if services are already running and stop them
if systemctl is-active --quiet bmtl-device; then
    echo "🛑 Stopping existing bmtl-device service..."
    sudo systemctl stop bmtl-device
fi

if systemctl is-active --quiet bmtl-camera; then
    echo "🛑 Stopping existing bmtl-camera service..."
    sudo systemctl stop bmtl-camera
fi

# Disable existing services if they exist
if systemctl is-enabled --quiet bmtl-device 2>/dev/null; then
    echo "🔄 Disabling existing bmtl-device service..."
    sudo systemctl disable bmtl-device
fi

if systemctl is-enabled --quiet bmtl-camera 2>/dev/null; then
    echo "🔄 Disabling existing bmtl-camera service..."
    sudo systemctl disable bmtl-camera
fi

# Create backup before starting (only for updates)
create_backup

# Check if running on Raspberry Pi
if ! grep -q "Raspberry Pi\|BCM" /proc/cpuinfo; then
    echo "⚠️  Warning: This script is designed for Raspberry Pi"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Update system packages
echo "📦 Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install required packages
echo "📦 Installing dependencies..."
sudo apt install -y python3 python3-pip python3-venv git gphoto2 libgphoto2-dev python3-rpi.gpio rsync build-essential python3-dev libffi-dev

# Create application directory
APP_DIR="/opt/bmtl-device"
CURRENT_LINK="$APP_DIR/current"

echo "📁 Creating application directory: $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown "$USER":"$(id -gn)" "$APP_DIR"

# Determine active/inactive slots for blue/green layout
determine_slots "$CURRENT_LINK"

if [ "$UPDATE_MODE" = "update" ]; then
    TARGET_DIR="$INACTIVE_DIR"
    echo "🔄 Updating inactive slot: $(basename "$TARGET_DIR")"

    echo "🔄 Ensuring services are stopped for update..."
    sudo systemctl stop bmtl-device bmtl-camera 2>/dev/null || true
    sudo systemctl disable bmtl-device bmtl-camera 2>/dev/null || true
else
    TARGET_DIR="$ACTIVE_DIR"
    echo "📋 Installing fresh copy into $(basename "$TARGET_DIR")"
fi

sync_release "$SCRIPT_DIR" "$TARGET_DIR"
mkdir -p "$TARGET_DIR/logs"

echo "🐍 Creating Python virtual environment..."
rm -rf "$TARGET_DIR/venv"
python3 -m venv "$TARGET_DIR/venv"

source "$TARGET_DIR/venv/bin/activate"

echo "📦 Installing Python dependencies..."
pip install --upgrade pip
pip install -r "$TARGET_DIR/requirements.txt"

echo "🧪 Validating bytecode compilation..."
"$TARGET_DIR/venv/bin/python" -m compileall "$TARGET_DIR"

deactivate

echo "🔍 Verifying venv installation..."
if [ ! -f "$TARGET_DIR/venv/bin/python" ]; then
    echo "❌ Virtual environment was not created properly"
    if [ "$UPDATE_MODE" = "update" ]; then
        echo "💥 Update failed due to venv creation failure, initiating rollback..."
        rollback_on_failure
    fi
    exit 1
fi

echo "🔗 Updating current symlink"
ln -sfn "$TARGET_DIR" "$CURRENT_LINK"

# Create config directory
sudo mkdir -p /etc/bmtl-device

# Setup configuration file
echo "⚙️  Setting up configuration..."

# Extract device ID from hostname
HOSTNAME=$(hostname)
DEVICE_ID="01"  # default

if [[ $HOSTNAME =~ bmotion([0-9]+) ]]; then
    DEVICE_ID=$(printf "%02d" ${BASH_REMATCH[1]})
    echo "📍 Detected device ID from hostname: $DEVICE_ID"
else
    echo "⚠️  Could not extract device ID from hostname '$HOSTNAME', using default: $DEVICE_ID"
fi

# Copy and customize configuration
if [ ! -f /etc/bmtl-device/config.ini ]; then
    sudo cp "$TARGET_DIR/config.ini" /etc/bmtl-device/
fi

# Update device ID and sitename in config
sudo sed -i "s/^id = .*/id = $DEVICE_ID/" /etc/bmtl-device/config.ini
# Ensure sitename reflects the device hostname
sudo sed -i "s/^sitename = .*/sitename = $HOSTNAME/" /etc/bmtl-device/config.ini

echo "📝 Configuration updated:"
echo "   Device ID: $DEVICE_ID"
echo "   Site Name: $HOSTNAME"
echo "   Config file: /etc/bmtl-device/config.ini"

# Install systemd services
echo "🔧 Installing systemd services..."

# Install MQTT daemon service
cp bmtl-device.service bmtl-device.service.tmp
sed -i "s/User=pi/User=$USER/g" bmtl-device.service.tmp
sed -i "s/Group=pi/Group=$(id -gn)/g" bmtl-device.service.tmp
sudo cp bmtl-device.service.tmp /etc/systemd/system/bmtl-device.service
rm bmtl-device.service.tmp

# Install Camera daemon service
cp bmtl-camera.service bmtl-camera.service.tmp
sed -i "s/User=pi/User=$USER/g" bmtl-camera.service.tmp
sed -i "s/Group=pi/Group=$(id -gn)/g" bmtl-camera.service.tmp
sudo cp bmtl-camera.service.tmp /etc/systemd/system/bmtl-camera.service
rm bmtl-camera.service.tmp

sudo systemctl daemon-reload
sudo systemctl enable bmtl-device
sudo systemctl enable bmtl-camera

# Start the services
echo "🚀 Starting bmtl services..."
sudo systemctl start bmtl-device
sudo systemctl start bmtl-camera

# Wait a moment for services to start
sleep 3

# Check service status
echo "📊 Checking service status..."
MQTT_STATUS=$(systemctl is-active bmtl-device 2>/dev/null || echo "inactive")
CAMERA_STATUS=$(systemctl is-active bmtl-camera 2>/dev/null || echo "inactive")

echo "MQTT Daemon: $MQTT_STATUS"
echo "Camera Daemon: $CAMERA_STATUS"

if [[ "$MQTT_STATUS" == "active" && "$CAMERA_STATUS" == "active" ]]; then
    echo "✅ Installation completed successfully! Both services are running."

    # Clean up backup on successful update
    if [ "$UPDATE_MODE" = "update" ] && [ -d "$BACKUP_DIR" ]; then
        echo "🧹 Cleaning up backup (update successful)..."
        sudo rm -rf "$BACKUP_DIR"
    fi

    echo ""
    echo "📝 To view logs:"
    echo "   sudo journalctl -u bmtl-device -f    # MQTT daemon"
    echo "   sudo journalctl -u bmtl-camera -f    # Camera daemon"
    echo ""
    echo "📝 To view application logs:"
    echo "   tail -f $CURRENT_LINK/logs/mqtt_daemon.log"
    echo "   tail -f $CURRENT_LINK/logs/camera_daemon.log"
    echo ""
    echo "⚙️  Configuration file: /etc/bmtl-device/config.ini"
    echo "⚙️  Environment file: $CURRENT_LINK/.env"
    echo "📂 Camera config directory: /tmp/bmtl-config"

    # Disable error trap since we succeeded
    trap - ERR
else
    echo "⚠️  Some services failed to start. Check status with:"
    echo "   sudo systemctl status bmtl-device"
    echo "   sudo systemctl status bmtl-camera"
    echo "   sudo journalctl -u bmtl-device -f"
    echo "   sudo journalctl -u bmtl-camera -f"

    # Trigger rollback on failure
    if [ "$UPDATE_MODE" = "update" ]; then
        echo "💥 Update failed, initiating rollback..."
        rollback_on_failure
    fi
    exit 1
fi
