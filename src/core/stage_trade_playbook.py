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

from src.core.flex_engine import MODE_AGGRESSIVE, build_flex_panel_v2, load_backtest_stats_file
from src.core.sector_etf_map import attach_etf_fields

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


def extend_risk_for_playbook(
    risk_components: pd.DataFrame,
    index_history: pd.DataFrame | None = None,
    *,
    nowcast_rt: float | None = None,
    nowcast_trade_date: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Append missing HS300 sessions after last official risk row for Flex/playbook only.

    Official risk_components often lags index by 1–N sessions when option close fetch fails.
    Latest/nowcast can already be on the index date. Bridge those days so as_of matches
    the latest market session the site is talking about.

    Does NOT rewrite official history used for temperature charts — caller must keep
    official risk separate from this extended frame.
    """
    meta: dict[str, Any] = {
        "bridged": False,
        "official_as_of": None,
        "bridged_dates": [],
        "bridge_source": None,
    }
    if risk_components is None or risk_components.empty:
        return risk_components, meta

    risk = risk_components.copy()
    risk["trade_date"] = pd.to_datetime(risk["trade_date"], errors="coerce")
    risk = risk.dropna(subset=["trade_date"]).sort_values("trade_date")
    official_last = risk["trade_date"].max()
    meta["official_as_of"] = str(official_last.date())

    if index_history is None or index_history.empty:
        return risk_components, meta

    hs = index_history[index_history["symbol"].astype(str) == "sh000300"].copy()
    if hs.empty:
        return risk_components, meta
    hs["date"] = pd.to_datetime(hs["date"], errors="coerce")
    hs["close"] = pd.to_numeric(hs["close"], errors="coerce")
    hs = hs.dropna(subset=["date", "close"]).sort_values("date")
    future = hs[hs["date"] > official_last]
    if future.empty:
        return risk_components, meta

    # Prefer explicit nowcast for the tip day; otherwise carry last official RT.
    last_row = risk.iloc[-1].to_dict()
    fill_rt = float(last_row.get("risk_temperature"))
    bridge_source = "CARRY_OFFICIAL_RT"
    if nowcast_rt is not None and pd.notna(nowcast_rt):
        fill_rt = float(nowcast_rt)
        bridge_source = "NOWCAST_RT"
    meta["bridge_source"] = bridge_source

    closes = hs.set_index("date")["close"]
    roll_max = closes.rolling(60, min_periods=20).max()
    dd_series = closes / roll_max - 1.0

    add_rows: list[dict[str, Any]] = []
    for _, r in future.iterrows():
        d = pd.Timestamp(r["date"])
        # Use nowcast RT only on the nowcast trade_date when provided; earlier gaps carry official.
        rt_use = fill_rt
        if nowcast_trade_date and str(d.date()) != str(nowcast_trade_date)[:10]:
            rt_use = float(last_row.get("risk_temperature"))
        row = {c: last_row.get(c) for c in risk.columns}
        row["trade_date"] = d
        row["risk_temperature"] = rt_use
        row["sh000300_close"] = float(r["close"])
        if d in dd_series.index and pd.notna(dd_series.loc[d]):
            row["sh000300_dd60"] = float(dd_series.loc[d])
        q = str(row.get("quality") or "")
        row["quality"] = (q + "|" if q and q != "nan" else "") + "NOWCAST_BRIDGE"
        row["regime"] = row.get("regime") or "HIGH_RISK"
        row["regime_cn"] = row.get("regime_cn") or "桥接"
        add_rows.append(row)
        meta["bridged_dates"].append(str(d.date()))

    if not add_rows:
        return risk_components, meta

    ext = pd.concat([risk, pd.DataFrame(add_rows)], ignore_index=True)
    ext["trade_date"] = pd.to_datetime(ext["trade_date"]).dt.strftime("%Y-%m-%d")
    meta["bridged"] = True
    return ext, meta


def build_playbook_payload(
    risk_components: pd.DataFrame,
    index_history: pd.DataFrame | None = None,
    *,
    bridge_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
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

    flex_panel = build_flex_panel_v2(
        feat,
        stages,
        detailed,
        core_buy,
        primary,
        risk_components=risk_components,
        index_history=index_history,
        mode=MODE_AGGRESSIVE,
        backtest_stats=load_backtest_stats_file(),
    )

    data_quality: dict[str, Any] = {
        "risk_source": "OFFICIAL_CLOSE",
        "official_as_of": None if not bridge_meta else bridge_meta.get("official_as_of"),
        "bridged": bool(bridge_meta and bridge_meta.get("bridged")),
        "bridged_dates": [] if not bridge_meta else list(bridge_meta.get("bridged_dates") or []),
        "bridge_source": None if not bridge_meta else bridge_meta.get("bridge_source"),
        "note_cn": (
            "策略书 as_of 已用指数日历 + NOWCAST/桥接补齐正式 RT 缺口；温度图正式序列仍以 official 为准。"
            if bridge_meta and bridge_meta.get("bridged")
            else "策略书 as_of 与正式 risk_components 一致。"
        ),
    }
    if bridge_meta and bridge_meta.get("bridged"):
        data_quality["risk_source"] = "OFFICIAL_PLUS_NOWCAST_BRIDGE"

    return {
        "title": "风险温度分阶段交易指令（严格研究版）",
        "disclaimer": "历史统计规律生成的研究指令，不构成投资建议；请控制仓位并自担风险。",
        "methodology": {
            "csi300_rule": CSI300_RULE,
            "sector_proxy": "excess_return = sector_return - csi300_return",
            "execution_default": "T close signal → T+1 open",
            "flex_version": "v2_state_machine_quality_merge",
            "sources": [
                "research/output/strict/strict_rt_csi300_report.md",
                "research/output/sector_flow/strict_rt_sector_flow_report.md",
                "research/output/core_plus_sectors/core_plus_sectors_report.md",
            ],
        },
        "as_of": feat["trade_date"],
        "data_quality": data_quality,
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
