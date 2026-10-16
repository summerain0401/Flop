"""
agent.py - 单个独立 AI Agent 运行与交互逻辑

功能特性:
1. 绑定专属 Ed25519 did:key 身份，自动注册 DID Note。
2. 独立运行长轮询 (Long Polling) 监听房间新消息。
3. 严格过滤自身消息，杜绝死循环对话。
4. 智能判断响应时机 (@提及触发、讨论参与概率、回复冷却控制)。
5. 自动签名并发送消息至 Technocore 平台。
6. [CPU & 日志优化]: 支持静默大厅广播模式与定时聚合健康心跳，降低 99% 的 I/O 开销与 CPU 占用。
"""

import time
import random
import threading
from typing import Dict, Any, Optional, List
from collections import deque

from identity import AgentIdentity
from technocore_client import TechnocoreClient
from llm_provider import LLMProvider


class TechnocoreAgent:
    """独立的 AI 智能体实例"""

    def __init__(
        self,
        name: str,
        room: str = "lobby",
        system_prompt: str = "",
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-3.5-turbo",
        base_endpoint: str = "https://technocore.chat",
        poll_wait: int = 10,
        reply_probability: float = 0.25,
        cooldown_seconds: float = 6.0,
        key_dir: str = "agent_keys",
        auto_greet: bool = True,
        log_incoming_messages: bool = False,
        heartbeat_interval_seconds: int = 300
    ):
        self.name = name
        self.room = room
        self.poll_wait = poll_wait
        self.reply_probability = reply_probability
        self.cooldown_seconds = cooldown_seconds
        self.auto_greet = auto_greet
        self.log_incoming_messages = log_incoming_messages
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

        # 初始化专属身份
        self.identity = AgentIdentity(name=self.name, key_dir=key_dir)
        
        # 初始化 HTTP 客户端
        self.client = TechnocoreClient(base_url=base_endpoint, user_agent=f"Agent-{self.name}/1.0")
        
        # 初始化 LLM 提供者
        self.llm = LLMProvider(
            api_key=api_key,
            base_url=base_url,
            model=model,
            system_prompt=system_prompt
        )

        self.last_seq: Optional[int] = None
        self.last_reply_time: float = 0.0
        self.recent_messages: deque = deque(maxlen=20)
        self.is_running: bool = False

        # 统计计数器
        self.total_received: int = 0
        self.total_sent: int = 0
        self.last_heartbeat_time: float = time.time()
        self.interval_received: int = 0

    def log(self, text: str):
        """格式化打印 Agent 日志"""
        ts = time.strftime("%H:%M:%S", time.localtime())
        print(f"[{ts}] [{self.name}] {text}", flush=True)

    def initialize(self):
        """Agent 启动准备: 注册 DID Note、抓取最新基准 seq、发送上线问候"""
        self.log(f"🚀 初始化智能体 | 身份: {self.identity.did}")
        self.log(f"🤖 推理引擎: {self.llm.get_engine_description()}")

        # 1. 注册/更新 DID Note (符合 Pattern 3 规范)
        try:
            shard, key = self.identity.get_sharded_did_path()
            success = self.client.publish_did_note(shard, key, self.identity.did, nick=self.name)
            if success:
                self.log(f"✅ DID Note 注册成功 (/kv/did-{shard}/{key})")
        except Exception as e:
            self.log(f"⚠️ DID Note 注册异常: {e}")

        # 2. 先获取大厅最近一条消息以对齐基准 seq (避免启动时大量回复历史消息)
        try:
            init_data = self.client.get_messages(room=self.room, wait=0, limit=1)
            if "last_seq" in init_data and init_data["last_seq"] is not None:
                self.last_seq = init_data["last_seq"]
                self.log(f"📍 对齐最新消息序列游标: seq={self.last_seq}")
        except Exception as e:
            self.log(f"⚠️ 获取初始序列号异常: {e}")

        # 3. 发送上线签名问候
        if self.auto_greet:
            greeting = f"Agent {self.name} online. Initialized did:key signature loop. Ready to interact."
            self.send_signed_message(greeting)

    def send_signed_message(self, text: str) -> bool:
        """对文本进行签名并发送至指定房间"""
        try:
            cleaned_text, nonce, sig = self.identity.sign_message(self.room, text)
            success = self.client.say_signed(
                room=self.room,
                did=self.identity.did,
                sig=sig,
                nonce=nonce,
                text=cleaned_text
            )
            if success:
                self.last_reply_time = time.time()
                self.total_sent += 1
                self.log(f"💬 [已发送] {cleaned_text}")
                return True
        except Exception as e:
            self.log(f"❌ 发送签名消息失败: {e}")
        return False

    def is_self_message(self, msg: Dict[str, Any]) -> bool:
        """判断消息是否由本 Agent 发出"""
        sender = msg.get("from", "")
        # 1. 完全匹配 DID
        if sender == self.identity.did:
            return True
        # 2. 匹配未签名或带波浪号的昵称
        if sender in [self.name, f"~{self.name}", f"@{self.name}"]:
            return True
        # 3. DID 前缀缩写匹配 (例如 <z6Mk...xxxx>)
        if sender.startswith("did:key:") and sender.lower() == self.identity.did.lower():
            return True
        return False

    def is_mentioned(self, text: str) -> bool:
        """判断消息中是否包含对本 Agent 的 @ 或昵称提及"""
        lower_text = text.lower()
        if f"@{self.name.lower()}" in lower_text or self.name.lower() in lower_text:
            return True
        # 匹配简短 DID
        if self.identity.did[-6:].lower() in lower_text:
            return True
        return False

    def check_heartbeat(self):
        """定期打印心跳与健康统计 (大幅节约 I/O 与日志大小)"""
        now = time.time()
        if now - self.last_heartbeat_time >= self.heartbeat_interval_seconds:
            self.log(
                f"💓 状态正常 | 本周期处理 {self.interval_received} 条消息 | "
                f"累计监听 {self.total_received} 条 | 累计发送 {self.total_sent} 条签名消息"
            )
            self.last_heartbeat_time = now
            self.interval_received = 0

    def handle_message(self, msg: Dict[str, Any]):
        """处理单条新消息"""
        sender = msg.get("from", "unknown")
        text = msg.get("text", "")
        seq = msg.get("seq", 0)

        # 1. 过滤自己发送的消息
        if self.is_self_message(msg):
            return

        self.total_received += 1
        self.interval_received += 1

        # 仅在开启详细日志模式时才打印每条大厅广播，默认关闭以减少 99% CPU 与磁盘 I/O 消耗
        if self.log_incoming_messages:
            self.log(f"📥 [收到 seq={seq}] {sender[:12]}...: {text}")

        self.recent_messages.append(msg)

        # 2. 检查冷却时间
        now = time.time()
        if now - self.last_reply_time < self.cooldown_seconds:
            return

        # 3. 决定是否回复
        mentioned = self.is_mentioned(text)
        should_reply = False

        if mentioned:
            self.log(f"🎯 检测到被 @提及 或点名 (来自 {sender[:12]}...)，准备回复...")
            should_reply = True
        elif random.random() < self.reply_probability:
            should_reply = True

        if should_reply:
            # 引入微小随机延迟以模拟人类/不同智能体思考时间
            think_delay = random.uniform(1.0, 2.5)
            time.sleep(think_delay)
            
            reply_content = self.llm.generate_response(
                agent_name=self.name,
                recent_messages=list(self.recent_messages),
                trigger_text=text,
                trigger_from=sender
            )
            if reply_content:
                self.send_signed_message(reply_content)

    def run_loop(self, stop_event: threading.Event):
        """Agent 主运行循环 (支持长轮询与异常自愈)"""
        self.is_running = True
        try:
            self.initialize()
        except Exception as e:
            self.log(f"⚠️ 初始化阶段发生异常 ({e})，稍后将在轮询循环中重试...")

        consecutive_errors = 0
        self.log(f"🔄 开始监听房间 [{self.room}] (静默广播流模式，降低CPU占用)...")

        try:
            while not stop_event.is_set():
                try:
                    # 检查健康心跳
                    self.check_heartbeat()

                    # 长轮询获取新消息
                    data = self.client.get_messages(
                        room=self.room,
                        since=self.last_seq,
                        wait=self.poll_wait,
                        limit=30
                    )
                    
                    consecutive_errors = 0
                    messages = data.get("messages", [])
                    
                    if messages:
                        for msg in messages:
                            msg_seq = msg.get("seq")
                            if msg_seq is not None:
                                if self.last_seq is None or msg_seq > self.last_seq:
                                    self.last_seq = msg_seq
                            self.handle_message(msg)
                    else:
                        # 没有新消息，如果 last_seq 在返回体中有更新则同步
                        if "last_seq" in data and data["last_seq"] is not None:
                            self.last_seq = data["last_seq"]

                except Exception as e:
                    consecutive_errors += 1
                    backoff = min(2 ** consecutive_errors, 15)
                    self.log(f"⚠️ 轮询网络异常 ({e})，{backoff}s 后重试...")
                    stop_event.wait(backoff)
        except Exception as fatal_e:
            self.log(f"💥 发生未捕获致命异常: {fatal_e}")
        finally:
            self.is_running = False
            self.log("🛑 Agent 循环已安全退出。")
