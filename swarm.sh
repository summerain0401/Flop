#!/usr/bin/env bash
# ==============================================================================
# swarm.sh - Technocore Multi-Agent Swarm Unified Controller
# ==============================================================================
# 支持一键全局启停 (5个 Agent 同步) 或精准单组启停 (大厅 3 个 / 交易 2 个解耦)
#
# 使用方法:
#   ./swarm.sh start [all|lobby|trader]     # 启动服务 (默认 all)
#   ./swarm.sh stop [all|lobby|trader]      # 优雅停止服务 (默认 all)
#   ./swarm.sh restart [all|lobby|trader]   # 平滑重启服务 (默认 all)
#   ./swarm.sh status                       # 查看全部 Agent 实时聚合状态与战报
#   ./swarm.sh logs [lobby|trader]          # 追踪查看指定集群日志
# ==============================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

mkdir -p logs

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ------------------------------------------------------------------------------
# 辅助函数: 获取运行中进程 PID
# ------------------------------------------------------------------------------
get_lobby_pid() {
    pgrep -fl "main.py" 2>/dev/null | grep -v "trader_main.py" | awk '{print $1}' | head -n 1 || true
}

get_trader_pid() {
    pgrep -fl "trader_main.py" 2>/dev/null | awk '{print $1}' | head -n 1 || true
}

get_process_uptime() {
    local pid="$1"
    if [ -n "$pid" ] && ps -p "$pid" > /dev/null 2>&1; then
        ps -p "$pid" -o etime= | tr -d ' '
    else
        echo "-"
    fi
}

# ------------------------------------------------------------------------------
# 启动模块
# ------------------------------------------------------------------------------
start_lobby() {
    local pid=$(get_lobby_pid)
    if [ -n "$pid" ]; then
        echo -e "${YELLOW}⚠️  [大厅交互集群] 已经在运行中 (PID: ${pid}, 运行时长: $(get_process_uptime "$pid"))，无需重复启动。${NC}"
        return 0
    fi

    echo -e "${CYAN}🚀 正在启动 [大厅交互集群] (Alpha-Node, Beta-Coder, Gamma-Sage)...${NC}"
    nohup python3 main.py >> logs/lobby.log 2>&1 &
    sleep 2
    local new_pid=$(get_lobby_pid)
    if [ -n "$new_pid" ]; then
        echo -e "${GREEN}✅ [大厅交互集群] 启动成功 (PID: ${new_pid}, 日志: logs/lobby.log)${NC}"
    else
        echo -e "${RED}❌ [大厅交互集群] 启动失败，请检查 logs/lobby.log 查看详细错误。${NC}"
        return 1
    fi
}

start_trader() {
    local pid=$(get_trader_pid)
    if [ -n "$pid" ]; then
        echo -e "${YELLOW}⚠️  [交易挑战赛集群] 已经在运行中 (PID: ${pid}, 运行时长: $(get_process_uptime "$pid"))，无需重复启动。${NC}"
        return 0
    fi

    echo -e "${CYAN}🚀 正在启动 [交易挑战赛集群] (Delta-Maker, Epsilon-Sniper)...${NC}"
    nohup python3 trader_main.py >> logs/trader.log 2>&1 &
    sleep 2
    local new_pid=$(get_trader_pid)
    if [ -n "$new_pid" ]; then
        echo -e "${GREEN}✅ [交易挑战赛集群] 启动成功 (PID: ${new_pid}, 日志: logs/trader.log)${NC}"
    else
        echo -e "${RED}❌ [交易挑战赛集群] 启动失败，请检查 logs/trader.log 查看详细错误。${NC}"
        return 1
    fi
}

# ------------------------------------------------------------------------------
# 停止模块
# ------------------------------------------------------------------------------
stop_lobby() {
    local pid=$(get_lobby_pid)
    if [ -z "$pid" ]; then
        echo -e "${YELLOW}ℹ️  [大厅交互集群] 当前未运行。${NC}"
        return 0
    fi

    echo -e "${CYAN}🛑 正在通知 [大厅交互集群] (PID: ${pid}) 优雅停机...${NC}"
    kill -15 "$pid" 2>/dev/null || true

    for i in {1..8}; do
        if ! ps -p "$pid" > /dev/null 2>&1; then
            echo -e "${GREEN}✅ [大厅交互集群] 已安全退出。${NC}"
            return 0
        fi
        sleep 1
    done

    echo -e "${YELLOW}⚠️  [大厅交互集群] 仍在运行，尝试强制退出...${NC}"
    kill -9 "$pid" 2>/dev/null || true
    echo -e "${GREEN}✅ [大厅交互集群] 已终止。${NC}"
}

stop_trader() {
    local pid=$(get_trader_pid)
    if [ -z "$pid" ]; then
        echo -e "${YELLOW}ℹ️  [交易挑战赛集群] 当前未运行。${NC}"
        return 0
    fi

    echo -e "${CYAN}🛑 正在通知 [交易挑战赛集群] (PID: ${pid}) 优雅停机...${NC}"
    kill -15 "$pid" 2>/dev/null || true

    for i in {1..8}; do
        if ! ps -p "$pid" > /dev/null 2>&1; then
            echo -e "${GREEN}✅ [交易挑战赛集群] 已安全退出。${NC}"
            return 0
        fi
        sleep 1
    done

    echo -e "${YELLOW}⚠️  [交易挑战赛集群] 仍在运行，尝试强制退出...${NC}"
    kill -9 "$pid" 2>/dev/null || true
    echo -e "${GREEN}✅ [交易挑战赛集群] 已终止。${NC}"
}

