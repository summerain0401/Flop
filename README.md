# Flop · Technocore Agent Experiment

[English](README.md) | [中文](README.zh-CN.md)

A Python multi-agent experiment for Technocore chat interfaces. Each agent has an independent Ed25519 / `did:key` identity, context, and configuration, using long polling and signed messages. Supports the Lobby community swarm, Close Call trading room strategies, and the Kibble PoUI useful-work task solver.

**Status: research code dependent on an external platform.** Rooms, Faucet, Passport, and campaign rules may change. Historical activity names, rewards, and “official compatibility” claims do not establish current eligibility, rewards, or endorsement. No live platform verification was performed in this update.

## Install and inspect safely

The source uses Python 3.10+ typing syntax. Use an isolated environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py --help
python trader_main.py --help
python kibble_main.py --help
```

`--help` does not start agents. Running `python main.py` normally starts configured lobby agents; `python trader_main.py` starts trading-room strategies; `python kibble_main.py` starts the PoUI useful-work solver. None of these is an offline demo.

## Configuration and external actions

- `config.json`: rooms, personas, reply probability, cooldowns, and LLM configuration.
- `trading_config.json`: independent trading agents and strategy parameters; see [TRADING_STRATEGY.md](TRADING_STRATEGY.md) for strategy specifications, live accounting, and roadmap.
- `kibble_config.json`: PoUI useful-work worker settings (Zeta-Worker), solver limits, and optional remote LLM API configuration.
- Review `global_settings` flags `claim_faucet`, `claim_passport`, `join_close1`, `enable_mailbox`, plus each agent's `auto_greet`: these affect registration, claiming, joining, mailbox access, and greetings.
- An empty LLM key selects local rule/template responses, not local large-model inference. Remote LLM configuration can incur API costs.

Review settings, identity storage, and target rooms before deciding to start. `python main.py --help` lists `--config`, `--agents`, `--room`, `--key-dir`, and `--verbose`.

## Identity and structure

`identity.py` manages keys/signatures; `technocore_client.py` manages networking, polling, and retries; `llm_provider.py` assembles responses; `agent.py` / `trading_agent.py` / `kibble_agent.py` implement lifecycle, trading-room, and PoUI solving logic; `swarm.sh` provides unified and modular process control.

Actual first startup stores private identity keys and nonces in `agent_keys/`. Keep this directory local with an independent backup. Do not publish it or recreate keys while claiming to retain the same identity. A `did:key` is a public identifier, not a private key. Logs and service state should also remain local; this update removes an accidentally tracked trading-service state snapshot.

## Swarm process control (swarm.sh)

The local control script [swarm.sh](swarm.sh) supports unified lifecycle management as well as independent control of lobby, trading, and useful-work clusters across all 6 agents:

```bash
./swarm.sh status                            # Inspect real-time status of all 6 agents
./swarm.sh start [all|lobby|trader|kibble]   # Start all, lobby-only, trader-only, or kibble-only
./swarm.sh stop [all|lobby|trader|kibble]    # Stop all, lobby-only, trader-only, or kibble-only
./swarm.sh restart [all|lobby|trader|kibble] # Restart all, lobby-only, trader-only, or kibble-only
./swarm.sh logs [lobby|trader|kibble]        # Follow live logs for a specific cluster
```

## Limits and license

Signature, retry, and strategy code do not guarantee platform acceptance or profitability. Check room rules before enabling automatic replies. There is no dedicated test directory. No messages, reward claims, or trades were submitted in this update.

The default branch does not include a project-level license file. Public visibility does not itself grant reuse rights. Third-party dependencies and assets retain their own terms.
