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
# Create a temporary service file with current user
cp bmtl-device.service bmtl-device.service.tmp
sed -i "s/User=pi/User=$USER/g" bmtl-device.service.tmp
sed -i "s/Group=pi/Group=$(id -gn)/g" bmtl-device.service.tmp
sudo cp bmtl-device.service.tmp /etc/systemd/system/bmtl-device.service
rm bmtl-device.service.tmp
sudo systemctl daemon-reload
sudo systemctl enable bmtl-device

# Remove old log directory references (logs are now in app directory)
# Create log directory is handled by the application itself

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