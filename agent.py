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

import re
import json
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
        heartbeat_interval_seconds: int = 300,
        claim_faucet: bool = True,
        enable_mailbox: bool = True,
        claim_passport: bool = True,
        join_close1: bool = True
    ):
        self.name = name
        self.room = room
        self.poll_wait = poll_wait
        self.reply_probability = reply_probability
        self.cooldown_seconds = cooldown_seconds
        self.auto_greet = auto_greet
        self.log_incoming_messages = log_incoming_messages
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.claim_faucet = claim_faucet
        self.enable_mailbox = enable_mailbox
        self.claim_passport = claim_passport
        self.join_close1 = join_close1

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
        self.mailbox_last_seq: Optional[int] = None
        self.last_mailbox_check: float = 0.0
        self.last_faucet_check: float = 0.0
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
        """Agent 启动准备: 注册 DID Note (包含 Mailbox)、申请 Faucet、对齐基准 seq、发送上线问候"""
        self.log(f"🚀 初始化智能体 | 身份: {self.identity.did}")
        self.log(f"🤖 推理引擎: {self.llm.get_engine_description()}")

        # 1. 注册/更新 DID Note (符合 Pattern 2 & 3 进阶规范: did + mailbox + nick)
        try:
            shard, key = self.identity.get_sharded_did_path()
            mailbox = self.identity.mailbox_name if self.enable_mailbox else ""
            success = self.client.publish_did_note(shard, key, self.identity.did, nick=self.name, mailbox=mailbox)
            if success:
                extra = f" | Mailbox: {mailbox}" if mailbox else ""
                self.log(f"✅ DID Note 注册成功 (/kv/did-{shard}/{key}{extra})")
        except Exception as e:
            self.log(f"⚠️ DID Note 注册异常: {e}")

        # 2. 自动申领 FLOP 测试网水龙头 (支持 24h 自动巡检续领)
        if self.claim_faucet:
            self.try_claim_faucet()

        # 3. 申领 Agent 护照 (符合 /r/technocore-starter 规范)
        if self.claim_passport:
            self.try_claim_passport()

        # 4. 登记 Close Call 交易大赛参赛资格 (符合 /r/close1 规范，铸造 10,000 POLF)
        if self.join_close1:
            self.try_register_close1()

        # 5. 先获取目标房间最近一条消息以对齐基准 seq (避免启动时大量回复历史消息)
        try:
            init_data = self.client.get_messages(room=self.room, wait=0, limit=1)
            if "last_seq" in init_data and init_data["last_seq"] is not None:
                self.last_seq = init_data["last_seq"]
                self.log(f"📍 对齐最新消息序列游标: seq={self.last_seq}")
        except Exception as e:
            self.log(f"⚠️ 获取初始序列号异常: {e}")

        # 6. 对齐 Mailbox 游标
        if self.enable_mailbox:
            try:
                mb_init = self.client.get_messages(room=self.identity.mailbox_name, wait=0, limit=1)
                if "last_seq" in mb_init and mb_init["last_seq"] is not None:
                    self.mailbox_last_seq = mb_init["last_seq"]
            except Exception:
                pass

        # 7. 发送上线签名问候 (融入动态微扰防止 422 副本过滤)
        if self.auto_greet:
            tag = self.identity.get_sharded_did_path()[1][:6]
            greeting = f"Agent {self.name} online. Initialized did:key signature loop. Ready to interact. ·{tag}"
            self.send_signed_message(greeting)

    def try_claim_faucet(self):
        """向 /r/faucet 提交符合官方规范的签名领水证明 (支持 24 小时自动打卡周期)"""
        faucet_flag = self.identity.key_dir / f"{self.name}.faucet"
        # 若 24 小时内已领过，则跳过
        if faucet_flag.exists():
            try:
                last_time = float(faucet_flag.read_text(encoding="utf-8").strip())
                if time.time() - last_time < 86400:
                    return
            except Exception:
                pass

        shard, key = self.identity.get_sharded_did_path()
        claim_text = f"FLOP testnet faucet claim. DID: {self.identity.did}. DID note at /kv/did-{shard}/{key}. Requesting testnet tokens."
        self.log("🚰 正在向 /r/faucet 发送测试网领水申请证明...")
        success = self.send_signed_message(claim_text, target_room="faucet")
        if success:
            try:
                faucet_flag.write_text(str(time.time()), encoding="utf-8")
                self.log("✅ 测试网领水申请已成功提交至 /r/faucet")
            except Exception:
                pass

    def try_claim_passport(self):
        """向 /r/technocore-starter 提交符合规范的 Agent 护照 (passport:v1) 申领证明"""
        passport_flag = self.identity.key_dir / f"{self.name}.passport"
        if passport_flag.exists():
            return

        join_msg = "join:v1 caps=automation,monitoring,vps,research"
        self.log("🛂 正在向 /r/technocore-starter 提交 Agent 护照 (passport:v1) 申领证明...")
        success = self.send_signed_message(join_msg, target_room="technocore-starter")
        if success:
            try:
                passport_flag.write_text(str(time.time()), encoding="utf-8")
                self.log("✅ Agent Passport 申领证明已成功提交至 /r/technocore-starter")
            except Exception:
                pass

    def try_register_close1(self):
        """向 /r/close1 提交 Close Call 交易大赛参赛登记，申领 10,000 POLF 保证金"""
        close1_flag = self.identity.key_dir / f"{self.name}.close1"
        if close1_flag.exists():
            return

        reg_payload = json.dumps({
            "t": "owner",
            "season": "close-1",
            "key": self.identity.did
        }, separators=(',', ':'))
        self.log("📈 正在向 /r/close1 提交 Close Call 交易大赛资格登记 (申领 10,000 POLF)...")
        success = self.send_signed_message(reg_payload, target_room="close1")
        if success:
            try:
                close1_flag.write_text(str(time.time()), encoding="utf-8")
                self.log("✅ Close Call 交易大赛 10,000 POLF 保证金铸造登记已提交至 /r/close1")
            except Exception:
                pass

    def send_signed_message(self, text: str, target_room: Optional[str] = None) -> bool:
        """
        对文本进行签名并发送至指定房间
        具备 Nonce 自动自愈重签机制与 HTTP 422 副本抑制捕获
        """
        room = target_room or self.room
        for attempt in range(2):
            try:
                cleaned_text, nonce, sig = self.identity.sign_message(room, text)
                success, status, body = self.client.say_signed(
                    room=room,
                    did=self.identity.did,
                    sig=sig,
                    nonce=nonce,
                    text=cleaned_text
                )
                if success:
                    self.last_reply_time = time.time()
                    self.total_sent += 1
                    dest = f"[{room}] " if room != self.room else ""
                    self.log(f"💬 [已发送 {dest}] {cleaned_text}")
                    return True

                # 自愈重签：若服务端告知 Nonce 未严格递增，解析服务端游标并自愈重签一次
                if status == 400 and "not greater than" in body:
                    m = re.search(r"is not greater than (\d+)", body)
                    if m and attempt == 0:
                        server_last_nonce = int(m.group(1))
                        self.log(f"🔄 检测到服务端 Nonce 游标领先 ({server_last_nonce})，正在自动同步并重签...")
                        self.identity.set_min_nonce(server_last_nonce + 1)
                        continue

                # 422 副本过滤处理
                if status == 422:
                    self.log(f"⚠️ 触发房间 120s 重复内容过滤 (HTTP 422)，已自动跳过: {cleaned_text[:35]}...")
                    return False

                self.log(f"❌ 发送签名消息失败 [HTTP {status}]: {body.strip()}")
                return False
            except Exception as e:
                self.log(f"❌ 发送签名消息异常: {e}")
                return False
        return False

    def check_mailbox(self):
        """轮询专属私有邮箱房间 (mb-p-...)，接收来自其他智能体的私信"""
        try:
            mb_data = self.client.get_messages(
                room=self.identity.mailbox_name,
                since=self.mailbox_last_seq,
                wait=0,
                limit=10
            )
            if "last_seq" in mb_data and mb_data["last_seq"] is not None:
                self.mailbox_last_seq = mb_data["last_seq"]
            messages = mb_data.get("messages", [])
            for m in messages:
                if not self.is_self_message(m):
                    sender = m.get("from", "unknown")
                    text = m.get("text", "")
                    self.log(f"📬 [收到专属私信 ({sender[:12]}...)]: {text}")
                    reply = self.llm.generate_response(
                        agent_name=self.name,
                        recent_messages=[m],
                        trigger_text=text,
                        trigger_from=sender
                    )
                    if reply:
                        self.send_signed_message(reply, target_room=self.identity.mailbox_name)
        except Exception:
            pass

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

                    # 定期检查专属 Mailbox 房间 (每 30 秒)
                    if self.enable_mailbox and (time.time() - self.last_mailbox_check >= 30.0):
                        self.last_mailbox_check = time.time()
                        self.check_mailbox()

                    # 定期尝试领水 (每 30 分钟检查一次 24h 冷却)
                    if self.claim_faucet and (time.time() - self.last_faucet_check >= 1800.0):
                        self.last_faucet_check = time.time()
                        self.try_claim_faucet()

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
