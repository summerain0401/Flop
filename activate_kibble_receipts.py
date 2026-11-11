#!/usr/bin/env python3
"""
activate_kibble_receipts.py - 为指定 Agent 快速激活 Kibble 有用功凭证 (one receipted delivery)

机制：
针对大厅集群 Agent (Beta-Coder, Alpha-Node, Gamma-Sage)，顺次在 /r/kibble 持续抢单并提交高质量任务（带 1.5s 冷却），
直到该 Agent 的任意一笔交付获得官方裁判的 USEFUL 认证回执（通常 1-2 分钟内即有抽检通过），
从而永久激活其在 FlopMarkets 预测市场及生态各处的正式计分与结算资格。
"""

import sys
import time
import json
import re
from typing import Dict, Any, Set

from identity import AgentIdentity
from technocore_client import TechnocoreClient
from kibble_agent import KibbleSolver, safe_truncate

TARGET_AGENTS = ["Beta-Coder", "Alpha-Node", "Gamma-Sage"]

JOB_PATTERN = re.compile(r"^JOB\s+v1\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*(.+)$", re.IGNORECASE)
ATTEST_PATTERN = re.compile(r"^ATTEST\s+v1\s*\|\s*([^|]+)\s*\|\s*(useful|not)\s*\|?(.*)$", re.IGNORECASE)


def activate_agent(agent_name: str, client: TechnocoreClient, solver: KibbleSolver) -> bool:
    print(f"\n{'='*65}", flush=True)
    print(f"🚀 开始为 [{agent_name}] 申请并激活 Kibble 有用功凭证 (USEFUL 回执)...", flush=True)
    
    identity = AgentIdentity(agent_name)
    print(f"🆔 DID: {identity.did}", flush=True)

    # 1. 确保已登记 Note
    shard, key = identity.get_sharded_did_path()
    mailbox = identity.mailbox_name
    client.publish_did_note(shard, key, identity.did, nick=agent_name, mailbox=mailbox)

    # 2. 锚定房间最新游标
    init_data = client.get_messages(room="kibble", wait=0, limit=1)
    last_seq = init_data.get("last_seq", 0)
    print(f"📍 游标已锚定在 /r/kibble seq: {last_seq}", flush=True)

    delivered_jobs: Set[str] = set()
    last_claim_time = 0.0
    claim_cooldown = 1.5
    max_wait_seconds = 240
    start_time = time.time()

    while time.time() - start_time < max_wait_seconds:
        data = client.get_messages(room="kibble", since=last_seq, wait=3, limit=30)
        messages = data.get("messages", [])
        
        for msg in messages:
            seq = msg.get("seq")
            if seq is not None and (last_seq is None or seq > last_seq):
                last_seq = seq

            text = msg.get("text", "").strip()

            # 1. 监控裁判回执 ATTEST
            attest_match = ATTEST_PATTERN.match(text)
            if attest_match:
                a_id, a_verdict, a_reason = attest_match.groups()
                a_id = a_id.strip()
                a_verdict = a_verdict.strip().lower()

                if a_id in delivered_jobs:
                    if a_verdict == "useful":
                        print(f"\n🎉🎉🎉 [{agent_name}] 任务 {a_id} 成功获得 USEFUL 认证背书!", flush=True)
                        print(f"裁判评语: {a_reason.strip()[:140]}", flush=True)
                        print(f"✅ [{agent_name}] 有用功资质已永久生效 (one receipted delivery 达成)!", flush=True)
                        return True
                    else:
                        print(f"📝 [{agent_name}] 任务 {a_id} 裁判反馈: NOT ({a_reason.strip()[:60]})，继续交付更多任务...", flush=True)

            # 2. 持续捕获新任务并交付
            now = time.time()
            if now - last_claim_time >= claim_cooldown:
                job_match = JOB_PATTERN.match(text)
                if job_match:
                    j_id, j_cat, j_prompt = job_match.groups()
                    j_id = j_id.strip()
                    j_cat = j_cat.strip()
                    j_prompt = j_prompt.strip()

                    if len(j_prompt) < 25 or j_id in delivered_jobs:
                        continue

                    # 抢单 CLAIM
                    claim_text = f"CLAIM v1 | {j_id} | worker"
                    cleaned_c, nonce_c, sig_c = identity.sign_message("kibble", claim_text)
                    ok_c, code_c, resp_c = client.say_signed("kibble", identity.did, sig_c, nonce_c, cleaned_c)
                    
                    if not ok_c and code_c != 200:
                        continue

                    last_claim_time = now
                    print(f"⚡ [{agent_name}] 成功抢单 {j_id} ({j_cat})，正在求解...", flush=True)
                    solution = solver.solve(j_cat, j_prompt)
                    solution = safe_truncate(solution, 2400)

                    # 交付 DELIVER
                    deliver_text = f"DELIVER v1 | {j_id} | {solution}"
                    cleaned_d, nonce_d, sig_d = identity.sign_message("kibble", deliver_text)
                    ok_d, code_d, resp_d = client.say_signed(
                        "kibble", identity.did, sig_d, nonce_d, cleaned_d, use_post=(len(cleaned_d) > 800)
                    )

                    if ok_d or code_d == 200:
                        delivered_jobs.add(j_id)
                        print(f"🚀 [{agent_name}] 已交付任务 {j_id} (当前累计交付 {len(delivered_jobs)} 笔，等待抽检)...", flush=True)

    print(f"❌ [{agent_name}] 激活超时 (未在 {max_wait_seconds}s 内捕获到抽检回执)", flush=True)
    return False


def main():
    llm_cfg = {}
    try:
        with open("kibble_config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
            llm_cfg = cfg.get("llm_settings", {})
    except Exception:
        pass

    client = TechnocoreClient()
    solver = KibbleSolver(llm_cfg)

    print("==================================================================", flush=True)
    print("    Technocore Agent 有用功凭证激活工具 (Receipted Delivery)      ", flush=True)
    print("==================================================================", flush=True)

    results = {}
    for agent_name in TARGET_AGENTS:
        ok = activate_agent(agent_name, client, solver)
        results[agent_name] = ok
        if ok:
            time.sleep(3)

    print("\n" + "="*65, flush=True)
    print("🎯 全员激活执行结果汇总:", flush=True)
    for agent, ok in results.items():
        status_str = "✅ 成功认证 (USEFUL 凭证生效)" if ok else "❌ 认证未完成"
        print(f"   • {agent:<15} : {status_str}", flush=True)
    print("="*65, flush=True)


if __name__ == "__main__":
    main()
