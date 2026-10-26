"""
config.py - 配置加载与管理模块
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, List


DEFAULT_CONFIG: Dict[str, Any] = {
    "global_settings": {
        "base_url": "https://technocore.chat",
        "default_room": "lobby",
        "poll_wait_seconds": 10,
        "key_dir": "agent_keys",
        "claim_faucet": True,
        "enable_mailbox": True
    },
    "logging_settings": {
        "log_level": "INFO",
        "log_incoming_messages": False,
        "heartbeat_interval_seconds": 300,
        "max_log_size_mb": 10,
        "backup_count": 3,
        "log_dir": "logs"
    },
    "default_llm": {
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-3.5-turbo",
        "temperature": 0.7
    },
    "agents": [
        {
            "name": "Alpha-Node",
            "room": "lobby",
            "system_prompt": "You are Alpha-Node, an autonomous crypto network monitor. You discuss did:key, $FLOP token epochs, and consensus.",
            "reply_probability": 0.2,
            "cooldown_seconds": 8.0,
            "auto_greet": True
        },
        {
            "name": "Beta-Coder",
            "room": "lobby",
            "system_prompt": "You are Beta-Coder, an AI systems engineer. You talk about GPU clusters, throughput, and distributed agents.",
            "reply_probability": 0.25,
            "cooldown_seconds": 7.0,
            "auto_greet": True
        }
    ]
}


def load_config(config_path: str = "config.json") -> Dict[str, Any]:
    """从本地文件加载配置，若不存在则创建默认配置"""
    path = Path(config_path)
    if not path.exists():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        print(f"[Config] 📝 未找到配置文件，已生成默认配置: {config_path}")
        return DEFAULT_CONFIG

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        print(f"[Config] ⚠️ 解析 {config_path} 失败 ({e})，使用默认内存配置")
        return DEFAULT_CONFIG
