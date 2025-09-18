#!/bin/bash

# BMTL Device MQTT Client Daemon Installation Script
# For Raspberry Pi

set -e

echo "🔧 Installing BMTL Device MQTT Client Daemon..."

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
sudo apt install -y python3 python3-pip python3-venv git gphoto2 libgphoto2-dev python3-rpi.gpio

# Create application directory
APP_DIR="/opt/bmtl-device"
echo "📁 Creating application directory: $APP_DIR"
sudo mkdir -p $APP_DIR
sudo chown $USER:$USER $APP_DIR

# Check if this is an update (app directory already exists with files)
if [ -d "$APP_DIR/venv" ] && [ -f "$APP_DIR/mqtt_daemon.py" ]; then
    echo "🔄 Updating existing installation..."
    # Copy updated files
    cp -f *.py $APP_DIR/ 2>/dev/null || true
    cp -f *.service $APP_DIR/ 2>/dev/null || true
    cp -f *.ini $APP_DIR/ 2>/dev/null || true
    cp -f *.sh $APP_DIR/ 2>/dev/null || true
    cp -f .env.example $APP_DIR/ 2>/dev/null || true

    # Ensure both services are stopped and disabled during update
    echo "🔄 Ensuring services are stopped for update..."
    sudo systemctl stop bmtl-device bmtl-camera 2>/dev/null || true
    sudo systemctl disable bmtl-device bmtl-camera 2>/dev/null || true
else
    echo "📋 Installing fresh copy..."
    # Copy all application files
    cp -r . $APP_DIR/
fi
cd $APP_DIR

# Create Python virtual environment (if not exists) or reuse existing
if [ ! -d "venv" ]; then
    echo "🐍 Creating Python virtual environment..."
    python3 -m venv venv
else
    echo "🐍 Using existing Python virtual environment..."
fi
source venv/bin/activate

# Install Python dependencies
echo "📦 Installing Python dependencies..."
pip install paho-mqtt configparser python-dotenv inotify-simple

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
    sudo cp config.ini /etc/bmtl-device/
fi

# Update device ID and sitename in config
sudo sed -i "s/^id = .*/id = $DEVICE_ID/" /etc/bmtl-device/config.ini
sudo sed -i "s/^location = .*/sitename = $HOSTNAME/" /etc/bmtl-device/config.ini

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
    echo ""
    echo "📝 To view logs:"
    echo "   sudo journalctl -u bmtl-device -f    # MQTT daemon"
    echo "   sudo journalctl -u bmtl-camera -f    # Camera daemon"
    echo ""
    echo "📝 To view application logs:"
    echo "   tail -f $APP_DIR/logs/mqtt_daemon.log"
    echo "   tail -f $APP_DIR/logs/camera_daemon.log"
    echo ""
    echo "⚙️  Configuration file: /etc/bmtl-device/config.ini"
    echo "⚙️  Environment file: $APP_DIR/.env"
    echo "📂 Camera config directory: /tmp/bmtl-config"
else
    echo "⚠️  Some services failed to start. Check status with:"
    echo "   sudo systemctl status bmtl-device"
    echo "   sudo systemctl status bmtl-camera"
    echo "   sudo journalctl -u bmtl-device -f"
    echo "   sudo journalctl -u bmtl-camera -f"
fi