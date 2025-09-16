#!/usr/bin/env python3

import os
import sys
import json
import time
import signal
import logging
import configparser
import os
import ssl
from dotenv import load_dotenv
from datetime import datetime
from pathlib import Path
import paho.mqtt.client as mqtt
from shared_config import write_camera_config, write_camera_command, write_camera_schedule, read_camera_config

class BMTLMQTTDaemon:
    def __init__(self):
        self.config_path = "/etc/bmtl-device/config.ini"
        self.log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        self.client = None
        self.running = True
        
        self.setup_logging()
        self.load_config()
        
    def setup_logging(self):
        os.makedirs(self.log_dir, exist_ok=True)
        log_file = os.path.join(self.log_dir, "mqtt_daemon.log")
        messages_log_file = os.path.join(self.log_dir, "mqtt_messages.log")
        
        # Main daemon logger
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger('BMTLMQTTDaemon')
        
        # Separate logger for MQTT messages
        self.message_logger = logging.getLogger('BMTLMQTTMessages')
        self.message_logger.setLevel(logging.INFO)
        message_handler = logging.FileHandler(messages_log_file)
        message_formatter = logging.Formatter('%(asctime)s - %(message)s')
        message_handler.setFormatter(message_formatter)
        self.message_logger.addHandler(message_handler)
        self.message_logger.propagate = False
        
    def load_config(self):
        # Load .env file first
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
        load_dotenv(env_path)
        
        self.config = configparser.ConfigParser()
        
        if not os.path.exists(self.config_path):
            self.logger.error(f"Configuration file not found: {self.config_path}")
            sys.exit(1)
            
        try:
            self.config.read(self.config_path)
            
            # MQTT Configuration - prioritize environment variables
            self.mqtt_host = os.getenv('MQTT_HOST', self.config.get('mqtt', 'host', fallback='localhost'))
            self.mqtt_port = int(os.getenv('MQTT_PORT', self.config.getint('mqtt', 'port', fallback=1883)))
            self.mqtt_username = os.getenv('MQTT_USERNAME', self.config.get('mqtt', 'username', fallback=''))
            self.mqtt_password = os.getenv('MQTT_PASSWORD', self.config.get('mqtt', 'password', fallback=''))
            self.mqtt_use_tls = os.getenv('MQTT_USE_TLS', self.config.get('mqtt', 'use_tls', fallback='false')).lower() == 'true'
            self.mqtt_client_id = self.config.get('mqtt', 'client_id', fallback='bmtl-device')
            
            # Device Configuration
            self.device_id = self.config.get('device', 'id', fallback='raspberry-pi')
            self.device_location = self.config.get('device', 'location', fallback='unknown')
            
            # Topics
            self.status_topic = self.config.get('topics', 'status', fallback='bmtl/device/status')
            self.heartbeat_topic = self.config.get('topics', 'heartbeat', fallback='bmtl/device/heartbeat')
            self.command_topic = self.config.get('topics', 'command', fallback='bmtl/device/command')
            self.camera_topic = self.config.get('topics', 'camera', fallback='bmtl/device/camera')
            self.subscribe_topics = self.config.get('topics', 'subscribe', fallback='#')
            
            # Subscription Configuration
            self.enable_all_topics = self.config.getboolean('subscription', 'enable_all_topics', fallback=True)
            self.log_all_messages = self.config.getboolean('subscription', 'log_all_messages', fallback=True)
            
            # Intervals
            self.heartbeat_interval = self.config.getint('intervals', 'heartbeat', fallback=60)
            self.status_interval = self.config.getint('intervals', 'status', fallback=300)
            
            self.logger.info("Configuration loaded successfully")
            
        except Exception as e:
            self.logger.error(f"Error loading configuration: {e}")
            sys.exit(1)
            
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.logger.info("Connected to MQTT broker")
            
            # Subscribe to command topic
            client.subscribe(f"{self.command_topic}/{self.device_id}")
            self.logger.info(f"Subscribed to {self.command_topic}/{self.device_id}")

            # Subscribe to camera topic
            client.subscribe(f"{self.camera_topic}/{self.device_id}")
            self.logger.info(f"Subscribed to {self.camera_topic}/{self.device_id}")
            
            # Subscribe to all topics if enabled
            if self.enable_all_topics:
                for topic in self.subscribe_topics.split(','):
                    topic = topic.strip()
                    client.subscribe(topic)
                    self.logger.info(f"Subscribed to all topics: {topic}")
            
            # Send initial status
            self.send_status("online")
            
        else:
            self.logger.error(f"Failed to connect to MQTT broker with result code {rc}")
            
    def on_disconnect(self, client, userdata, rc):
        disconnect_reasons = {
            0: "Connection successful",
            1: "Connection refused - incorrect protocol version", 
            2: "Connection refused - invalid client identifier",
            3: "Connection refused - server unavailable",
            4: "Connection refused - bad username or password",
            5: "Connection refused - not authorised",
            6: "Connection refused - reserved for future use",
            7: "Connection refused - not authorised",
            8: "Connection refused - reserved for future use"
        }
        reason = disconnect_reasons.get(rc, f"Unknown disconnect reason ({rc})")
        self.logger.warning(f"Disconnected from MQTT broker: {reason}")
        
    def on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            # Log all messages to separate file if enabled
            if self.log_all_messages:
                message_info = {
                    'topic': topic,
                    'payload': payload,
                    'qos': msg.qos,
                    'retain': msg.retain,
                    'timestamp': datetime.now().isoformat()
                }
                self.message_logger.info(json.dumps(message_info, ensure_ascii=False))
            
            self.logger.info(f"Received message on {topic}: {payload}")
            
            # Handle command messages specifically
            if topic.startswith(self.command_topic):
                try:
                    command = json.loads(payload)
                    self.handle_command(command)
                except json.JSONDecodeError:
                    self.logger.error(f"Invalid JSON in command: {payload}")

            # Handle camera messages
            elif topic.startswith(self.camera_topic):
                try:
                    camera_command = json.loads(payload)
                    self.handle_camera_command(camera_command)
                except json.JSONDecodeError:
                    self.logger.error(f"Invalid JSON in camera command: {payload}")
                
        except Exception as e:
            self.logger.error(f"Error handling message: {e}")
            
    def handle_command(self, command):
        cmd_type = command.get('type', '')
        
        if cmd_type == 'status':
            self.send_status("online")
        elif cmd_type == 'restart':
            self.logger.info("Restart command received")
            self.send_status("restarting")
            # Add restart logic here
        elif cmd_type == 'shutdown':
            self.logger.info("Shutdown command received")
            self.send_status("shutting_down")
            self.running = False
        else:
            self.logger.warning(f"Unknown command type: {cmd_type}")

    def handle_camera_command(self, command):
        """Handle camera-specific commands by writing to config files"""
        try:
            cmd_type = command.get('type', '')

            if cmd_type == 'config':
                # Camera configuration update
                config = command.get('config', {})
                write_camera_config(config)
                self.logger.info(f"Camera config updated: {config}")

            elif cmd_type == 'capture':
                # Immediate capture command
                capture_command = {
                    'type': 'capture',
                    'filename': command.get('filename'),
                    'timestamp': datetime.now().isoformat()
                }
                write_camera_command(capture_command)
                self.logger.info(f"Camera capture command sent: {capture_command}")

            elif cmd_type == 'schedule':
                # Camera schedule update
                schedule = command.get('schedule', {})
                write_camera_schedule(schedule)
                self.logger.info(f"Camera schedule updated: {schedule}")

            elif cmd_type == 'status':
                # Request camera status
                status_command = {
                    'type': 'status',
                    'timestamp': datetime.now().isoformat()
                }
                write_camera_command(status_command)
                self.logger.info("Camera status requested")

            else:
                self.logger.warning(f"Unknown camera command type: {cmd_type}")

        except Exception as e:
            self.logger.error(f"Error handling camera command: {e}")
            
    def send_status(self, status):
        try:
            payload = {
                'device_id': self.device_id,
                'location': self.device_location,
                'status': status,
                'timestamp': datetime.now().isoformat(),
                'uptime': self.get_uptime()
            }
            
            topic = f"{self.status_topic}/{self.device_id}"
            self.client.publish(topic, json.dumps(payload), retain=True)
            self.logger.info(f"Status sent: {status}")
            
        except Exception as e:
            self.logger.error(f"Error sending status: {e}")
            
    def send_heartbeat(self):
        try:
            payload = {
                'device_id': self.device_id,
                'timestamp': datetime.now().isoformat(),
                'uptime': self.get_uptime()
            }
            
            topic = f"{self.heartbeat_topic}/{self.device_id}"
            self.client.publish(topic, json.dumps(payload))
            
        except Exception as e:
            self.logger.error(f"Error sending heartbeat: {e}")
            
    def get_uptime(self):
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
                return int(uptime_seconds)
        except:
            return 0
            
    def setup_mqtt_client(self):
        self.client = mqtt.Client(client_id=self.mqtt_client_id)
        
        # Set username and password if provided
        if self.mqtt_username and self.mqtt_password:
            self.client.username_pw_set(self.mqtt_username, self.mqtt_password)
        
        # Enable TLS if configured
        if self.mqtt_use_tls:
            self.client.tls_set(ca_certs=None, certfile=None, keyfile=None, 
                              cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS, 
                              ciphers=None)
            
        # Set callbacks
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        
        # Set will message
        will_payload = {
            'device_id': self.device_id,
            'status': 'offline',
            'timestamp': datetime.now().isoformat()
        }
        self.client.will_set(f"{self.status_topic}/{self.device_id}", 
                           json.dumps(will_payload), retain=True)
        
    def signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
        
        if self.client:
            self.send_status("offline")
            self.client.disconnect()
            
    def run(self):
        # Set up signal handlers
        signal.signal(signal.SIGTERM, self.signal_handler)
        signal.signal(signal.SIGINT, self.signal_handler)
        
        self.setup_mqtt_client()
        
        try:
            self.logger.info(f"Connecting to MQTT broker at {self.mqtt_host}:{self.mqtt_port}")
            self.client.connect(self.mqtt_host, self.mqtt_port, 60)
            self.client.loop_start()
            
            last_heartbeat = 0
            last_status = 0
            
            while self.running:
                current_time = time.time()
                
                # Send heartbeat
                if current_time - last_heartbeat >= self.heartbeat_interval:
                    self.send_heartbeat()
                    last_heartbeat = current_time
                    
                # Send status update
                if current_time - last_status >= self.status_interval:
                    self.send_status("online")
                    last_status = current_time
                    
                time.sleep(1)
                
        except Exception as e:
            self.logger.error(f"Error in main loop: {e}")
            
        finally:
            if self.client:
                self.send_status("offline")
                self.client.loop_stop()
                self.client.disconnect()
                
            self.logger.info("MQTT daemon stopped")

if __name__ == "__main__":
    daemon = BMTLMQTTDaemon()
    daemon.run()