# ------------------------------------------------------------------------------
# 状态模块
# ------------------------------------------------------------------------------
show_status() {
    local lobby_pid=$(get_lobby_pid)
    local trader_pid=$(get_trader_pid)

    echo -e "${BOLD}========================================================================${NC}"
    echo -e "${BOLD}              Technocore Multi-Agent Swarm 运行状态监控                   ${NC}"
    echo -e "${BOLD}========================================================================${NC}"

    # 1. 大厅交互集群状态
    if [ -n "$lobby_pid" ]; then
        local uptime=$(get_process_uptime "$lobby_pid")
        echo -e "🟢 ${BOLD}[大厅交互集群] (Lobby Swarm)${NC} - ${GREEN}RUNNING${NC}"
        echo -e "   • 进程 PID    : ${CYAN}${lobby_pid}${NC}"
        echo -e "   • 持续运行时长 : ${GREEN}${uptime}${NC}"
        echo -e "   • 目标房间    : ${BLUE}/r/lobby${NC}"
        echo -e "   • 成员 Agent  : ${BOLD}Alpha-Node, Beta-Coder, Gamma-Sage (3个)${NC}"
        echo -e "   • 任务职能    : 社区对话交互、心跳聚合、24h水龙头、早5点报表"
    else
        echo -e "🔴 ${BOLD}[大厅交互集群] (Lobby Swarm)${NC} - ${RED}STOPPED${NC}"
    fi

    echo ""

    # 2. 交易挑战赛集群状态
    if [ -n "$trader_pid" ]; then
        local uptime=$(get_process_uptime "$trader_pid")
        echo -e "🟢 ${BOLD}[交易挑战赛集群] (Close Call Swarm)${NC} - ${GREEN}RUNNING${NC}"
        echo -e "   • 进程 PID    : ${CYAN}${trader_pid}${NC}"
        echo -e "   • 持续运行时长 : ${GREEN}${uptime}${NC}"
        echo -e "   • 交易房间    : ${BLUE}/r/close1${NC}"
        echo -e "   • 成员 Agent  : ${BOLD}Delta-Maker (做市网格), Epsilon-Sniper (吃单狙击)${NC}"
        echo -e "   • 初始资金    : 各持 10,000 POLF 保证金资格已生效"
        echo -e "   • 任务职能    : 盘面 NVDA 移动均线跟踪、Maker 分层挂单、Taker 毫秒级狙击"
    else
        echo -e "🔴 ${BOLD}[交易挑战赛集群] (Close Call Swarm)${NC} - ${RED}STOPPED${NC}"
    fi

    echo -e "${BOLD}========================================================================${NC}"
}

# ------------------------------------------------------------------------------
# 日志查看模块
# ------------------------------------------------------------------------------
show_logs() {
    local target="${1:-lobby}"
    if [ "$target" = "trader" ]; then
        echo -e "${CYAN}📜 正在追踪交易集群最新日志 (按 Ctrl+C 退出)...${NC}"
        tail -f -n 30 logs/trader.log 2>/dev/null || (ls -t /Users/spring/.gemini/antigravity-cli/brain/bf9c3952-f057-4478-8a1c-da04ce373861/.system_generated/tasks/task-655.log 2>/dev/null | xargs tail -f -n 30)
    else
        echo -e "${CYAN}📜 正在追踪大厅交互最新日志 (按 Ctrl+C 退出)...${NC}"
        tail -f -n 30 logs/lobby.log 2>/dev/null || (ls -t /Users/spring/.gemini/antigravity-cli/brain/bf9c3952-f057-4478-8a1c-da04ce373861/.system_generated/tasks/task-466.log 2>/dev/null | xargs tail -f -n 30)
    fi
}

# ------------------------------------------------------------------------------
# 命令行分发
# ------------------------------------------------------------------------------
COMMAND="${1:-status}"
TARGET="${2:-all}"

case "$COMMAND" in
    start)
        case "$TARGET" in
            all)
                start_lobby
                start_trader
                ;;
            lobby)
                start_lobby
                ;;
            trader)
                start_trader
                ;;
            *)
                echo -e "${RED}未知目标: $TARGET (可选: all, lobby, trader)${NC}"
                exit 1
                ;;
        esac
        ;;
    stop)
        case "$TARGET" in
            all)
                stop_trader
                stop_lobby
                ;;
            lobby)
                stop_lobby
                ;;
            trader)
                stop_trader
                ;;
            *)
                echo -e "${RED}未知目标: $TARGET (可选: all, lobby, trader)${NC}"
                exit 1
                ;;
        esac
        ;;
    restart)
        case "$TARGET" in
            all)
                stop_trader
                stop_lobby
                sleep 1
                start_lobby
                start_trader
                ;;
            lobby)
                stop_lobby
                sleep 1
                start_lobby
                ;;
            trader)
                stop_trader
                sleep 1
                start_trader
                ;;
            *)
                echo -e "${RED}未知目标: $TARGET (可选: all, lobby, trader)${NC}"
                exit 1
                ;;
        esac
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs "$TARGET"
        ;;
    *)
        echo -e "${BOLD}Technocore Swarm 控制器使用帮助:${NC}"
        echo -e "  $0 status                     # 查看全部 Agent 实时状态"
        echo -e "  $0 start [all|lobby|trader]   # 启动服务 (默认 all)"
        echo -e "  $0 stop [all|lobby|trader]    # 停止服务 (默认 all)"
        echo -e "  $0 restart [all|lobby|trader] # 重启服务 (默认 all)"
        echo -e "  $0 logs [lobby|trader]        # 查看实时日志"
        exit 1
        ;;
esac
