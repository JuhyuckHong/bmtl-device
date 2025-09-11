#!/bin/bash

# BMTL Device MQTT Client Daemon Installation Script
# For Raspberry Pi

set -e

echo "🔧 Installing BMTL Device MQTT Client Daemon..."

# Check if service is already running and stop it
if systemctl is-active --quiet bmtl-device; then
    echo "🛑 Stopping existing bmtl-device service..."
    sudo systemctl stop bmtl-device
fi

# Disable existing service if it exists
if systemctl is-enabled --quiet bmtl-device 2>/dev/null; then
    echo "🔄 Disabling existing bmtl-device service..."
    sudo systemctl disable bmtl-device
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
sudo apt install -y python3 python3-pip python3-venv git

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
pip install paho-mqtt configparser python-dotenv

# Create config directory
sudo mkdir -p /etc/bmtl-device

# Copy default configuration if it doesn't exist
if [ ! -f /etc/bmtl-device/config.ini ]; then
    echo "⚙️  Creating default configuration..."
    sudo cp config.ini /etc/bmtl-device/
    echo "📝 Please edit /etc/bmtl-device/config.ini to configure your MQTT settings"
fi

# Install systemd service
echo "🔧 Installing systemd service..."
# Create a temporary service file with current user
cp bmtl-device.service bmtl-device.service.tmp
sed -i "s/User=pi/User=$USER/g" bmtl-device.service.tmp
sed -i "s/Group=pi/Group=$(id -gn)/g" bmtl-device.service.tmp
sudo cp bmtl-device.service.tmp /etc/systemd/system/bmtl-device.service
rm bmtl-device.service.tmp
sudo systemctl daemon-reload
sudo systemctl enable bmtl-device

# Start the service
echo "🚀 Starting bmtl-device service..."
sudo systemctl start bmtl-device

# Wait a moment for service to start
sleep 2

# Check service status
echo "📊 Checking service status..."
if systemctl is-active --quiet bmtl-device; then
    echo "✅ Installation completed successfully! Service is running."
    echo ""
    echo "📝 To view logs:"
    echo "   sudo journalctl -u bmtl-device -f"
    echo ""
    echo "📝 To view application logs:"
    echo "   tail -f $APP_DIR/logs/mqtt_daemon.log"
    echo ""
    echo "⚙️  Configuration file: /etc/bmtl-device/config.ini"
    echo "⚙️  Environment file: $APP_DIR/.env"
else
    echo "⚠️  Service installed but failed to start. Check status with:"
    echo "   sudo systemctl status bmtl-device"
    echo "   sudo journalctl -u bmtl-device -f"
fi