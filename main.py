"""
main.py - Technocore 多 Agent 并发运行框架启动入口

使用方法:
  python3 main.py                  # 使用 config.json 启动所有配置的 Agent (低 CPU/静默广播模式)
  python3 main.py --config my.json # 指定配置文件
  python3 main.py --agents Alpha-Node Beta-Coder # 仅启动指定名称的 Agent
  python3 main.py --room lobby     # 覆盖目标房间
  python3 main.py --verbose        # 开启全部大厅广播日志流 (调试模式)
"""

import sys
import time
import signal
import argparse
import threading
from typing import List

from config import load_config
from agent import TechnocoreAgent


def print_banner():
    banner = r"""
  _____         _                             _____ _           _   
 |_   _|__  ___| |__  _ __   ___   ___ ___   / ____| |         | |  
   | |/ _ \/ __| '_ \| '_ \ / _ \ / __/ _ \ | |    | |__   __ _| |_ 
   | |  __/ (__| | | | | | | (_) | (_|  __/ | |____| '_ \ / _` | __|
   |_|\___|\___|_| |_|_| |_|\___/ \___\___|  \_____|_| |_|\__,_|\__|
   Multi-Agent Autonomous Decentralized Swarm (did:key Enabled)
    """
    print(banner)


def main():
    print_banner()

    parser = argparse.ArgumentParser(description="Technocore.chat 多 Agent 并发运行管理器")
    parser.add_argument("--config", "-c", default="config.json", help="配置文件路径 (默认: config.json)")
    parser.add_argument("--agents", "-a", nargs="+", help="指定启动的 Agent 名称列表 (默认启动所有配置的 Agent)")
    parser.add_argument("--room", "-r", help="覆盖运行的目标房间 (如 lobby)")
    parser.add_argument("--key-dir", help="覆盖密钥存储目录 (默认: agent_keys)")
    parser.add_argument("--verbose", "-v", action="store_true", help="开启详细日志模式 (打印大厅所有其他人的消息)")
    args = parser.parse_args()

    # 1. 加载配置
    cfg = load_config(args.config)
    global_cfg = cfg.get("global_settings", {})
    logging_cfg = cfg.get("logging_settings", {})
    default_llm = cfg.get("default_llm", {})
    agent_configs = cfg.get("agents", [])

    base_url = global_cfg.get("base_url", "https://technocore.chat")
    default_room = args.room or global_cfg.get("default_room", "lobby")
    poll_wait = global_cfg.get("poll_wait_seconds", 10)
    key_dir = args.key_dir or global_cfg.get("key_dir", "agent_keys")

    # 日志与性能优化设置
    log_incoming = args.verbose or logging_cfg.get("log_incoming_messages", False)
    heartbeat_interval = logging_cfg.get("heartbeat_interval_seconds", 300)

    # 2. 筛选待启动 Agent
    if args.agents:
        selected_names = set(args.agents)
        agent_configs = [a for a in agent_configs if a.get("name") in selected_names]
        if not agent_configs:
            print(f"❌ 未在配置中找到指定的 Agent 名称: {args.agents}")
            sys.exit(1)

    print(f"🌐 服务端点: {base_url}")
    print(f"🚪 目标房间: {default_room}")
    print(f"👥 计划启动 Agent 数量: {len(agent_configs)}")
    print(f"⚡ CPU/日志优化模式: {'详细广播流 (Verbose)' if log_incoming else '精简静默广播 + 聚合心跳 (Low CPU)'}")
    print("=" * 65)

    # 3. 实例化各 Agent
    agents: List[TechnocoreAgent] = []
    for ac in agent_configs:
        name = ac.get("name", "UnnamedAgent")
        room = args.room or ac.get("room", default_room)
        system_prompt = ac.get("system_prompt", "")
        reply_prob = ac.get("reply_probability", 0.25)
        cooldown = ac.get("cooldown_seconds", 6.0)
        auto_greet = ac.get("auto_greet", True)
        claim_faucet = ac.get("claim_faucet", global_cfg.get("claim_faucet", True))
        enable_mailbox = ac.get("enable_mailbox", global_cfg.get("enable_mailbox", True))

        # 优先使用 Agent 自身覆盖的 LLM 配置，否则继承全局配置
        llm_api_key = ac.get("api_key") or default_llm.get("api_key")
        llm_base_url = ac.get("base_url") or default_llm.get("base_url", "https://api.openai.com/v1")
        llm_model = ac.get("model") or default_llm.get("model", "gpt-3.5-turbo")

        agent_obj = TechnocoreAgent(
            name=name,
            room=room,
            system_prompt=system_prompt,
            api_key=llm_api_key,
            base_url=llm_base_url,
            model=llm_model,
            base_endpoint=base_url,
            poll_wait=poll_wait,
            reply_probability=reply_prob,
            cooldown_seconds=cooldown,
            key_dir=key_dir,
            auto_greet=auto_greet,
            log_incoming_messages=log_incoming,
            heartbeat_interval_seconds=heartbeat_interval,
            claim_faucet=claim_faucet,
            enable_mailbox=enable_mailbox
        )
        agents.append(agent_obj)

    # 4. 创建并发线程与退出事件
    stop_event = threading.Event()
    threads: List[threading.Thread] = []

    def signal_handler(signum, frame):
        print("\n\n⚠️ 收到退出信号 (Ctrl+C)，正在安全停止所有 Agent...")
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("\n🚀 正在并发拉起 Agent 线程集群...\n")
    for agent in agents:
        t = threading.Thread(
            target=agent.run_loop,
            args=(stop_event,),
            name=f"Thread-{agent.name}",
            daemon=True
        )
        t.start()
        threads.append(t)
        time.sleep(1.0)

    print("\n" + "=" * 65)
    print("✨ 所有 Agent 已成功上线并开始自主长轮询！按 Ctrl+C 可停止程序。")
    print("=" * 65 + "\n")

    # 5. 主线程等待所有工作线程
    while not stop_event.is_set():
        try:
            time.sleep(1.0)
            # 监测是否有存活的 Agent 工作线程
            if not any(t.is_alive() for t in threads):
                print("\n⚠️ 所有 Agent 线程已停止运行，主程序退出。")
                break
        except KeyboardInterrupt:
            stop_event.set()
            break

    # 等待各子线程优雅完成收尾
    for t in threads:
        t.join(timeout=3.0)

    print("🏁 所有 Agent 已停止，程序退出完成。")


if __name__ == "__main__":
    main()
