#!/usr/bin/env python3

import time
import sys
import os
from multiprocessing import Process, Queue
import signal
import logging

# Stabilize module imports even if '/opt/bmtl-device/current' flips during startup
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_RESOLVED_DIR = os.path.realpath(_BASE_DIR)
if _RESOLVED_DIR not in sys.path:
    sys.path.insert(0, _RESOLVED_DIR)

from mqtt_daemon import MqttDaemon
from device_worker import DeviceWorker

# 로거 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(processName)s] - %(levelname)s - %(message)s'
)

processes = []
task_queue = None
response_queue = None

def shutdown_handler(signum, frame):
    """Terminate child processes and flush queues."""
    logging.info("Shutdown signal received. Terminating processes.")
    if task_queue is not None:
        try:
            task_queue.put_nowait(None)
        except Exception:
            pass
    if response_queue is not None:
        try:
            response_queue.put_nowait(None)
        except Exception:
            pass
    for p in processes:
        if p.is_alive():
            p.join(5)
    for p in processes:
        if p.is_alive():
            p.terminate()
            p.join(5)
    logging.info("All processes terminated.")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    logging.info("Starting BMTL device application...")

    # 프로세스 간 통신을 위한 큐 생성
    task_queue = Queue()
    response_queue = Queue()

    # MQTT 데몬 프로세스 생성
    mqtt_daemon_process = Process(
        target=lambda: MqttDaemon(task_queue, response_queue).run(),
        name="MqttDaemon"
    )
    processes.append(mqtt_daemon_process)

    # 디바이스 워커 프로세스 생성
    device_worker_process = Process(
        target=lambda: DeviceWorker(task_queue, response_queue).run(),
        name="DeviceWorker"
    )
    processes.append(device_worker_process)

    # 프로세스 시작
    mqtt_daemon_process.start()
    logging.info("MqttDaemon process started.")
    device_worker_process.start()
    logging.info("DeviceWorker process started.")

    # 메인 프로세스는 자식 프로세스들이 종료될 때까지 대기
    try:
        while True:
            # 모든 프로세스가 살아있는지 확인
            if not all(p.is_alive() for p in processes):
                logging.error("One of the child processes has died. Shutting down.")
                # 다른 프로세스도 종료
                for p in processes:
                    if p.is_alive():
                        p.terminate()
                break
            time.sleep(5)
    except KeyboardInterrupt:
        # 이 부분은 shutdown_handler에 의해 처리되지만, 만약을 위해 남겨둠
        logging.info("Main process interrupted. Shutting down.")
        shutdown_handler(signal.SIGINT, None)

