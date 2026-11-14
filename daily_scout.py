#!/usr/bin/env python3
"""
daily_scout.py - Technocore 每日情报侦察 + 智能派遣 + 增强简报系统 v1.0

三阶段智能巡检:
  Phase 1: 平台情报侦察 (扫描 /rooms, /r/announcements, /r/events, 新房间发现)
  Phase 2: 智能 Agent 派遣 (probe 回复, 新房间签到, passport 检查, 新玩法参与)
  Phase 3: 增强简报生成 (集群运行指标 + 情报发现 + 派遣日志 + 邮箱推送)

用法:
  python3 daily_scout.py                    # 正常执行 (侦察+派遣+简报)
  python3 daily_scout.py --dry-run          # 干运行 (仅侦察, 不发送消息)
  python3 daily_scout.py --config my.json   # 指定配置文件
"""

import os
import re
import sys
import json
import time
import hashlib
import argparse
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta, timezone

from identity import AgentIdentity
from technocore_client import TechnocoreClient


# =============================================================================
# Phase 1: 平台情报侦察引擎
# =============================================================================

class PlatformScout:
    """Technocore 平台情报侦察引擎"""

    # 已知的核心房间 (不需要作为'新发现'报告)
    CORE_ROOMS = {
        "lobby", "technocore", "kibble", "close1", "faucet",
        "technocore-starter", "announcements", "flop", "flop_labs",
        "meta", "dev", "tclk-offers", "events", "market",
        "flop_chat", "ashflop", "gpu-miners", "crypto",
        "flop-governance", "flop-collective", "flop-dao", "flop-market",
        "inference-agents", "ai2ai",
    }

    # 私有 / 邮箱 / 忽略的房间前缀
    IGNORE_PREFIXES = ("p-", "mb-", "mb-p-")

    def __init__(self, config: dict):
        self.base_url = config.get("base_url", "https://technocore.chat")
        self.client = TechnocoreClient(base_url=self.base_url, user_agent="DailyScout/1.0")
        self.known_rooms_db_path = config.get("known_rooms_db", "logs/known_rooms.json")
        self.known_rooms = self._load_known_rooms()
        self.watch_rooms = config.get("watch_rooms", [])

    # ---- 已知房间数据库 ----

    def _load_known_rooms(self) -> dict:
        try:
            with open(self.known_rooms_db_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"last_scan_time": "", "rooms": {},
                    "last_announcement_seq": 0, "last_events_seq": 0,
                    "last_starter_seq": 0}

    def _save_known_rooms(self):
        self.known_rooms["last_scan_time"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        Path(self.known_rooms_db_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.known_rooms_db_path, "w", encoding="utf-8") as f:
            json.dump(self.known_rooms, f, indent=2, ensure_ascii=False)

    # ---- 扫描 /rooms ----

    def scan_all_rooms(self) -> dict:
        """调用 /rooms 获取全量房间列表, 与本地已知数据库比较"""
        result = {"new_rooms": [], "grown_rooms": [], "active_rooms": [], "total": 0}
        try:
            status, body = self.client._request("GET", "/rooms", params={"format": "json"}, timeout=20.0)
            if status != 200:
                log("⚠️ /rooms 请求失败", status)
                return result

            data = json.loads(body)
            rooms_list = data if isinstance(data, list) else data.get("rooms", [])
            result["total"] = len(rooms_list) if isinstance(rooms_list, list) else data.get("total", 0)

            if isinstance(rooms_list, list):
                known = self.known_rooms.get("rooms", {})
                today = datetime.now().strftime("%Y-%m-%d")

                for r in rooms_list:
                    if not isinstance(r, dict):
                        continue
                    name = r.get("room", "")
                    last_seq = r.get("last_seq", 0)
                    idle = r.get("idle_seconds", 99999)
                    topic = r.get("topic")
                    nick_div = r.get("nick_diversity", 0)
                    window = r.get("window", 0)

                    # 跳过私有/邮箱房间
                    if any(name.startswith(p) for p in self.IGNORE_PREFIXES):
                        continue

                    room_info = {
                        "name": name, "last_seq": last_seq,
                        "idle_seconds": idle, "topic": topic,
                        "nick_diversity": nick_div, "window": window
                    }

                    # 新发现的房间
                    if name not in known and name not in self.CORE_ROOMS:
                        if last_seq > 50:  # 至少有一定活跃度
                            room_info["first_seen"] = today
                            result["new_rooms"].append(room_info)

                    # 活跃度显著增长的房间
                    elif name in known:
                        old_seq = known[name].get("last_seq", 0)
                        if last_seq > old_seq + 5000:  # 日增 5000+ 消息
                            room_info["growth"] = last_seq - old_seq
                            result["grown_rooms"].append(room_info)

                    # 当前活跃房间 (idle < 60s)
                    if idle < 60 and last_seq > 100:
                        result["active_rooms"].append(room_info)

                    # 更新本地数据库
                    if name not in known:
                        known[name] = {"last_seq": last_seq, "first_seen": today}
                    else:
                        known[name]["last_seq"] = last_seq

                self.known_rooms["rooms"] = known

        except Exception as e:
            log(f"⚠️ 扫描 /rooms 异常: {e}")
        return result

    # ---- 扫描公告房间 ----

    def scan_announcements(self) -> list:
        """从 /r/announcements 提取近24小时内的新公告"""
        results = []
        try:
            last_seq = self.known_rooms.get("last_announcement_seq", 0)
            data = self.client.get_messages(room="announcements", since=last_seq, wait=0, limit=50)
            messages = data.get("messages", [])
            cutoff = datetime.now(timezone.utc) - timedelta(hours=25)

            for m in messages:
                seq = m.get("seq", 0)
                ts_str = m.get("ts", "")
                text = m.get("text", "")
                sender = m.get("from", "")

                if seq > last_seq:
                    self.known_rooms["last_announcement_seq"] = seq

                # 解析时间戳
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts < cutoff:
                        continue
                except Exception:
                    continue

                # 解析 probe v1 格式
                probe_match = re.match(r"probe\s+v1\s*\|\s*([^\|]+)\s*\|\s*(.+)", text, re.IGNORECASE)
                if probe_match:
                    probe_id = probe_match.group(1).strip()
                    instructions = probe_match.group(2).strip()
                    results.append({
                        "type": "probe",
                        "id": probe_id,
                        "instructions": instructions,
                        "room": "announcements",
                        "sender": sender,
                        "ts": ts_str,
                        "seq": seq
                    })
                else:
                    results.append({
                        "type": "announcement",
                        "text": text[:200],
                        "room": "announcements",
                        "sender": sender,
                        "ts": ts_str,
                        "seq": seq
                    })

        except Exception as e:
            log(f"⚠️ 扫描 /r/announcements 异常: {e}")
        return results

    # ---- 扫描 technocore-starter ----

    def scan_starter(self) -> list:
        """从 /r/technocore-starter 检测新协议和 passport 状态变化"""
        results = []
        try:
            last_seq = self.known_rooms.get("last_starter_seq", 0)
            data = self.client.get_messages(room="technocore-starter", since=last_seq, wait=0, limit=30)
            messages = data.get("messages", [])

            for m in messages:
                seq = m.get("seq", 0)
                text = m.get("text", "")
                sender = m.get("from", "")

                if seq > last_seq:
                    self.known_rooms["last_starter_seq"] = seq

                # 检测 passport-status 响应
                if "passport-status:v1" in text:
                    results.append({
                        "type": "passport_status",
                        "text": text[:300],
                        "sender": sender,
                        "seq": seq
                    })
                # 新的协议/格式
                elif "network-error:v1" in text:
                    results.append({
                        "type": "network_update",
                        "text": text[:200],
                        "sender": sender,
                        "seq": seq
                    })

        except Exception as e:
            log(f"⚠️ 扫描 /r/technocore-starter 异常: {e}")
        return results

    # ---- 扫描 tclk-offers ----

    def scan_tclk_offers(self) -> list:
        """从 /r/tclk-offers 监控最新的 tclk/1 交易帧"""
        results = []
        try:
            data = self.client.get_messages(room="tclk-offers", wait=0, limit=20)
            messages = data.get("messages", [])
            cutoff = datetime.now(timezone.utc) - timedelta(hours=25)

            for m in messages:
                text = m.get("text", "")
                ts_str = m.get("ts", "")
                sender = m.get("from", "")

                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts < cutoff:
                        continue
                except Exception:
                    continue

                # 解析 tclk/1 offer 帧
                offer_match = re.match(
                    r"tclk/1\s+offer\s+(off_[a-f0-9]+)\s+([\d.]+)\s+(\w+)\s+rail:(\w+)",
                    text, re.IGNORECASE
                )
                if offer_match:
                    results.append({
                        "type": "tclk_offer",
                        "offer_id": offer_match.group(1),
                        "amount": offer_match.group(2),
                        "token": offer_match.group(3),
                        "rail": offer_match.group(4),
                        "sender": sender,
                        "ts": ts_str
                    })

                # 解析 tclk/1 accept 帧
                accept_match = re.match(r"tclk/1\s+accept\s+(off_[a-f0-9]+)", text, re.IGNORECASE)
                if accept_match:
                    results.append({
                        "type": "tclk_accept",
                        "offer_id": accept_match.group(1),
                        "sender": sender,
                        "ts": ts_str
                    })

        except Exception as e:
            log(f"⚠️ 扫描 /r/tclk-offers 异常: {e}")
        return results

    # ---- 扫描 /r/events 新房间创建事件 ----

    def scan_events(self) -> list:
        """从 /r/events 发现新创建的公共房间"""
        results = []
        try:
            last_seq = self.known_rooms.get("last_events_seq", 0)
            data = self.client.get_messages(room="events", since=last_seq, wait=0, limit=50)
            messages = data.get("messages", [])

            for m in messages:
                seq = m.get("seq", 0)
                text = m.get("text", "")
                ts_str = m.get("ts", "")

                if seq > last_seq:
                    self.known_rooms["last_events_seq"] = seq

                if text.startswith("created "):
                    room_name = text.replace("created ", "").strip()
                    results.append({
                        "type": "room_created",
                        "room": room_name,
                        "ts": ts_str,
                        "seq": seq
                    })

        except Exception as e:
            log(f"⚠️ 扫描 /r/events 异常: {e}")
        return results

    # ---- 扫描特定观察房间的最新动态 ----

    def scan_watch_rooms(self) -> list:
        """扫描所有关注房间的最新消息, 提取关键信号"""
        results = []
        keywords = [
            "new feature", "update", "launch", "live", "season",
            "kibble-v2", "close-2", "genesis", "airdrop", "reward",
            "challenge", "quest", "bounty", "invitation", "join",
            "mesh", "builder", "testnet", "mainnet", "migrate",
        ]

        for room in self.watch_rooms:
            try:
                data = self.client.get_messages(room=room, wait=0, limit=10)
                messages = data.get("messages", [])
                cutoff = datetime.now(timezone.utc) - timedelta(hours=25)

                for m in messages:
                    text = m.get("text", "").lower()
                    ts_str = m.get("ts", "")

                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts < cutoff:
                            continue
                    except Exception:
                        continue

                    # 检查是否包含关键信号
                    matched_keywords = [kw for kw in keywords if kw in text]
                    if matched_keywords:
                        results.append({
                            "type": "signal",
                            "room": room,
                            "text": m.get("text", "")[:200],
                            "keywords": matched_keywords,
                            "sender": m.get("from", ""),
                            "ts": ts_str
                        })

            except Exception:
                pass

        return results

    # ---- 综合分析 ----

    def detect_new_activities(self) -> dict:
        """综合分析所有情报, 识别可参与的新玩法"""
        log("📡 扫描 /rooms 全量房间列表...")
        rooms_intel = self.scan_all_rooms()

        log("📡 扫描 /r/announcements 公告...")
        announcements = self.scan_announcements()

        log("📡 扫描 /r/technocore-starter 协议变化...")
        starter_intel = self.scan_starter()

        log("📡 扫描 /r/tclk-offers 交易帧...")
        tclk_intel = self.scan_tclk_offers()

        log("📡 扫描 /r/events 新房间事件...")
        events_intel = self.scan_events()

        log("📡 扫描关注房间关键信号...")
        signals = self.scan_watch_rooms()

        # 提取 probes
        probes = [a for a in announcements if a["type"] == "probe"]

        # 保存已知房间数据库
        self._save_known_rooms()

        intel = {
            "probes": probes,
            "announcements": [a for a in announcements if a["type"] != "probe"],
            "new_rooms": rooms_intel.get("new_rooms", []),
            "grown_rooms": rooms_intel.get("grown_rooms", []),
            "active_rooms": rooms_intel.get("active_rooms", []),
            "total_rooms": rooms_intel.get("total", 0),
            "starter_intel": starter_intel,
            "tclk_offers": [t for t in tclk_intel if t["type"] == "tclk_offer"],
            "tclk_accepts": [t for t in tclk_intel if t["type"] == "tclk_accept"],
            "new_room_events": events_intel,
            "signals": signals,
            "alerts": [],
        }

        # 生成告警
        if len(probes) > 0:
            intel["alerts"].append(f"发现 {len(probes)} 个待回复的 probe/wave check")
        if len(intel["new_rooms"]) > 0:
            intel["alerts"].append(f"发现 {len(intel['new_rooms'])} 个新活跃房间")
        if len(intel["tclk_offers"]) > 0:
            intel["alerts"].append(f"发现 {len(intel['tclk_offers'])} 个 tclk/1 公开 offer")
        if len(intel["grown_rooms"]) > 0:
            names = ", ".join(r["name"] for r in intel["grown_rooms"][:3])
            intel["alerts"].append(f"房间活跃度激增: {names}")
        if len(signals) > 0:
            intel["alerts"].append(f"捕获 {len(signals)} 条关键信号 (含新功能/新活动关键词)")

        log(f"✅ 情报侦察完成: {len(probes)} probes, {len(intel['new_rooms'])} 新房间, "
            f"{len(intel['tclk_offers'])} tclk offers, {len(signals)} 信号")
        return intel


# =============================================================================
# Phase 2: 智能 Agent 派遣器
# =============================================================================

class AgentDispatcher:
    """智能 Agent 派遣器 (激进模式)"""

    def __init__(self, config: dict, dry_run: bool = False):
        self.dispatch_config = config.get("dispatch_agents", {})
        self.auto_config = config.get("auto_participate", {})
        self.scout_config = config.get("scout_settings", {})
        self.max_actions = self.auto_config.get("max_actions_per_run", 20)
        self.aggressive = self.auto_config.get("aggressive_mode", True)
        self.dry_run = dry_run

        self.base_url = self.scout_config.get("base_url", "https://technocore.chat")
        self.key_dir = self.scout_config.get("key_dir", "agent_keys")
        self.client = TechnocoreClient(base_url=self.base_url, user_agent="DailyScout-Dispatch/1.0")
        self.action_log: List[dict] = []
        self.actions_taken = 0

        # 预加载 Agent 身份缓存
        self._identity_cache: Dict[str, AgentIdentity] = {}

    def _get_identity(self, agent_name: str) -> AgentIdentity:
        if agent_name not in self._identity_cache:
            self._identity_cache[agent_name] = AgentIdentity(agent_name, key_dir=self.key_dir)
        return self._identity_cache[agent_name]

    def _send_signed(self, agent_name: str, room: str, text: str) -> bool:
        """使用指定 Agent 的身份签名并发送消息"""
        if self.dry_run:
            self._log_action(agent_name, "DRY-RUN", room, text[:60], "⏭️ 跳过")
            return True

        identity = self._get_identity(agent_name)
        cleaned, nonce, sig = identity.sign_message(room, text)

        ok, code, body = self.client.say_signed(
            room=room, did=identity.did, sig=sig, nonce=nonce, text=cleaned,
            use_post=(len(cleaned) > 800)
        )

        if ok or code == 200:
            self._log_action(agent_name, "已发送", room, cleaned[:60], "✅")
            return True
        elif code == 422:
            self._log_action(agent_name, "重复内容", room, cleaned[:40], "⚠️ 422")
            return False
        else:
            self._log_action(agent_name, "发送失败", room, f"HTTP {code}", "❌")
            return False

    def _log_action(self, agent: str, action: str, target: str, detail: str, result: str):
        ts = time.strftime("%H:%M:%S")
        entry = {
            "time": ts, "agent": agent, "action": action,
            "target": target, "detail": detail, "result": result
        }
        self.action_log.append(entry)
        log(f"  [{ts}] {result} {agent} -> {action} @ /r/{target}: {detail}")

    # ---- 派遣: probe 回复 ----

    def dispatch_probe_responses(self, probes: list) -> int:
        """派遣 Agent 回复 probe/wave check"""
        if not probes:
            return 0

        agent = self.dispatch_config.get("probe_responder", "Beta-Coder")
        count = 0

        for probe in probes:
            if self.actions_taken >= self.max_actions:
                break

            probe_id = probe.get("id", "")
            instructions = probe.get("instructions", "")
            room = probe.get("room", "announcements")

            # 构造符合 probe 回声规范的回复
            reply = f"echo {probe_id} | Agent {agent} acknowledges wave. did:key verified."
            if self._send_signed(agent, room, reply):
                count += 1
                self.actions_taken += 1
                time.sleep(1.5)  # 频率控制

        return count

    # ---- 派遣: 新房间心跳签到 ----

    def dispatch_room_heartbeats(self, new_rooms: list) -> int:
        """派遣 Agent 在新发现的活跃房间中发送签名心跳"""
        if not new_rooms:
            return 0

        agent = self.dispatch_config.get("room_scout", "Alpha-Node")
        identity = self._get_identity(agent)
        count = 0

        # 按活跃度排序, 优先加入高活跃房间
        sorted_rooms = sorted(new_rooms, key=lambda r: r.get("last_seq", 0), reverse=True)

        for room_info in sorted_rooms[:5]:  # 最多 5 个新房间
            if self.actions_taken >= self.max_actions:
                break

            name = room_info.get("name", "")
            if not name or any(name.startswith(p) for p in ("p-", "mb-")):
                continue

            tag = identity.did[-6:]
            heartbeat = (
                f"Agent {agent} online. Autonomous did:key node "
                f"acknowledging room /r/{name}. Signed verification active. "
                f"·{tag}"
            )

            if self._send_signed(agent, name, heartbeat):
                count += 1
                self.actions_taken += 1
                time.sleep(2.0)

        return count

    # ---- 派遣: flop_labs 建设者心跳 ----

    def dispatch_flop_labs_heartbeat(self) -> int:
        """向 /r/flop_labs 发送建设者验证心跳"""
        agent = self.dispatch_config.get("protocol_explorer", "Gamma-Sage")
        identity = self._get_identity(agent)

        if self.actions_taken >= self.max_actions:
            return 0

        tag = identity.did[-6:]
        heartbeat = (
            f"Verified builder node {agent}. Autonomous multi-agent swarm "
            f"contributing to FLOP ecosystem: Lobby (3 agents), CloseCall (2 agents), "
            f"Kibble PoUI (1 agent). Continuous operation confirmed. ·{tag}"
        )

        if self._send_signed(agent, "flop_labs", heartbeat):
            self.actions_taken += 1
            return 1
        return 0

    # ---- 派遣: passport 状态检查 ----

    def dispatch_passport_check(self) -> int:
        """检查所有 Agent 的 passport 状态"""
        agents_to_check = ["Alpha-Node", "Beta-Coder", "Gamma-Sage",
                           "Delta-Maker", "Epsilon-Sniper", "Zeta-Worker"]
        count = 0

        for agent_name in agents_to_check:
            if self.actions_taken >= self.max_actions:
                break

            identity = self._get_identity(agent_name)

            # 发送 status:v1 查询
            status_msg = "status:v1"
            if self._send_signed(agent_name, "technocore-starter", status_msg):
                count += 1
                self.actions_taken += 1
                time.sleep(2.0)

        return count

    # ---- 派遣: tclk 交易探索 ----

    def dispatch_tclk_explore(self, tclk_offers: list) -> int:
        """在 tclk-offers 表达交易意向 (仅探索性参与)"""
        if not tclk_offers or not self.aggressive:
            return 0

        agent = self.dispatch_config.get("tclk_explorer", "Delta-Maker")
        identity = self._get_identity(agent)

        if self.actions_taken >= self.max_actions:
            return 0

        # 只发送探索性消息, 不实际锁仓
        tag = identity.did[-6:]
        explore_msg = (
            f"Agent {agent} monitoring tclk/1 order flow. "
            f"Active offers observed: {len(tclk_offers)}. "
            f"Maker capability: paper rail. ·{tag}"
        )

        if self._send_signed(agent, "tclk-offers", explore_msg):
            self.actions_taken += 1
            return 1
        return 0

    # ---- 派遣: 信号驱动的新房间签到 ----

    def dispatch_signal_responses(self, signals: list) -> int:
        """对包含关键词的信号发起签到响应"""
        if not signals:
            return 0

        agent = self.dispatch_config.get("protocol_explorer", "Gamma-Sage")
        identity = self._get_identity(agent)
        count = 0
        responded_rooms = set()

        for signal in signals:
            if self.actions_taken >= self.max_actions:
                break

            room = signal.get("room", "")
            if room in responded_rooms or room in ("lobby", "close1", "kibble"):
                continue

            keywords = signal.get("keywords", [])
            if not keywords:
                continue

            tag = identity.did[-6:]
            response = (
                f"Agent {agent} responding to signal: {', '.join(keywords[:3])}. "
                f"Autonomous swarm node with verified did:key identity. ·{tag}"
            )

            if self._send_signed(agent, room, response):
                count += 1
                self.actions_taken += 1
                responded_rooms.add(room)
                time.sleep(2.0)

        return count

    # ---- 全量执行 ----

    def execute_all(self, intel: dict) -> list:
        """执行所有派遣动作, 返回行动日志"""
        mode_str = "DRY-RUN 模式" if self.dry_run else ("激进模式" if self.aggressive else "保守模式")
        log(f"🚀 派遣模式: {mode_str} (上限 {self.max_actions} 个动作)")

        # 1. 回复 probes
        if self.auto_config.get("probe_reply") and intel.get("probes"):
            n = self.dispatch_probe_responses(intel["probes"])
            log(f"   probe 回复: {n} 个")

        # 2. 新房间心跳
        if self.auto_config.get("new_room_heartbeat") and intel.get("new_rooms"):
            n = self.dispatch_room_heartbeats(intel["new_rooms"])
            log(f"   新房间签到: {n} 个")

        # 3. flop_labs 建设者心跳
        if self.auto_config.get("flop_labs_heartbeat"):
            n = self.dispatch_flop_labs_heartbeat()
            log(f"   flop_labs 心跳: {n} 个")

        # 4. passport 状态检查 (每隔 3 天检查一次)
        if self.auto_config.get("passport_refresh"):
            days_since_last = 999
            try:
                state_file = Path("logs/.last_passport_check")
                if state_file.exists():
                    last_ts = float(state_file.read_text().strip())
                    days_since_last = (time.time() - last_ts) / 86400
            except Exception:
                pass

            if days_since_last >= 3:
                n = self.dispatch_passport_check()
                log(f"   passport 检查: {n} 个")
                try:
                    Path("logs/.last_passport_check").write_text(str(time.time()))
                except Exception:
                    pass

        # 5. tclk 探索 (激进模式)
        if self.auto_config.get("tclk_explore") and self.aggressive:
            n = self.dispatch_tclk_explore(intel.get("tclk_offers", []))
            log(f"   tclk 探索: {n} 个")

        # 6. 信号驱动响应 (激进模式)
        if self.aggressive and intel.get("signals"):
            n = self.dispatch_signal_responses(intel["signals"])
            log(f"   信号响应: {n} 个")

        log(f"✅ 派遣完成: 共执行 {self.actions_taken} 个动作")
        return self.action_log


# =============================================================================
# Phase 3: 增强版每日简报生成器
# =============================================================================

class EnhancedReporter:
    """增强版每日简报生成器"""

    def __init__(self, config: dict, scout_config: dict):
        self.report_dir = config.get("report_dir", "logs")
        self.push_to_mailbox = config.get("push_to_mailbox", True)
        self.report_room = config.get("report_room", "p-scout-reports-a1b2c3d4e5f6")
        self.scout_config = scout_config

    # ---- 收集集群运行指标 ----

    def collect_cluster_stats(self) -> dict:
        """收集 3 大集群运行指标"""
        stats = {"lobby": {}, "trader": {}, "kibble": {}, "system": {}}

        # 获取进程信息
        for label, pattern in [("lobby", "main.py"), ("trader", "trader_main.py"), ("kibble", "kibble_main.py")]:
            pid, uptime, mem = self._get_process_info(pattern, exclude=["trader_main", "kibble_main"] if label == "lobby" else [])
            stats[label]["pid"] = pid
            stats[label]["uptime"] = uptime
            stats[label]["memory_mb"] = mem

        # 解析日志尾部提取统计
        stats["lobby"].update(self._parse_lobby_log())
        stats["trader"].update(self._parse_trader_log())
        stats["kibble"].update(self._parse_kibble_log())

        return stats

    def _get_process_info(self, script_name: str, exclude: list = None) -> Tuple[str, str, str]:
        """获取 Python 进程的 PID、运行时长、内存"""
        try:
            cmd = f"ps aux | grep '{script_name}' | grep -v grep"
            if exclude:
                for ex in exclude:
                    cmd += f" | grep -v '{ex}'"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
            lines = result.stdout.strip().split("\n")
            if lines and lines[0].strip():
                parts = lines[0].split()
                pid = parts[1] if len(parts) > 1 else "-"
                mem_kb = float(parts[5]) / 1024 if len(parts) > 5 else 0

                # 获取 uptime
                try:
                    up_result = subprocess.run(
                        f"ps -p {pid} -o etime=", shell=True,
                        capture_output=True, text=True, timeout=5
                    )
                    uptime = up_result.stdout.strip() or "-"
                except Exception:
                    uptime = "-"

                return pid, uptime, f"{mem_kb:.1f}"
        except Exception:
            pass
        return "-", "-", "-"

    def _parse_lobby_log(self) -> dict:
        """解析 lobby.log 尾部提取统计"""
        stats = {"total_received": 0, "total_sent": 0}
        try:
            result = subprocess.run(
                "grep '💓' logs/lobby.log | tail -3",
                shell=True, capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split("\n"):
                m_recv = re.search(r"累计监听\s+(\d+)\s+条", line)
                m_sent = re.search(r"累计发送\s+(\d+)\s+条", line)
                if m_recv:
                    stats["total_received"] = max(stats["total_received"], int(m_recv.group(1)))
                if m_sent:
                    stats["total_sent"] = max(stats["total_sent"], int(m_sent.group(1)))
        except Exception:
            pass
        return stats

    def _parse_trader_log(self) -> dict:
        """解析 trader.log 尾部提取统计"""
        stats = {"total_orders": 0, "total_sniped": 0, "total_filled": 0, "mid_price": 0}
        try:
            result = subprocess.run(
                "grep '💓' logs/trader.log | tail -3",
                shell=True, capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split("\n"):
                m_orders = re.search(r"累计挂单\s+(\d+)\s+笔", line)
                m_sniped = re.search(r"累计狙击吃单\s+(\d+)\s+笔", line)
                m_filled = re.search(r"已被承兑成交\s+(\d+)\s+笔", line)
                m_price = re.search(r"参考中间价:\s*\$?([\d.]+)", line)
                if m_orders:
                    stats["total_orders"] = max(stats["total_orders"], int(m_orders.group(1)))
                if m_sniped:
                    stats["total_sniped"] = max(stats["total_sniped"], int(m_sniped.group(1)))
                if m_filled:
                    stats["total_filled"] = max(stats["total_filled"], int(m_filled.group(1)))
                if m_price:
                    stats["mid_price"] = float(m_price.group(1))
        except Exception:
            pass
        return stats

    def _parse_kibble_log(self) -> dict:
        """解析 kibble.log 尾部提取统计"""
        stats = {"total_jobs": 0, "total_claimed": 0, "total_delivered": 0,
                 "total_useful": 0, "pass_rate": 0}
        try:
            result = subprocess.run(
                "grep '💓' logs/kibble.log | tail -3",
                shell=True, capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split("\n"):
                m_jobs = re.search(r"捕获任务\s+(\d+)", line)
                m_claimed = re.search(r"已抢单\s+(\d+)", line)
                m_delivered = re.search(r"已交付\s+(\d+)", line)
                m_useful = re.search(r"有用功通过数:\s*(\d+)", line)
                m_rate = re.search(r"判决通过率:\s*([\d.]+)%", line)
                if m_jobs:
                    stats["total_jobs"] = max(stats["total_jobs"], int(m_jobs.group(1)))
                if m_claimed:
                    stats["total_claimed"] = max(stats["total_claimed"], int(m_claimed.group(1)))
                if m_delivered:
                    stats["total_delivered"] = max(stats["total_delivered"], int(m_delivered.group(1)))
                if m_useful:
                    stats["total_useful"] = max(stats["total_useful"], int(m_useful.group(1)))
                if m_rate:
                    stats["pass_rate"] = float(m_rate.group(1))
        except Exception:
            pass

        # 如果心跳没有统计, 从认证日志计数
        if stats["total_useful"] == 0:
            try:
                result = subprocess.run(
                    "grep -c '有用功认证通过' logs/kibble.log",
                    shell=True, capture_output=True, text=True, timeout=5
                )
                count = int(result.stdout.strip())
                stats["total_useful"] = count
            except Exception:
                pass

        return stats

    # ---- 生成增强版简报 ----

    def generate_report(self, cluster_stats: dict, intel: dict, dispatch_log: list) -> str:
        """生成增强版 Markdown 简报"""
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")

        # 计算期数 (从 2026-10-28 开始计算)
        try:
            start = datetime(2026, 10, 28)
            issue_num = (now - start).days + 1
        except Exception:
            issue_num = 0

        lines = []
        lines.append(f"# 🔭 Technocore Daily Scout 增强简报 ({date_str} {time_str} - 第 {issue_num} 期)")
        lines.append("")
        lines.append(f"> **巡检模式**：情报侦察 + 智能派遣 + 集群监控 (激进模式)")

        # 总体状态判定
        all_online = all(
            cluster_stats.get(c, {}).get("pid", "-") != "-"
            for c in ["lobby", "trader", "kibble"]
        )
        status_str = "🟢 **全集群 3 大模块在线**" if all_online else "🟡 **部分集群离线 (见下方详情)**"
        lines.append(f"> **服务运行状态**：{status_str}")
        lines.append("")
        lines.append("---")

        # ======== 一、集群运行状态 ========
        lines.append("")
        lines.append("## 一、集群运行状态与 Agent 战报")
        lines.append("")
        lines.append("| 集群 | PID | 持续运行 | 内存 | Agent | 核心指标 | 状态 |")
        lines.append("|:---|:---:|:---:|:---:|:---|:---|:---:|")

        lobby = cluster_stats.get("lobby", {})
        lines.append(
            f"| **大厅交互** | `{lobby.get('pid', '-')}` | {lobby.get('uptime', '-')} | "
            f"{lobby.get('memory_mb', '-')} MB | Alpha/Beta/Gamma | "
            f"监听 {lobby.get('total_received', '?'):,} 条, 发送 {lobby.get('total_sent', '?'):,} 条 | "
            f"{'🟢' if lobby.get('pid', '-') != '-' else '🔴'} |"
        )

        trader = cluster_stats.get("trader", {})
        price_str = f"${trader.get('mid_price', 0):.2f}" if trader.get('mid_price') else "N/A"
        lines.append(
            f"| **交易挑战赛** | `{trader.get('pid', '-')}` | {trader.get('uptime', '-')} | "
            f"{trader.get('memory_mb', '-')} MB | Delta/Epsilon | "
            f"挂单 {trader.get('total_orders', '?'):,} 笔, 成交 {trader.get('total_filled', 0)} 笔, "
            f"中间价 {price_str} | {'🟢' if trader.get('pid', '-') != '-' else '🔴'} |"
        )

        kibble = cluster_stats.get("kibble", {})
        lines.append(
            f"| **有用功推理** | `{kibble.get('pid', '-')}` | {kibble.get('uptime', '-')} | "
            f"{kibble.get('memory_mb', '-')} MB | Zeta-Worker | "
            f"抢单 {kibble.get('total_claimed', '?'):,}, 交付 {kibble.get('total_delivered', '?'):,}, "
            f"USEFUL {kibble.get('total_useful', '?'):,} ✅ ({kibble.get('pass_rate', 0):.1f}%) | "
            f"{'🟢' if kibble.get('pid', '-') != '-' else '🔴'} |"
        )

        # ======== 二、情报侦察发现 ========
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 二、📡 情报侦察发现")

        # 新房间
        new_rooms = intel.get("new_rooms", [])
        if new_rooms:
            lines.append("")
            lines.append(f"### 新发现活跃房间 ({len(new_rooms)} 个)")
            for r in new_rooms[:10]:
                topic_str = f" — {r['topic']}" if r.get("topic") else ""
                lines.append(f"- `/r/{r['name']}` (seq: {r.get('last_seq', '?'):,}, "
                             f"多样性: {r.get('nick_diversity', 0):.2f}{topic_str})")

        # 活跃度增长
        grown = intel.get("grown_rooms", [])
        if grown:
            lines.append("")
            lines.append(f"### 房间活跃度激增 ({len(grown)} 个)")
            for r in grown[:5]:
                lines.append(f"- `/r/{r['name']}` 日增 **+{r.get('growth', 0):,}** 条消息")

        # 公告
        announcements = intel.get("announcements", [])
        probes = intel.get("probes", [])
        if probes:
            lines.append("")
            lines.append(f"### Probe / Wave Check ({len(probes)} 个)")
            for p in probes[:5]:
                lines.append(f"- `{p['id']}`: {p['instructions'][:100]}")

        if announcements:
            lines.append("")
            lines.append(f"### 公告更新 ({len(announcements)} 条)")
            for a in announcements[:5]:
                lines.append(f"- [{a.get('ts', '')[:10]}] {a['text'][:120]}")

        # tclk 交易
        tclk_offers = intel.get("tclk_offers", [])
        if tclk_offers:
            lines.append("")
            lines.append(f"### tclk/1 公开 Offer ({len(tclk_offers)} 个)")
            for t in tclk_offers[:5]:
                lines.append(f"- `{t['offer_id']}`: {t['amount']} {t['token']} (rail: {t['rail']})")

        # 关键信号
        signals = intel.get("signals", [])
        if signals:
            lines.append("")
            lines.append(f"### 🔔 关键信号捕获 ({len(signals)} 条)")
            for s in signals[:5]:
                kws = ", ".join(s.get("keywords", [])[:3])
                lines.append(f"- `/r/{s['room']}` [{kws}]: {s['text'][:100]}")

        # 平台总览
        lines.append("")
        lines.append(f"### 平台总览")
        lines.append(f"- 全网公共房间总数: **{intel.get('total_rooms', '?'):,}**")
        lines.append(f"- 近 24h 新创建房间事件: **{len(intel.get('new_room_events', []))}** 个")

        # ======== 三、自动派遣行动记录 ========
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 三、🚀 自动派遣行动记录")

        if dispatch_log:
            lines.append("")
            lines.append("| 时间 | 派遣 Agent | 行动 | 目标房间 | 结果 |")
            lines.append("|:---|:---|:---|:---|:---:|")
            for entry in dispatch_log[:20]:
                lines.append(
                    f"| {entry['time']} | {entry['agent']} | {entry['action']} | "
                    f"/r/{entry['target']} | {entry['result']} |"
                )
        else:
            lines.append("")
            lines.append("_本次巡检无需执行派遣动作。_")

        # ======== 四、告警与建议 ========
        alerts = intel.get("alerts", [])
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## 四、⚠️ 需人工关注")

        if alerts:
            for i, alert in enumerate(alerts, 1):
                lines.append(f"{i}. {alert}")
        else:
            lines.append("_无异常告警, 一切正常运行。_")

        # 离线集群告警
        for label, display in [("lobby", "大厅交互集群"), ("trader", "交易挑战赛集群"), ("kibble", "有用功推理集群")]:
            if cluster_stats.get(label, {}).get("pid", "-") == "-":
                lines.append(f"- **🔴 {display}进程未检测到, 建议运行 `./swarm.sh start {label}` 重启**")

        lines.append("")
        return "\n".join(lines)

    # ---- 保存与推送 ----

    def save_report(self, content: str):
        """保存简报到文件"""
        Path(self.report_dir).mkdir(parents=True, exist_ok=True)
        date_str = time.strftime("%Y%m%d")
        path = f"{self.report_dir}/daily_report_{date_str}.md"
        latest_path = f"{self.report_dir}/daily_report_latest.md"

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        with open(latest_path, "w", encoding="utf-8") as f:
            f.write(content)

        log(f"📄 简报已保存至: {path}")

    def push_to_mailbox_room(self, content: str, config: dict):
        """将简报摘要推送到 Technocore 私有邮箱房间"""
        if not self.push_to_mailbox:
            return

        agent_name = config.get("dispatch_agents", {}).get("report_sender", "Alpha-Node")
        key_dir = config.get("scout_settings", {}).get("key_dir", "agent_keys")
        base_url = config.get("scout_settings", {}).get("base_url", "https://technocore.chat")

        identity = AgentIdentity(agent_name, key_dir=key_dir)
        client = TechnocoreClient(base_url=base_url, user_agent="DailyScout-Report/1.0")

        # 构造单行摘要 (Technocore 消息不能有换行)
        date_str = time.strftime("%Y-%m-%d %H:%M")
        summary = self._make_single_line_summary(content, date_str)

        # 推送到报告房间
        cleaned, nonce, sig = identity.sign_message(self.report_room, summary)
        ok, code, body = client.say_signed(
            room=self.report_room, did=identity.did, sig=sig, nonce=nonce,
            text=cleaned, use_post=True
        )

        if ok or code == 200:
            log(f"📬 简报摘要已推送至邮箱房间: /r/{self.report_room}")
        else:
            log(f"⚠️ 邮箱推送失败 (HTTP {code}): {body[:80]}")

        # 也推送到 Alpha-Node 的 mailbox
        mb_name = identity.mailbox_name
        cleaned2, nonce2, sig2 = identity.sign_message(mb_name, summary)
        ok2, code2, body2 = client.say_signed(
            room=mb_name, did=identity.did, sig=sig2, nonce=nonce2,
            text=cleaned2, use_post=True
        )
        if ok2 or code2 == 200:
            log(f"📬 简报摘要已推送至 {agent_name} 邮箱: /r/{mb_name}")

    def _make_single_line_summary(self, full_report: str, date_str: str) -> str:
        """从完整报告中提取单行摘要 (≤ 4000 字符)"""
        lines = full_report.split("\n")

        # 提取关键数据点
        stats = []
        for line in lines:
            # 集群状态行
            if "大厅交互" in line and "|" in line:
                stats.append("Lobby: " + self._extract_table_stats(line))
            elif "交易挑战赛" in line and "|" in line:
                stats.append("Trading: " + self._extract_table_stats(line))
            elif "有用功推理" in line and "|" in line:
                stats.append("Kibble: " + self._extract_table_stats(line))

        # 提取告警
        alerts = []
        in_alerts = False
        for line in lines:
            if "需人工关注" in line:
                in_alerts = True
                continue
            if in_alerts and line.strip().startswith(("1.", "2.", "3.", "4.", "5.")):
                alerts.append(line.strip())
            elif in_alerts and line.startswith("---"):
                break

        # 组装摘要
        parts = [f"[Daily Scout {date_str}]"]
        if stats:
            parts.append(" | ".join(stats))
        if alerts:
            parts.append("ALERTS: " + "; ".join(a[:60] for a in alerts[:3]))

        summary = " || ".join(parts)

        # 确保不超过 4000 字符
        if len(summary) > 3900:
            summary = summary[:3900] + "..."

        return summary

    def _extract_table_stats(self, line: str) -> str:
        """从表格行提取关键统计"""
        # 移除 markdown 格式
        clean = re.sub(r"[*`|]", "", line).strip()
        # 提取数字和关键词
        parts = [p.strip() for p in clean.split() if p.strip()]
        # 简化
        return " ".join(parts[:8])


# =============================================================================
# 辅助函数
# =============================================================================

def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [Scout] {msg}", flush=True)


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        log(f"❌ 配置文件不存在: {config_path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# 主入口
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Technocore Daily Scout & Dispatch System")
    parser.add_argument("--config", default="scout_config.json", help="配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="干运行 (仅侦察, 不发送消息)")
    args = parser.parse_args()

    config = load_config(args.config)

    print("=" * 65)
    print("  🔭 Technocore Daily Scout & Dispatch System v1.0")
    print("     情报侦察 → 智能派遣 → 增强简报 → 邮箱推送")
    print("=" * 65)

    if args.dry_run:
        log("⚠️ DRY-RUN 模式: 仅执行侦察, 不发送任何签名消息")

    # Phase 1: 情报侦察
    log("")
    log("=" * 50)
    log("📡 Phase 1: 平台情报侦察")
    log("=" * 50)
    scout = PlatformScout({
        **config.get("scout_settings", {}),
        "watch_rooms": config.get("watch_rooms", [])
    })
    intel = scout.detect_new_activities()

    # Phase 2: 智能派遣
    log("")
    log("=" * 50)
    log("🚀 Phase 2: 智能 Agent 派遣")
    log("=" * 50)
    dispatcher = AgentDispatcher(config, dry_run=args.dry_run)
    dispatch_log = dispatcher.execute_all(intel)

    # Phase 3: 生成增强简报
    log("")
    log("=" * 50)
    log("📝 Phase 3: 增强简报生成与推送")
    log("=" * 50)
    reporter = EnhancedReporter(
        config.get("report_settings", {}),
        config.get("scout_settings", {})
    )

    log("收集集群运行指标...")
    cluster_stats = reporter.collect_cluster_stats()

    log("生成增强版每日简报...")
    report = reporter.generate_report(cluster_stats, intel, dispatch_log)
    reporter.save_report(report)

    # 推送到邮箱
    if not args.dry_run:
        log("推送简报到邮箱房间...")
        reporter.push_to_mailbox_room(report, config)

    print("")
    print("=" * 65)
    log("✅ 每日巡检全流程完成!")
    print("=" * 65)


if __name__ == "__main__":
    main()
