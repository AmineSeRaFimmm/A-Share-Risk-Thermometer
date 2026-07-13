"""Stage-based trade instructions from strict RT research.

Sources (do not invent rules):
  - research/output/strict: CSI300 RT strategy s006676
  - research/output/sector_flow: event-study sector excess returns

IMPORTANT:
  - Instructions are research playbooks, NOT investment advice.
  - Sector "flow" is excess return vs CSI300, not official fund flow.
  - Prefer high win-rate + significant sample rules; small-n rules are demoted.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.core.sector_etf_map import attach_etf_fields, map_csi300, map_sector

# ---- CSI300 primary rule (strict backtest, balanced IS/OOS) ----
CSI300_RULE = {
    "id": "s006676",
    "buy": {
        "rt_low": 60.0,
        "rt_high": 80.0,
        "hs300_dd60_max": -0.05,  # close/rolling_max_60 - 1 <= -5%
    },
    "sell": {"hold_trading_days": 5, "execution": "T+1 open after hold complete"},
    "execution": "signal_close_T_exec_open_T1",
    "stats": {
        "n": 49,
        "win_rate": 0.6327,
        "ann_return": 0.1035,
        "total_return": 0.8574,
        "max_dd": -0.1842,
        "avg_trade": 0.0107,
        "oos_n": 22,
        "oos_win_rate": 0.6364,
        "oos_total_return": 0.4702,
    },
}

# Stage playbooks: only rules with decent sample & edge
STAGE_DEFS = [
    {
        "stage_id": "CALM",
        "name_cn": "平静 / 低波动",
        "priority": 10,
        "detect": "rt < 40",
        "csi300": {
            "action": "HOLD_CASH_OR_SKIP",
            "action_cn": "不做 RT 主动做多沪深300",
            "detail": "历史低分段 20 日均收益接近 0 且胜率偏低；主策略不在此开仓。",
            "confidence": "HIGH",
        },
        "sectors_long": [
            {"name": "有色金属", "why": "平静区间未来5日超额均值约+0.65%，显著", "win_rate": 0.53, "n": 735, "horizon": "5D"},
            {"name": "煤炭", "why": "平静区间相对偏强（显著）", "win_rate": 0.49, "n": 509, "horizon": "5D"},
            {"name": "石油石化", "why": "平静区间相对偏强（显著）", "win_rate": 0.53, "n": 509, "horizon": "5D"},
        ],
        "sectors_short": [
            {"name": "美容护理", "why": "平静区间显著跑输大盘", "win_rate": 0.36, "n": 509, "horizon": "5D"},
            {"name": "恒生科技", "why": "平静区间显著跑输（非主升环境）", "win_rate": 0.40, "n": 679, "horizon": "5D"},
            {"name": "商贸零售", "why": "平静区间显著跑输", "win_rate": 0.42, "n": 735, "horizon": "5D"},
        ],
        "hold_days": 5,
        "notes": ["低风险环境避免用「恐慌博弈」思路做多弹性资产。"],
    },
    {
        "stage_id": "RISING_HARD",
        "name_cn": "升温冲击（ΔRT_5≥+5）",
        "priority": 40,
        "detect": "rt_d5 >= 5",
        "csi300": {
            "action": "NO_CHASE_SAME_DAY",
            "action_cn": "升温当日不追涨指数；若同时满足主策略买入条件则按主策略",
            "detail": "升温是波动抬升，不等于立刻满仓。主策略仍要求 60≤RT<80 且 60日回撤≤-5%。",
            "confidence": "HIGH",
        },
        "sectors_long": [
            {"name": "通信", "why": "升温后5日超额最高且极显著；IS/OOS同号", "win_rate": 0.59, "n": 438, "horizon": "5D", "mean_excess": 0.0079},
            {"name": "电子", "why": "升温后5日超额显著；IS/OOS同号", "win_rate": 0.55, "n": 438, "horizon": "5D", "mean_excess": 0.0066},
            {"name": "机械设备", "why": "升温后5日超额显著；IS/OOS同号", "win_rate": 0.57, "n": 438, "horizon": "5D", "mean_excess": 0.0045},
            {"name": "国防军工", "why": "升温后5日超额显著；IS/OOS同号", "win_rate": 0.55, "n": 438, "horizon": "5D", "mean_excess": 0.0040},
        ],
        "sectors_short": [
            {"name": "美容护理", "why": "升温后显著跑输", "win_rate": 0.40, "n": 327, "horizon": "5D"},
            {"name": "房地产", "why": "升温后显著跑输；IS/OOS稳定为负", "win_rate": 0.42, "n": 438, "horizon": "5D"},
            {"name": "钢铁", "why": "升温后显著跑输", "win_rate": 0.42, "n": 438, "horizon": "5D"},
        ],
        "same_day_note": "升温当日同步：银行/公用事业相对强；恒生科技/电子/计算机当日相对弱——先别抢反弹。",
        "hold_days": 5,
        "notes": ["板块指令是相对沪深300的超额偏好，持有约5日窗口；不建议升温当日追恒生科技。"],
    },
    {
        "stage_id": "CSI300_CORE_BUY",
        "name_cn": "主策略做多窗口（高胜率高收益核心）",
        "priority": 100,
        "detect": "60 <= rt < 80 and hs300_dd60 <= -0.05",
        "csi300": {
            "action": "BUY",
            "action_cn": "买入沪深300（或等价ETF）",
            "detail": "T日收盘满足条件 → T+1开盘买入；持有5个交易日 → 到期后下一开盘卖出。",
            "confidence": "HIGH",
            "rule_id": "s006676",
            "stats_summary": "全样本 n=49 胜率63% 年化~10% 总收益~86%；OOS n=22 胜率64% 总收益~47%",
        },
        "sectors_long": [
            {"name": "建筑材料", "why": "高风险区间[60,75) 未来5日超额显著、胜率~58%", "win_rate": 0.58, "n": 184, "horizon": "5D"},
            {"name": "商贸零售", "why": "高风险区间超额显著", "win_rate": 0.59, "n": 184, "horizon": "5D"},
            {"name": "传媒", "why": "高风险区间超额显著", "win_rate": 0.58, "n": 184, "horizon": "5D"},
            {"name": "恒生科技", "why": "高风险区间超额显著（弹性，仓位宜小于A股主仓）", "win_rate": 0.58, "n": 165, "horizon": "5D"},
        ],
        "sectors_short": [
            {"name": "公用事业", "why": "高风险区间相对偏弱（防御在升温冲击日才强）", "win_rate": 0.44, "n": 184, "horizon": "5D"},
            {"name": "银行", "why": "高风险区间相对不占优", "win_rate": 0.49, "n": 184, "horizon": "5D"},
        ],
        "hold_days": 5,
        "notes": [
            "这是全体系最高优先级的指数级指令。",
            "同一时间最多1笔主策略；持仓中忽略新信号。",
            "建议仓位研究区间 1/3~1/2，非回测满仓假设。",
        ],
    },
    {
        "stage_id": "ENTER_70_BOUNCE",
        "name_cn": "刚进入更高风险（穿越70）——弹性反弹窗口",
        "priority": 80,
        "detect": "prev_rt < 70 <= rt",
        "csi300": {
            "action": "OPTIONAL_IF_CORE_MET",
            "action_cn": "若同时满足主策略则执行主策略；否则不加仓指数",
            "detail": "穿越70本身样本较少；指数仍以主策略约束为准。",
            "confidence": "MEDIUM",
        },
        "sectors_long": [
            {
                "name": "恒生科技",
                "why": "进入≥70后5日超额约+2.5%，胜率75%，p<0.05（n=32，中等样本）",
                "win_rate": 0.75,
                "n": 32,
                "horizon": "5D",
                "mean_excess": 0.0253,
                "size_note": "高胜率但n=32，单笔风险预算宜小",
            },
        ],
        "sectors_short": [
            {"name": "石油石化", "why": "进入≥70后5日显著跑输，胜率仅27%", "win_rate": 0.27, "n": 30, "horizon": "5D"},
            {"name": "公用事业", "why": "进入高风险后相对偏弱", "win_rate": 0.38, "n": 34, "horizon": "5D"},
            {"name": "银行", "why": "进入高风险后相对偏弱", "win_rate": 0.47, "n": 34, "horizon": "5D"},
        ],
        "hold_days": 5,
        "notes": ["弹性反弹≠避险；与主策略冲突时以主策略与回撤约束优先。"],
    },
    {
        "stage_id": "HIGH_COOLING",
        "name_cn": "高位降温 / 避险轮动",
        "priority": 90,
        "detect": "rt_rollmax_10 >= 65 and rt >= 55 and rt_d5 <= -3 and rt_d1 < 0",
        "csi300": {
            "action": "STAND_DOWN_OR_HOLD_EXISTING",
            "action_cn": "主策略若已到期则不新开；已持有按规则卖",
            "detail": "高位回落阶段指数边沿不清晰；以板块相对避险与规则卖出为主。",
            "confidence": "MEDIUM",
        },
        "sectors_long": [
            {"name": "石油石化", "why": "高位回落5日超额+1.5%，胜率76%，显著（最高胜率组）", "win_rate": 0.76, "n": 37, "horizon": "5D", "mean_excess": 0.0149},
            {"name": "综合", "why": "高位回落超额+1.6%，胜率69%，显著", "win_rate": 0.69, "n": 39, "horizon": "5D"},
            {"name": "轻工制造", "why": "高位回落超额+1.5%，胜率69%，显著", "win_rate": 0.69, "n": 39, "horizon": "5D"},
            {"name": "煤炭", "why": "高位回落超额+1.5%，胜率59%，显著", "win_rate": 0.59, "n": 37, "horizon": "5D"},
            {"name": "传媒", "why": "高位回落超额+1.5%，胜率64%，显著", "win_rate": 0.64, "n": 39, "horizon": "5D"},
        ],
        "sectors_short": [
            {"name": "电子", "why": "高位回落阶段相对偏弱（均值负）", "win_rate": 0.49, "n": 39, "horizon": "5D"},
            {"name": "计算机", "why": "高位回落阶段相对偏弱", "win_rate": 0.44, "n": 39, "horizon": "5D"},
        ],
        "hold_days": 5,
        "notes": [
            "这是「避险/相对抗跌」指令，不是追涨科技。",
            "事件样本 n≈40，置信度低于升温大样本结论；仓位宜保守。",
        ],
    },
    {
        "stage_id": "FALLING_HARD",
        "name_cn": "降温（ΔRT_5≤-5）",
        "priority": 50,
        "detect": "rt_d5 <= -5",
        "csi300": {
            "action": "NO_NEW_LONG_UNLESS_CORE",
            "action_cn": "除非仍满足主策略持仓中，否则不新开指数多单",
            "detail": "降温阶段指数边沿取决于是否仍在60-80且回撤条件；默认观望。",
            "confidence": "MEDIUM",
        },
        "sectors_long": [
            {"name": "有色金属", "why": "降温后5日超额显著为正", "win_rate": 0.53, "n": 477, "horizon": "5D", "mean_excess": 0.0063},
            {"name": "煤炭", "why": "降温后5日超额显著为正", "win_rate": 0.52, "n": 339, "horizon": "5D", "mean_excess": 0.0061},
            {"name": "基础化工", "why": "降温后5日超额显著、胜率57%", "win_rate": 0.57, "n": 477, "horizon": "5D"},
            {"name": "电力设备", "why": "降温后5日超额显著", "win_rate": 0.53, "n": 477, "horizon": "5D"},
        ],
        "sectors_short": [
            {"name": "计算机", "why": "降温后显著跑输", "win_rate": 0.43, "n": 477, "horizon": "5D"},
            {"name": "传媒", "why": "降温后显著跑输", "win_rate": 0.44, "n": 477, "horizon": "5D"},
            {"name": "美容护理", "why": "降温后显著跑输", "win_rate": 0.42, "n": 339, "horizon": "5D"},
        ],
        "hold_days": 5,
        "notes": ["降温≠全面做多；偏资源/中游相对强，TMT相对弱。"],
    },
    {
        "stage_id": "PANIC_SMALL_N",
        "name_cn": "恐慌区（RT≥75，小样本）",
        "priority": 70,
        "detect": "rt >= 75",
        "csi300": {
            "action": "RESEARCH_ONLY_OR_CORE_IF_MET",
            "action_cn": "仅当仍满足主策略上沿前条件才考虑；禁止裸买极端恐慌",
            "detail": "区间前瞻收益高但样本薄；严格回测明确拒绝无确认的极端恐慌买入。",
            "confidence": "LOW",
        },
        "sectors_long": [
            {
                "name": "恒生科技",
                "why": "恐慌区间5日超额高、胜率72%，但n=25——研究仓",
                "win_rate": 0.72,
                "n": 25,
                "horizon": "5D",
                "size_note": "小样本，最多观察仓",
            },
            {
                "name": "有色金属",
                "why": "恐慌区间5日超额显著，n=25",
                "win_rate": 0.72,
                "n": 25,
                "horizon": "5D",
                "size_note": "小样本",
            },
        ],
        "sectors_short": [
            {"name": "公用事业", "why": "恐慌区间显著跑输，胜率仅20%", "win_rate": 0.20, "n": 25, "horizon": "5D"},
        ],
        "hold_days": 5,
        "notes": ["小样本阶段：允许研究指令，不允许重仓。"],
    },
]


def _dd60_from_index(index_history: pd.DataFrame) -> float | None:
    if index_history is None or index_history.empty:
        return None
    hs = index_history[index_history["symbol"].astype(str) == "sh000300"].copy()
    if hs.empty:
        return None
    hs = hs.sort_values("date")
    hs["close"] = pd.to_numeric(hs["close"], errors="coerce")
    if len(hs) < 20:
        return None
    dd = hs["close"] / hs["close"].rolling(60, min_periods=20).max() - 1
    val = dd.iloc[-1]
    return None if pd.isna(val) else float(val)


def classify_features(risk_components: pd.DataFrame, index_history: pd.DataFrame | None = None) -> dict[str, Any]:
    if risk_components is None or risk_components.empty:
        raise ValueError("risk_components empty")
    df = risk_components.copy().sort_values("trade_date")
    df["risk_temperature"] = pd.to_numeric(df["risk_temperature"], errors="coerce")
    df = df.dropna(subset=["risk_temperature"])
    latest = df.iloc[-1]
    rt = float(latest["risk_temperature"])
    prev = float(df.iloc[-2]["risk_temperature"]) if len(df) > 1 else rt
    rt_d1 = rt - prev
    rt_d5 = rt - float(df.iloc[-6]["risk_temperature"]) if len(df) > 6 else None
    rt_rollmax_10 = float(df["risk_temperature"].tail(10).max())
    dd60 = None
    if "sh000300_dd60" in latest.index and pd.notna(latest.get("sh000300_dd60")):
        dd60 = float(latest["sh000300_dd60"])
    else:
        dd60 = _dd60_from_index(index_history)

    return {
        "trade_date": str(latest["trade_date"]),
        "rt": rt,
        "prev_rt": prev,
        "rt_d1": rt_d1,
        "rt_d5": rt_d5,
        "rt_rollmax_10": rt_rollmax_10,
        "hs300_dd60": dd60,
        "regime": str(latest.get("regime", "")),
        "regime_cn": str(latest.get("regime_cn", "")),
        "quality": str(latest.get("quality", "")),
    }


def active_stages(feat: dict[str, Any]) -> list[str]:
    rt = feat["rt"]
    d5 = feat.get("rt_d5")
    d1 = feat.get("rt_d1")
    prev = feat.get("prev_rt")
    roll = feat.get("rt_rollmax_10")
    dd = feat.get("hs300_dd60")
    stages = []
    if rt < 40:
        stages.append("CALM")
    if d5 is not None and d5 >= 5:
        stages.append("RISING_HARD")
    if d5 is not None and d5 <= -5:
        stages.append("FALLING_HARD")
    if (
        roll is not None
        and d5 is not None
        and d1 is not None
        and roll >= 65
        and rt >= 55
        and d5 <= -3
        and d1 < 0
    ):
        stages.append("HIGH_COOLING")
    if prev is not None and prev < 70 <= rt:
        stages.append("ENTER_70_BOUNCE")
    if dd is not None and 60 <= rt < 80 and dd <= -0.05:
        stages.append("CSI300_CORE_BUY")
    if rt >= 75:
        stages.append("PANIC_SMALL_N")
    # fallback soft stage for high band without special event
    if 60 <= rt < 75 and "CSI300_CORE_BUY" not in stages and "HIGH_COOLING" not in stages:
        stages.append("CSI300_CORE_BUY" if dd is not None and dd <= -0.05 else "REGIME_HIGH_WATCH")
    return stages


def _stage_by_id(stage_id: str) -> dict | None:
    for s in STAGE_DEFS:
        if s["stage_id"] == stage_id:
            return s
    if stage_id == "REGIME_HIGH_WATCH":
        return {
            "stage_id": "REGIME_HIGH_WATCH",
            "name_cn": "高风险水平观望（未触发主策略回撤条件）",
            "priority": 30,
            "csi300": {
                "action": "WATCH",
                "action_cn": "观望指数：RT已高但60日回撤条件未满足",
                "detail": "可跟踪板块相对强弱，但不执行主策略买入。",
                "confidence": "MEDIUM",
            },
            "sectors_long": [
                {"name": "建筑材料", "why": "高风险区间相对偏强", "win_rate": 0.58, "n": 184, "horizon": "5D"},
                {"name": "商贸零售", "why": "高风险区间相对偏强", "win_rate": 0.59, "n": 184, "horizon": "5D"},
            ],
            "sectors_short": [
                {"name": "公用事业", "why": "高风险区间相对偏弱", "win_rate": 0.44, "n": 184, "horizon": "5D"},
            ],
            "hold_days": 5,
            "notes": ["等待回撤加深或信号明确。"],
        }
    return None


def build_playbook_payload(risk_components: pd.DataFrame, index_history: pd.DataFrame | None = None) -> dict[str, Any]:
    feat = classify_features(risk_components, index_history)
    stages = active_stages(feat)
    # sort by priority desc
    detailed = []
    for sid in stages:
        s = _stage_by_id(sid)
        if s:
            detailed.append(s)
    detailed.sort(key=lambda x: x.get("priority", 0), reverse=True)

    # primary instruction = highest priority active stage
    primary = detailed[0] if detailed else {
        "stage_id": "NEUTRAL",
        "name_cn": "中性观望",
        "csi300": {"action": "WATCH", "action_cn": "观望", "detail": "未命中高置信事件。", "confidence": "LOW"},
        "sectors_long": [],
        "sectors_short": [],
        "hold_days": 5,
        "notes": [],
    }

    core_buy = (
        feat.get("hs300_dd60") is not None
        and 60 <= feat["rt"] < 80
        and feat["hs300_dd60"] <= -0.05
    )

    instructions = []
    # flatten actionable list
    if core_buy:
        instructions.append(
            attach_etf_fields(
                {
                    "rank": 1,
                    "market": "CSI300",
                    "side": "BUY",
                    "name": "沪深300",
                    "instrument": "沪深300",
                    "trigger": "60≤RT<80 且 沪深300 60日回撤≤-5%",
                    "entry": "T+1 开盘",
                    "exit": "持有5个交易日后 T+1 开盘卖出",
                    "priority": "P0",
                    "win_rate": CSI300_RULE["stats"]["win_rate"],
                    "n": CSI300_RULE["stats"]["n"],
                    "evidence": CSI300_RULE["stats"],
                    "why": "严格回测最优：n=49 胜率63% 年化~10% OOS胜率64%",
                    "disclaimer": "研究指令，非投资建议",
                }
            )
        )
    for s in detailed:
        for i, sec in enumerate(s.get("sectors_long") or []):
            if sec.get("n", 0) < 25 and s["stage_id"] != "ENTER_70_BOUNCE":
                continue
            if sec.get("n", 0) < 25 and s["stage_id"] == "PANIC_SMALL_N":
                pass
            instructions.append(
                attach_etf_fields(
                    {
                        "rank": 10 + i,
                        "market": "SECTOR",
                        "side": "OVERWEIGHT_RELATIVE",
                        "name": sec["name"],
                        "instrument": sec["name"],
                        "stage": s["name_cn"],
                        "trigger": s.get("detect", s["stage_id"]),
                        "entry": "事件确认后 T+1",
                        "exit": f"持有约{s.get('hold_days', 5)}个交易日",
                        "priority": "P1" if sec.get("n", 0) >= 100 else "P2",
                        "win_rate": sec.get("win_rate"),
                        "n": sec.get("n"),
                        "why": sec.get("why"),
                        "size_note": sec.get("size_note", "相对收益指令，按映射 ETF 交易"),
                        "disclaimer": "超额收益代理资金偏好，非官方资金流",
                    }
                )
            )
        for i, sec in enumerate(s.get("sectors_short") or []):
            instructions.append(
                attach_etf_fields(
                    {
                        "rank": 50 + i,
                        "market": "SECTOR",
                        "side": "UNDERWEIGHT_RELATIVE",
                        "name": sec["name"],
                        "instrument": sec["name"],
                        "stage": s["name_cn"],
                        "trigger": s.get("detect", s["stage_id"]),
                        "entry": "事件确认后减配/回避",
                        "exit": f"约{s.get('hold_days', 5)}个交易日窗口",
                        "priority": "P1",
                        "win_rate": sec.get("win_rate"),
                        "n": sec.get("n"),
                        "why": sec.get("why"),
                        "disclaimer": "相对减配，不是裸空指令",
                    }
                )
            )

    flex_panel = _build_flex_panel(feat, stages, detailed, core_buy, primary)

    return {
        "title": "风险温度分阶段交易指令（严格研究版）",
        "disclaimer": "历史统计规律生成的研究指令，不构成投资建议；请控制仓位并自担风险。",
        "methodology": {
            "csi300_rule": CSI300_RULE,
            "sector_proxy": "excess_return = sector_return - csi300_return",
            "execution_default": "T close signal → T+1 open",
            "sources": [
                "research/output/strict/strict_rt_csi300_report.md",
                "research/output/sector_flow/strict_rt_sector_flow_report.md",
                "research/output/core_plus_sectors/core_plus_sectors_report.md",
            ],
        },
        "as_of": feat["trade_date"],
        "market_state": feat,
        "active_stage_ids": stages,
        "primary_stage": {
            "stage_id": primary.get("stage_id"),
            "name_cn": primary.get("name_cn"),
            "csi300": primary.get("csi300"),
            "notes": primary.get("notes", []),
        },
        "active_stages": [
            {
                "stage_id": s.get("stage_id"),
                "name_cn": s.get("name_cn"),
                "priority": s.get("priority"),
                "csi300": s.get("csi300"),
                "sectors_long": s.get("sectors_long", []),
                "sectors_short": s.get("sectors_short", []),
                "hold_days": s.get("hold_days", 5),
                "notes": s.get("notes", []),
                "same_day_note": s.get("same_day_note"),
            }
            for s in detailed
        ],
        "actionable_instructions": instructions,
        "flex_panel": flex_panel,
        "all_stage_catalog": [
            {
                "stage_id": s["stage_id"],
                "name_cn": s["name_cn"],
                "detect": s.get("detect"),
                "csi300_action": s.get("csi300", {}).get("action_cn"),
                "long": [x["name"] for x in s.get("sectors_long", [])],
                "short": [x["name"] for x in s.get("sectors_short", [])],
            }
            for s in STAGE_DEFS
        ],
    }


# Combined flex backtest snapshot (long-only high-conviction satellite)
FLEX_BACKTEST_STATS = {
    "mode": "combined_flex_long_only",
    "label_cn": "组合 Flex（核心+板块超配）",
    "core_weight_when_both": 0.60,
    "sat_weight_when_both": 0.40,
    "flex_rule_cn": "仅一仓激活时 100% 该仓；双仓同时 60% 核心 + 40% 卫星",
    "hold_days": 5,
    "execution": "T 收盘信号 → T+1 开盘",
    "full_sample": {
        "total_return": 4.0155,
        "ann_return": 0.2922,
        "max_dd": -0.1537,
        "win_rate": 0.625,
        "trade_count": 256,
    },
    "oos": {
        "total_return": 1.6078,
        "ann_return": 0.4868,
        "max_dd": -0.1096,
        "win_rate": 0.625,
        "trade_count": 104,
    },
    "core_only": {
        "total_return": 0.8523,
        "ann_return": 0.1030,
        "max_dd": -0.1065,
        "win_rate": 0.6531,
        "trade_count": 49,
    },
    "caveat_cn": "板块用行业指数代理 ETF，卫星交易较密，实盘收益应低于回测。",
}

# Satellite stage map aligned with backtest_core_plus_sectors.STAGE_LONG (strict)
FLEX_SAT_LONG = {
    "RISING_HARD": ["通信", "电子", "机械设备", "国防军工"],
    "CSI300_CORE_BUY": ["建筑材料", "商贸零售", "传媒", "恒生科技"],
    "ENTER_70_BOUNCE": ["恒生科技"],
    "HIGH_COOLING": ["石油石化", "综合", "轻工制造", "煤炭", "传媒"],
    "FALLING_HARD": ["有色金属", "煤炭", "基础化工", "电力设备"],
}
FLEX_SAT_SHORT = {
    "RISING_HARD": ["美容护理", "房地产", "钢铁"],
    "CSI300_CORE_BUY": ["公用事业", "银行"],
    "ENTER_70_BOUNCE": ["石油石化", "公用事业", "银行"],
    "HIGH_COOLING": ["电子", "计算机"],
    "FALLING_HARD": ["计算机", "传媒", "美容护理"],
}
FLEX_SAT_PRIORITY = [
    "CSI300_CORE_BUY",
    "HIGH_COOLING",
    "ENTER_70_BOUNCE",
    "RISING_HARD",
    "FALLING_HARD",
]


def _build_flex_panel(
    feat: dict[str, Any],
    stages: list[str],
    detailed: list[dict],
    core_buy: bool,
    primary: dict,
) -> dict[str, Any]:
    """Frontend-facing buy/sell panel for combined flex strategy."""
    sat_stage = next((s for s in FLEX_SAT_PRIORITY if s in stages), None)
    longs = list(FLEX_SAT_LONG.get(sat_stage or "", []))
    shorts = list(FLEX_SAT_SHORT.get(sat_stage or "", []))

    # Prefer names from active detailed stage if present (with why/win_rate)
    long_cards = []
    short_cards = []
    if sat_stage:
        st = next((d for d in detailed if d.get("stage_id") == sat_stage), None)
        if st:
            for sec in st.get("sectors_long") or []:
                if sec.get("name") in longs or not longs:
                    long_cards.append(
                        {
                            "name": sec["name"],
                            "side": "BUY",
                            "side_cn": "买入/超配",
                            "why": sec.get("why"),
                            "win_rate": sec.get("win_rate"),
                            "n": sec.get("n"),
                        }
                    )
            for sec in st.get("sectors_short") or []:
                if sec.get("name") in shorts or not shorts:
                    short_cards.append(
                        {
                            "name": sec["name"],
                            "side": "SELL",
                            "side_cn": "卖出/低配",
                            "why": sec.get("why"),
                            "win_rate": sec.get("win_rate"),
                            "n": sec.get("n"),
                        }
                    )
    if not long_cards and longs:
        long_cards = [{"name": n, "side": "BUY", "side_cn": "买入/超配"} for n in longs]
    if not short_cards and shorts:
        short_cards = [{"name": n, "side": "SELL", "side_cn": "卖出/低配"} for n in shorts]

    if core_buy:
        core_action = "BUY"
        core_action_cn = "买入"
        core_detail = "T 日收盘满足条件 → 下一交易日开盘买入；持有 5 个交易日后开盘卖出。"
        core_tone = "buy"
    else:
        core_action = "HOLD_OR_WAIT"
        core_action_cn = "观望/不新开"
        core_detail = "未同时满足 60≤RT<80 与 60日回撤≤-5%；核心仓不新开多单。"
        core_tone = "wait"
        # if currently would be sell from an open position we cannot know without position state
        if feat.get("rt", 0) < 60 or (feat.get("hs300_dd60") is not None and feat["hs300_dd60"] > -0.05):
            core_detail += " 若已持有主策略仓位，仍按 5 日持有到期卖出，不提前因阶段切换强平（回测规则）。"

    sat_active = bool(sat_stage and (long_cards or short_cards))
    if sat_active and core_buy:
        alloc_cn = "双仓：约 60% 核心沪深300 + 40% 卫星板块等权"
        alloc_mode = "BOTH"
    elif core_buy:
        alloc_cn = "仅核心：100% 沪深300 主策略（Flex）"
        alloc_mode = "CORE_ONLY"
    elif sat_active:
        alloc_cn = "仅卫星：100% 阶段板块篮子（Flex）"
        alloc_mode = "SAT_ONLY"
    else:
        alloc_cn = "空仓观望"
        alloc_mode = "FLAT"

    buy_list = []
    sell_list = []
    csi = map_csi300()
    if core_buy:
        buy_list.append(
            attach_etf_fields(
                {
                    "sleeve": "core",
                    "name": "沪深300",
                    "side": "BUY",
                    "side_cn": "买入",
                    "weight_hint": "Flex 满仓核心或 60%",
                    "entry": "T+1 开盘",
                    "exit": "持有 5 日 → 开盘卖出",
                    "why": "组合 Flex 核心规则（回测胜率约 65%）",
                }
            )
        )
    for c in long_cards:
        buy_list.append(
            attach_etf_fields(
                {
                    "sleeve": "satellite",
                    "name": c["name"],
                    "side": "BUY",
                    "side_cn": "买入/超配",
                    "weight_hint": "卫星等权" if alloc_mode != "CORE_ONLY" else "—",
                    "entry": "T+1 开盘",
                    "exit": "持有约 5 日",
                    "why": c.get("why") or "阶段超配",
                    "win_rate": c.get("win_rate"),
                    "n": c.get("n"),
                }
            )
        )
    for c in short_cards:
        sell_list.append(
            attach_etf_fields(
                {
                    "sleeve": "satellite",
                    "name": c["name"],
                    "side": "SELL",
                    "side_cn": "卖出/低配",
                    "weight_hint": "回避/减配（long-only 不持有）",
                    "entry": "立即减配",
                    "exit": "阶段窗口约 5 日",
                    "why": c.get("why") or "阶段低配",
                    "win_rate": c.get("win_rate"),
                    "n": c.get("n"),
                }
            )
        )

    # headline
    if alloc_mode == "BOTH":
        headline = "组合 Flex：核心买入 + 板块超配"
        status_cn = "双仓激活"
    elif alloc_mode == "CORE_ONLY":
        headline = "组合 Flex：核心买入（卫星未单独激活）"
        status_cn = "核心激活"
    elif alloc_mode == "SAT_ONLY":
        headline = "组合 Flex：板块卫星买入/轮动"
        status_cn = "卫星激活"
    else:
        headline = "组合 Flex：暂无开仓信号"
        status_cn = "观望"

    return {
        "status": status_cn,
        "status_code": alloc_mode,
        "headline": headline,
        "as_of": feat.get("trade_date"),
        "execution_cn": "信号日 T 收盘确认 → 下一交易日开盘执行",
        "hold_days": 5,
        "allocation_cn": alloc_cn,
        "allocation_mode": alloc_mode,
        "market_state": {
            "rt": feat.get("rt"),
            "rt_d1": feat.get("rt_d1"),
            "rt_d5": feat.get("rt_d5"),
            "hs300_dd60": feat.get("hs300_dd60"),
            "regime_cn": feat.get("regime_cn"),
        },
        "core": {
            "sleeve": "core",
            "name": "沪深300 主策略",
            "action": core_action,
            "action_cn": core_action_cn,
            "tone": core_tone,
            "detail": core_detail,
            "rule": "60≤RT<80 且 60日回撤≤-5%；持有5日",
            "active": core_buy,
            "etf_code": csi["etf_code"],
            "etf_name": csi["etf_name"],
            "etf_label": csi["etf_label"],
        },
        "satellite": {
            "sleeve": "satellite",
            "name": "板块超配卫星",
            "active": sat_active,
            "stage_id": sat_stage,
            "stage_cn": next((d.get("name_cn") for d in detailed if d.get("stage_id") == sat_stage), "无"),
            "buy": [attach_etf_fields(x) for x in long_cards],
            "sell": [attach_etf_fields(x) for x in short_cards],
            "detail": "仅高置信阶段；已映射到行业/主题 ETF（见 etf_code）；long-only 默认不做空。",
        },
        "buy_list": buy_list,
        "sell_list": sell_list,
        "sector_etf_map_version": "config/sector_etf_map.yml",
        "backtest": FLEX_BACKTEST_STATS,
        "disclaimer": "研究回测指令，非投资建议。ETF 映射见 config/sector_etf_map.yml；弱代理请降仓。",
        "primary_stage_cn": primary.get("name_cn"),
    }
