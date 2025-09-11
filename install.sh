#!/bin/bash

# BMTL Device MQTT Client Daemon Installation Script
# For Raspberry Pi

set -e

echo "🔧 Installing BMTL Device MQTT Client Daemon..."

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

# Copy application files
echo "📋 Copying application files..."
cp -r . $APP_DIR/
cd $APP_DIR

# Create Python virtual environment
echo "🐍 Setting up Python virtual environment..."
python3 -m venv venv
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
sudo cp bmtl-device.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bmtl-device

# Create log directory
sudo mkdir -p /var/log/bmtl-device
sudo chown $USER:$USER /var/log/bmtl-device

echo "✅ Installation completed successfully!"
echo ""
echo "🚀 To start the service:"
echo "   sudo systemctl start bmtl-device"
echo ""
echo "📊 To check service status:"
echo "   sudo systemctl status bmtl-device"
echo ""
echo "📝 To view logs:"
echo "   sudo journalctl -u bmtl-device -f"
echo ""
echo "⚙️  Configuration file: /etc/bmtl-device/config.ini"