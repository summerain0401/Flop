"""
trader_main.py - Close Call (close-1) 专属交易智能体集群启动入口 (方案 1: 独立进程运行)

启动 Delta-Maker (做市与网格策略) 与 Epsilon-Sniper (异动狙击与吃单套利策略)
与原有大厅问答集群 (Alpha-Node, Beta-Coder, Gamma-Sage) 物理隔离并行运行，互不干扰。
"""

import sys
import json
import signal
import argparse
import threading
from pathlib import Path
from typing import List

from trading_agent import CloseCallTradingAgent

BANNER = r"""
  _____ _                 _____      _ _   _______          _           
 / ____| |               / ____|    | | | |__   __|        | |          
| |    | | ___  ___  ___| |     __ _| | |    | |_ __ __ _  __| | ___ _ __ 
| |    | |/ _ \/ __|/ _ \ |    / _` | | |    | | '__/ _` |/ _` |/ _ \ '__|
| |____| | (_) \__ \  __/ |___| (_| | | |    | | | | (_| | (_| |  __/ |   
 \_____|_|\___/|___/\___|\_____\__,_|_|_|    |_|_|  \__,_|\__,_|\___|_|   
     Close Call (close-1) Autonomous Multi-Agent Trading Swarm
"""


def load_trading_config(config_path: str = "trading_config.json") -> dict:
    """加载交易集群配置"""
    cfg_file = Path(config_path)
    if not cfg_file.exists():
        print(f"❌ 找不到配置文件: {config_path}")
        sys.exit(1)
    with open(cfg_file, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Close Call (close-1) Trading Swarm Runner")
    parser.add_argument("-c", "--config", default="trading_config.json", help="交易配置文件路径")
    parser.add_argument("--room", default=None, help="覆盖默认交易房间 (默认: close1)")
    parser.add_argument("--traders", nargs="+", default=None, help="仅启动指定交易智能体 (如: Delta-Maker)")
    args = parser.parse_args()

    print(BANNER)
    config = load_trading_config(args.config)
    global_cfg = config.get("global_settings", {})
    logging_cfg = config.get("logging_settings", {})
    trader_configs = config.get("traders", [])

    if args.traders:
        trader_configs = [tc for tc in trader_configs if tc.get("name") in args.traders]
        if not trader_configs:
            print(f"❌ 未在配置中找到指定的交易智能体: {args.traders}")
            sys.exit(1)

    base_url = global_cfg.get("base_url", "https://technocore.chat")
    trading_room = args.room or global_cfg.get("trading_room", "close1")
    key_dir = global_cfg.get("key_dir", "agent_keys")
    poll_wait = int(global_cfg.get("poll_wait_seconds", 10))
    claim_faucet = bool(global_cfg.get("claim_faucet", True))
    claim_passport = bool(global_cfg.get("claim_passport", True))
    join_close1 = bool(global_cfg.get("join_close1", True))
    enable_mailbox = bool(global_cfg.get("enable_mailbox", True))
    hb_interval = int(logging_cfg.get("heartbeat_interval_seconds", 300))

    print(f"🌐 服务端点: {base_url}")
    print(f"📈 交易房间: {trading_room}")
    print(f"👥 启动交易智能体数量: {len(trader_configs)}")
    for tc in trader_configs:
        print(f"   • {tc.get('name')}: 策略={tc.get('strategy')}, 冷却={tc.get('cooldown_seconds')}s")
    print("=" * 70)

    # 实例化交易智能体
    traders: List[CloseCallTradingAgent] = []
    for tc in trader_configs:
        name = tc.get("name", "UnnamedTrader")
        strategy = tc.get("strategy", "maker_grid")

        agent_obj = CloseCallTradingAgent(
            name=name,
            strategy=strategy,
            room=trading_room,
            base_endpoint=base_url,
            poll_wait=poll_wait,
            key_dir=key_dir,
            claim_faucet=claim_faucet,
            enable_mailbox=enable_mailbox,
            claim_passport=claim_passport,
            join_close1=join_close1,
            strategy_config=tc,
            heartbeat_interval_seconds=hb_interval
        )
        traders.append(agent_obj)

    stop_event = threading.Event()
    threads: List[threading.Thread] = []

    for trader in traders:
        t = threading.Thread(
            target=trader.run_loop,
            args=(stop_event,),
            name=f"Thread-{trader.name}",
            daemon=True
        )
        threads.append(t)
        t.start()

    print("✨ 所有交易智能体已在独立线程启动！按 Ctrl+C 可优雅退出。")
    print("=" * 70)

    def handle_signal(sig, frame):
        print(f"\n🛑 收到退出信号 ({sig})，正在通知所有交易智能体安全停机...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while not stop_event.is_set():
            alive_threads = [t for t in threads if t.is_alive()]
            if not alive_threads:
                print("⚠️ 所有交易智能体线程均已退出。")
                break
            stop_event.wait(1.0)
    except KeyboardInterrupt:
        stop_event.set()

    print("⏳ 等待交易线程收尾...")
    for t in threads:
        t.join(timeout=3.0)

    print("✅ 交易集群已优雅退出。")


if __name__ == "__main__":
    main()
