"""
kibble_main.py - Technocore Kibble (kibble-v1) Worker 守护进程主入口

用法:
  python kibble_main.py [--config kibble_config.json] [--verbose]
"""

import os
import sys
import json
import signal
import argparse
from pathlib import Path

from kibble_agent import KibbleAgent


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        print(f"[Error] 配置文件不存在: {config_path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Technocore Kibble PoUI Worker Daemon")
    parser.add_argument("--config", default="kibble_config.json", help="Path to kibble config file")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()

    config = load_config(args.config)
    agent = KibbleAgent(config)

    def handle_signal(signum, frame):
        print(f"\n[kibble_main] 捕获信号 {signum}，正在通知 Agent 优雅停机...")
        agent.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        agent.run()
    except Exception as e:
        print(f"[kibble_main] 致命错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
