# Flop · Technocore Agent 实验

[English](README.md) | [简体中文](README.zh-CN.md)

面向 Technocore 聊天接口的 Python 多智能体实验。每个 Agent 使用独立的 Ed25519 / `did:key` 身份、上下文和配置，通过长轮询监听房间并提交签名消息；涵盖大厅交互集群、Close Call 期货对盘集群以及 Kibble PoUI 有用功推理抢单集群。

**当前状态：依赖外部平台的研究代码。** 房间、Faucet、Passport 与活动规则可能变化；仓库中的旧活动名称、奖励和“官方适配”说法不代表当前资格、奖励或平台背书。本次整理未连接平台核验。

## 安装与只读入口检查

源码含 Python 3.10+ 类型语法，建议使用独立环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py --help
python trader_main.py --help
python kibble_main.py --help
```

以上 `--help` 不启动 Agent。正常执行 `python main.py` 会运行配置中的大厅 Agent 并可能自动向外部平台发消息；`python trader_main.py` 会启动交易房间策略；`python kibble_main.py` 会启动有用功抢单与解答，三者都不是离线演示。

## 配置与外部动作

- `config.json`：大厅房间、Agent 人设、回复概率、冷却与 LLM 配置。
- `trading_config.json`：独立交易 Agent 与策略参数；策略数学模型、实盘战报与后续优化路线详见 [TRADING_STRATEGY.md](TRADING_STRATEGY.md)。
- `kibble_config.json`：有用功推理抢单 Worker（Zeta-Worker）配置、解答长度限制与可选的远程 LLM API 配置。
- `global_settings` 中的 `claim_faucet`、`claim_passport`、`join_close1`、`enable_mailbox` 以及各 Agent 的 `auto_greet` 会影响注册、领取、入驻、收信和问候行为；启动前逐项检查。
- 空 LLM key 使用本地规则 / 模板回复，不是本地大模型推理；配置远程 LLM 后可能产生 API 费用。

先核对配置、身份保存位置和要访问的房间，再决定是否启动。常用参数示例见 `python main.py --help`，包括 `--config`、`--agents`、`--room`、`--key-dir` 和 `--verbose`。

## 身份与结构

`identity.py` 管理密钥与签名；`technocore_client.py` 处理网络、长轮询和重试；`llm_provider.py` 组装回复；`agent.py` / `trading_agent.py` / `kibble_agent.py` 分别实现大厅、交易与有用功抢单业务逻辑；`swarm.sh` 提供三大集群与 6 个 Agent 的统一与独立管理脚本。

首次实际运行会在 `agent_keys/` 保存身份私钥与 nonce。该目录必须保留在本地并独立备份，不应公开或重新生成后冒充原身份。`did:key` 是公开标识，不是私钥。运行日志和服务状态也应留在本地，本次整理移除了误跟踪的交易服务状态快照。

## 集群控制 (swarm.sh)

提供本地控制脚本 [swarm.sh](swarm.sh)，既支持一键同步控制全部 6 个 Agent，也支持对大厅交互组、交易组与有用功组进行解耦单组操作：

```bash
./swarm.sh status                            # 查看三大集群与全量 6 个 Agent 的实时状态
./swarm.sh start [all|lobby|trader|kibble]   # 启动全部、仅大厅、仅交易或仅有用功
./swarm.sh stop [all|lobby|trader|kibble]    # 停止全部、仅大厅、仅交易或仅有用功
./swarm.sh restart [all|lobby|trader|kibble] # 重启全部、仅大厅、仅交易或仅有用功
./swarm.sh logs [lobby|trader|kibble]        # 追踪查看指定集群实时日志
```

## 限制与许可

签名、网络重试和策略代码不保证平台接受请求或策略盈利；自动回复可能不符合房间规则，需自行确认。仓库没有独立测试目录；本次未发送消息、申领奖励或提交交易。

当前默认分支未提供项目级许可证文件；公开可见不等于授予复用许可。第三方依赖及素材保留各自条款。
