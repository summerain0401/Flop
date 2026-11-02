"""
trading_agent.py - Technocore Close Call (close-1) 专属自动化交易智能体

策略包含:
1. Delta-Maker: 做市与网格策略 (Maker / Grid Liquidity Provider)
   - 基于实时盘面加权均价，在基准价上下分层挂单 (taker: "any")
   - 提供流动性，赚取买卖价差与网格收益
   - 自动计算并签署 maker_sig: close-1|terms|<terms>

2. Epsilon-Sniper: 盘口异动套利与吃单狙击策略 (Taker / Order Sniper)
   - 实时长轮询监听 /r/close1 中的所有 open 订单
   - 嗅探高性价比对手单 (低于市场均价的割肉卖单、高于市场均价的高溢价买单)
   - 快速签名承兑 taker_sig: close-1|accept|<terms>|<did>
   - 提交已完成撮合交易，抢占结算先机
"""

import os
import re
import json
import time
import uuid
import random
import threading
from typing import Dict, Any, Optional, List
from collections import deque

from identity import AgentIdentity
from technocore_client import TechnocoreClient


class CloseCallTradingAgent:
    """Close Call (close-1) 期货自动化交易智能体"""

    def __init__(
        self,
        name: str,
        strategy: str = "maker_grid",  # "maker_grid" 或 "taker_sniper"
        room: str = "close1",
        base_endpoint: str = "https://technocore.chat",
        poll_wait: int = 10,
        key_dir: str = "agent_keys",
        claim_faucet: bool = True,
        enable_mailbox: bool = True,
        claim_passport: bool = True,
        join_close1: bool = True,
        strategy_config: Optional[Dict[str, Any]] = None,
        heartbeat_interval_seconds: int = 300
    ):
        self.name = name
        self.strategy = strategy
        self.room = room
        self.base_endpoint = base_endpoint
        self.poll_wait = poll_wait
        self.claim_faucet = claim_faucet
        self.enable_mailbox = enable_mailbox
        self.claim_passport = claim_passport
        self.join_close1 = join_close1
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

        # 策略参数
        self.strategy_config = strategy_config or {}
        self.cooldown_seconds = float(self.strategy_config.get("cooldown_seconds", 15.0))
        self.grid_spread_pct = float(self.strategy_config.get("grid_spread_pct", 0.008))
        self.snipe_threshold_pct = float(self.strategy_config.get("snipe_threshold_pct", 0.006))
        self.order_qty_min = float(self.strategy_config.get("order_qty_min", 0.2))
        self.order_qty_max = float(self.strategy_config.get("order_qty_max", 0.5))

        # 专属去中心化身份
        self.identity = AgentIdentity(name=self.name, key_dir=key_dir)
        self.client = TechnocoreClient(base_url=base_endpoint, user_agent=f"TradingAgent-{self.name}/1.0")

        # 运行状态与行情数据
        self.is_running = False
        self.last_seq: Optional[int] = None
        self.mailbox_last_seq: Optional[int] = None
        self.last_mailbox_check: float = 0.0
        self.last_faucet_check: float = 0.0
        self.last_trade_action_time: float = 0.0
        self.last_heartbeat_time: float = time.time()

        # 行情追踪与盘口记忆
        self.price_history: deque = deque(maxlen=60)
        self.latest_market_sweep: int = 3042  # 默认基准 sweep 编号，随盘面动态递增
        self.seen_order_ids: set = set()      # 防止重复吃单或处理
        self.my_orders: Dict[str, Dict[str, Any]] = {}

        # 统计计数
        self.total_received: int = 0
        self.total_orders_placed: int = 0
        self.total_orders_sniped: int = 0
        self.total_sent: int = 0
        self.interval_received: int = 0

    def log(self, text: str):
        """格式化日志输出"""
        ts = time.strftime("%H:%M:%S", time.localtime())
        print(f"[{ts}] [{self.name}] {text}", flush=True)

    def initialize(self):
        """交易智能体启动准备: DID Note 注册、水龙头、Passport、Close-1 资格登记"""
        self.log(f"🚀 初始化交易智能体 ({self.strategy}) | 身份: {self.identity.did}")

        # 1. 注册 DID Note (包含 Mailbox)
        if self.enable_mailbox:
            try:
                shard, key = self.identity.get_sharded_did_path()
                mb_name = self.identity.mailbox_name
                success = self.client.register_did_note(shard, key, self.identity.did, mb_name)
                if success:
                    self.log(f"✅ DID Note 注册成功 (/kv/did-{shard}/{key} | Mailbox: {mb_name})")
            except Exception as e:
                self.log(f"⚠️ DID Note 注册异常: {e}")

        # 2. 申领 FLOP 测试网水龙头
        if self.claim_faucet:
            self.try_claim_faucet()

        # 3. 申领 Agent 护照 (passport:v1)
        if self.claim_passport:
            self.try_claim_passport()

        # 4. 登记 Close Call 交易大赛参赛资格 (申领 10,000 POLF)
        if self.join_close1:
            self.try_register_close1()

        # 5. 对齐交易大厅 close1 游标
        try:
            init_data = self.client.get_messages(room=self.room, wait=0, limit=20)
            msgs = init_data.get("messages", [])
            for m in msgs:
                self.extract_market_data(m)
            if "last_seq" in init_data and init_data["last_seq"] is not None:
                self.last_seq = init_data["last_seq"]
                self.log(f"📍 对齐交易大厅序列游标: seq={self.last_seq}")
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

        self.log(f"✨ 交易初始化完成，基准参考价: ${self.get_mid_price():.2f} | Sweep: {self.latest_market_sweep}")

    def try_claim_faucet(self):
        """向 /r/faucet 申领测试网代币"""
        faucet_flag = self.identity.key_dir / f"{self.name}.faucet"
        if faucet_flag.exists():
            try:
                last_time = float(faucet_flag.read_text(encoding="utf-8").strip())
                if time.time() - last_time < 86400.0:
                    return
            except Exception:
                pass

        shard, key = self.identity.get_sharded_did_path()
        faucet_msg = (
            f"FLOP testnet faucet claim. DID: {self.identity.did}. "
            f"DID note at /kv/did-{shard}/{key}. Requesting testnet tokens."
        )
        self.log("🚰 正在向 /r/faucet 发送测试网领水申请证明...")
        success = self.send_signed_message(faucet_msg, target_room="faucet")
        if success:
            try:
                faucet_flag.write_text(str(time.time()), encoding="utf-8")
                self.log("✅ 测试网领水申请已成功提交至 /r/faucet")
            except Exception:
                pass

    def try_claim_passport(self):
        """向 /r/technocore-starter 申领 passport:v1 护照"""
        passport_flag = self.identity.key_dir / f"{self.name}.passport"
        if passport_flag.exists():
            return

        join_msg = "join:v1 caps=automation,monitoring,vps,research,trading"
        self.log("🛂 正在向 /r/technocore-starter 提交 Agent 护照申领证明...")
        success = self.send_signed_message(join_msg, target_room="technocore-starter")
        if success:
            try:
                passport_flag.write_text(str(time.time()), encoding="utf-8")
                self.log("✅ Agent Passport 申领证明已成功提交至 /r/technocore-starter")
            except Exception:
                pass

    def try_register_close1(self):
        """向 /r/close1 登记 Close Call 交易大赛资格 (申领 10,000 POLF)"""
        close1_flag = self.identity.key_dir / f"{self.name}.close1"
        if close1_flag.exists():
            return

        reg_payload = json.dumps({
            "t": "owner",
            "season": "close-1",
            "key": self.identity.did
        }, separators=(',', ':'))
        self.log("📈 正在向 /r/close1 提交 Close Call 资格登记 (申领 10,000 POLF)...")
        success = self.send_signed_message(reg_payload, target_room="close1")
        if success:
            try:
                close1_flag.write_text(str(time.time()), encoding="utf-8")
                self.log("✅ Close Call 10,000 POLF 保证金铸造登记已提交至 /r/close1")
            except Exception:
                pass

    def send_signed_message(self, text: str, target_room: Optional[str] = None) -> bool:
        """
        对文本进行签名并发送至指定房间
        具备 Nonce 自动自愈重签机制与 HTTP 422 副本抑制捕获
        """
        room = target_room or self.room
        current_text = text
        for attempt in range(2):
            try:
                cleaned_text, nonce, sig = self.identity.sign_message(room, current_text)
                success, status, body = self.client.say_signed(
                    room=room,
                    did=self.identity.did,
                    sig=sig,
                    nonce=nonce,
                    text=cleaned_text
                )
                if success:
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
        """定期检查专属安全邮箱"""
        mb_room = self.identity.mailbox_name
        try:
            data = self.client.get_messages(room=mb_room, since=self.mailbox_last_seq, wait=0, limit=10)
            msgs = data.get("messages", [])
            if msgs:
                for m in msgs:
                    seq = m.get("seq")
                    if seq and (self.mailbox_last_seq is None or seq > self.mailbox_last_seq):
                        self.mailbox_last_seq = seq
                    self.log(f"📬 Mailbox 收到私信: 来自 {m.get('from')}: {m.get('text')[:60]}")
        except Exception:
            pass

    def extract_market_data(self, msg: Dict[str, Any]):
        """从房间实时消息流中提取 NVDA 参考价、成交价与当前 sweep 轮次"""
        text = msg.get("text", "")
        if '"until":' in text or '"px":' in text:
            try:
                data = json.loads(text)
                terms = data.get("terms", {})
                if "px" in terms:
                    px = float(terms["px"])
                    if 100.0 < px < 500.0:
                        self.price_history.append(px)
                if "until" in terms:
                    u = int(terms["until"])
                    if u > self.latest_market_sweep:
                        self.latest_market_sweep = u
            except Exception:
                pass

    def get_mid_price(self) -> float:
        """获取当前盘面参考中间价"""
        if self.price_history:
            return sum(self.price_history) / len(self.price_history)
        return 234.50  # 默认参考价

    def sign_trade_terms(self, terms: Dict[str, Any]) -> str:
        """按官方规范签署 terms: close-1|terms|<compact_sorted_json>"""
        terms_str = json.dumps(terms, separators=(',', ':'), sort_keys=True)
        payload = f"close-1|terms|{terms_str}"
        return self.identity.sign_raw(payload)

    def sign_trade_accept(self, terms: Dict[str, Any]) -> str:
        """按官方规范签署 accept: close-1|accept|<compact_sorted_json>|<taker_did>"""
        terms_str = json.dumps(terms, separators=(',', ':'), sort_keys=True)
        payload = f"close-1|accept|{terms_str}|{self.identity.did}"
        return self.identity.sign_raw(payload)

    def execute_maker_grid_cycle(self):
        """
        策略 1 (Delta-Maker): 网格做市周期
        在基准价上下按点差挂出 Maker 订单 (taker: "any")
        """
        now = time.time()
        if now - self.last_trade_action_time < self.cooldown_seconds:
            return

        mid_px = self.get_mid_price()
        # 产生随机网格偏置，规避单一价位拥挤
        jitter = random.uniform(-0.002, 0.002)
        spread = self.grid_spread_pct + jitter

        # 生成买单 (略低于中间价) 与卖单 (略高于中间价)
        buy_px = round(mid_px * (1.0 - spread), 2)
        sell_px = round(mid_px * (1.0 + spread), 2)
        qty = round(random.uniform(self.order_qty_min, self.order_qty_max), 2)
        sweep_until = max(self.latest_market_sweep, 3042)

        # 轮流交替挂买或卖单，控制资金占用
        side = "buy" if (self.total_orders_placed % 2 == 0) else "sell"
        target_px = buy_px if side == "buy" else sell_px
        order_id = uuid.uuid4().hex[:8]

        terms = {
            "id": order_id,
            "maker": self.identity.did,
            "px": f"{target_px:.2f}",
            "qty": f"{qty:.2f}",
            "side": side,
            "taker": "any",
            "until": sweep_until
        }

        maker_sig = self.sign_trade_terms(terms)
        trade_payload = {
            "t": "trade",
            "season": "close-1",
            "terms": terms,
            "taker": "any",
            "maker_sig": maker_sig
        }

        trade_msg = json.dumps(trade_payload, separators=(',', ':'))
        success = self.send_signed_message(trade_msg)
        if success:
            self.total_orders_placed += 1
            self.last_trade_action_time = now
            self.my_orders[order_id] = terms
            self.log(
                f"📊 [做市挂单] {side.upper()} {qty} NVDA @ ${target_px:.2f} "
                f"(Mid: ${mid_px:.2f}, ID: {order_id})"
            )

    def evaluate_taker_snipe(self, msg: Dict[str, Any]):
        """
        策略 2 (Epsilon-Sniper): 异动订单狙击与吃单套利
        从房间监听流中截获高性价比挂单并 countersign
        """
        text = msg.get("text", "")
        if '"t":"trade"' not in text or '"taker":"any"' not in text or '"maker_sig"' not in text:
            return

        try:
            data = json.loads(text)
            terms = data.get("terms")
            maker_sig = data.get("maker_sig")
            if not terms or not maker_sig:
                return

            order_id = terms.get("id")
            maker = terms.get("maker")
            side = terms.get("side")
            px_str = terms.get("px")
            qty_str = terms.get("qty")

            # 过滤自己挂出的单或已处理过的单
            if not order_id or order_id in self.seen_order_ids or maker == self.identity.did:
                return
            self.seen_order_ids.add(order_id)

            px = float(px_str)
            qty = float(qty_str)
            mid_px = self.get_mid_price()

            # 评估套利优势 (高于买方均价或低于卖方均价)
            is_profitable = False
            advantage_desc = ""

            # 卖方折价抛售 -> 我们作为 Taker 买入
            if side == "sell" and px <= mid_px * (1.0 - self.snipe_threshold_pct):
                is_profitable = True
                discount = ((mid_px - px) / mid_px) * 100
                advantage_desc = f"低估折价买入 (折扣 -{discount:.2f}%)"

            # 买方高溢价求购 -> 我们作为 Taker 卖出
            elif side == "buy" and px >= mid_px * (1.0 + self.snipe_threshold_pct):
                is_profitable = True
                premium = ((px - mid_px) / mid_px) * 100
                advantage_desc = f"高溢价空头卖出 (溢价 +{premium:.2f}%)"

            if is_profitable:
                # 冷却限制
                now = time.time()
                if now - self.last_trade_action_time < self.cooldown_seconds:
                    return

                # 签署 taker_sig
                taker_sig = self.sign_trade_accept(terms)
                completed_trade = {
                    "t": "trade",
                    "season": "close-1",
                    "terms": terms,
                    "taker": self.identity.did,
                    "maker_sig": maker_sig,
                    "taker_sig": taker_sig
                }

                trade_msg = json.dumps(completed_trade, separators=(',', ':'))
                success = self.send_signed_message(trade_msg)
                if success:
                    self.total_orders_sniped += 1
                    self.last_trade_action_time = now
                    self.log(
                        f"🎯 [狙击吃单] 成功撮合对手单! {advantage_desc} | "
                        f"{side.upper()} {qty} NVDA @ ${px:.2f} (Mid: ${mid_px:.2f}, ID: {order_id})"
                    )
        except Exception:
            pass

    def check_heartbeat(self):
        """定期汇报健康心跳与交易战报"""
        now = time.time()
        if now - self.last_heartbeat_time >= self.heartbeat_interval_seconds:
            mid_px = self.get_mid_price()
            if self.strategy == "maker_grid":
                self.log(
                    f"💓 [做市心跳] 运行正常 | 累计监听 {self.total_received} 条 | "
                    f"累计挂单 {self.total_orders_placed} 笔 | 参考中间价: ${mid_px:.2f}"
                )
            else:
                self.log(
                    f"💓 [狙击心跳] 运行正常 | 累计监听 {self.total_received} 条 | "
                    f"累计狙击吃单 {self.total_orders_sniped} 笔 | 参考中间价: ${mid_px:.2f}"
                )
            self.last_heartbeat_time = now
            self.interval_received = 0

    def run_loop(self, stop_event: threading.Event):
        """交易智能体主运行循环"""
        self.is_running = True
        try:
            self.initialize()
        except Exception as e:
            self.log(f"⚠️ 初始化阶段发生异常 ({e})，将在轮询中重试...")

        self.log(f"🔄 开始进驻交易大厅 [{self.room}] (策略模式: {self.strategy})...")
        consecutive_errors = 0

        try:
            while not stop_event.is_set():
                try:
                    # 检查心跳
                    self.check_heartbeat()

                    # 定期检查 Mailbox (30s)
                    if self.enable_mailbox and (time.time() - self.last_mailbox_check >= 30.0):
                        self.last_mailbox_check = time.time()
                        self.check_mailbox()

                    # 定期尝试领水 (每 30 分钟检查 24h 冷却)
                    if self.claim_faucet and (time.time() - self.last_faucet_check >= 1800.0):
                        self.last_faucet_check = time.time()
                        self.try_claim_faucet()

                    # 若为做市策略，定期执行网格挂单
                    if self.strategy == "maker_grid":
                        self.execute_maker_grid_cycle()

                    # 长轮询接收交易大厅新消息
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

                            self.total_received += 1
                            self.interval_received += 1
                            self.extract_market_data(msg)

                            # 若为狙击策略，对接收到的订单进行实时套利评估
                            if self.strategy == "taker_sniper":
                                self.evaluate_taker_snipe(msg)
                    else:
                        if "last_seq" in data and data["last_seq"] is not None:
                            self.last_seq = data["last_seq"]

                except Exception as e:
                    consecutive_errors += 1
                    backoff = min(2 ** consecutive_errors, 15)
                    self.log(f"⚠️ 轮询网络异常 ({e})，{backoff}s 后重试...")
                    stop_event.wait(backoff)
        except Exception as fatal_e:
            self.log(f"💥 发生未捕获异常: {fatal_e}")
        finally:
            self.is_running = False
            self.log("🛑 交易智能体循环已安全退出。")
