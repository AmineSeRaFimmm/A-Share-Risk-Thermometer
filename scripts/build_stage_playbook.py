#!/usr/bin/env python3
"""Build stage trade playbook JSON for site + research output."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.sector_etf_map import all_sector_mappings
from src.core.stage_trade_playbook import build_playbook_payload
from src.storage.csv_store import read_csv
from src.storage.json_store import write_json
from src.storage.paths import CALCULATED, DOCS, NORMALIZED, SITE, ensure_dirs
import pandas as pd


def main() -> None:
    ensure_dirs()
    risk = read_csv(CALCULATED / "risk_components.csv")
    index_history = read_csv(NORMALIZED / "index_history.csv")
    if risk.empty:
        raise SystemExit("risk_components.csv missing")
    payload = build_playbook_payload(risk, index_history)
    write_json(payload, SITE / "stage_playbook.json")
    research_out = Path("research/output/playbook")
    research_out.mkdir(parents=True, exist_ok=True)
    write_json(payload, research_out / "stage_playbook.json")

    mapping_rows = all_sector_mappings()
    map_df = pd.DataFrame(mapping_rows)
    map_df.to_csv(research_out / "sector_etf_mapping.csv", index=False)
    write_json({"rows": mapping_rows}, SITE / "sector_etf_map.json")

    # human markdown
    md = _to_markdown(payload, mapping_rows)
    (research_out / "stage_trade_instructions.md").write_text(md, encoding="utf-8")
    (research_out / "sector_etf_mapping.md").write_text(_mapping_md(mapping_rows), encoding="utf-8")

    if DOCS.exists():
        data_dir = DOCS / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SITE / "stage_playbook.json", data_dir / "stage_playbook.json")
        shutil.copy2(SITE / "sector_etf_map.json", data_dir / "sector_etf_map.json")

    print(
        f"Playbook as_of={payload['as_of']} primary={payload['primary_stage']['name_cn']} "
        f"stages={payload['active_stage_ids']} instructions={len(payload['actionable_instructions'])} "
        f"etf_maps={len(mapping_rows)}"
    )


def _pct(x):
    try:
        return f"{float(x)*100:.1f}%"
    except Exception:
        return "--"


def _mapping_md(rows: list) -> str:
    lines = [
        "# 申万一级 / 风格 → ETF 映射表",
        "",
        "来源: `config/sector_etf_map.yml`（人工精选，优先名称贴合 + 可交易 ETF）",
        "",
        "| 板块 | ETF代码 | ETF名称 | 贴合度 | 备注 |",
        "|------|---------|---------|--------|------|",
    ]
    for r in rows:
        lines.append(
            f"| {r.get('sector')} | {r.get('etf_code')} | {r.get('etf_name')} | "
            f"{r.get('quality_cn')} | {r.get('note') or '—'} |"
        )
    lines.append("")
    return "\n".join(lines)


def _to_markdown(p: dict, mapping_rows: list | None = None) -> str:
    ms = p["market_state"]
    flex = p.get("flex_panel") or {}
    lines = [
        "# 风险温度分阶段交易指令（严格研究版）",
        "",
        f"> **免责声明**：{p['disclaimer']}",
        "",
        f"- 生成日期（数据 as_of）: **{p['as_of']}**",
        f"- 当前 RT: **{ms['rt']:.1f}**（{ms.get('regime_cn') or ms.get('regime')}）",
        f"- ΔRT_1d: **{ms.get('rt_d1'):+.1f}** | ΔRT_5d: **{(ms.get('rt_d5') if ms.get('rt_d5') is not None else float('nan')):+.1f}**"
        if ms.get("rt_d5") is not None
        else f"- ΔRT_1d: **{ms.get('rt_d1'):+.1f}**",
        f"- 近10日 RT 峰值: **{ms.get('rt_rollmax_10'):.1f}**",
        f"- 沪深300 60日回撤: **{_pct(ms.get('hs300_dd60'))}**",
        f"- 激活阶段: {', '.join(p.get('active_stage_ids') or [])}",
        f"- ETF 映射: `config/sector_etf_map.yml`",
        "",
        "## 今日主指令",
        "",
        f"### {p['primary_stage']['name_cn']}",
        "",
        f"- **指数**: {p['primary_stage']['csi300'].get('action_cn')}",
        f"- 细节: {p['primary_stage']['csi300'].get('detail')}",
        f"- 置信度: {p['primary_stage']['csi300'].get('confidence')}",
        "",
    ]
    if flex:
        lines += [
            f"- Flex v2 状态: **{flex.get('status')}** · 模式 `{flex.get('mode')}` · {flex.get('allocation_cn')}",
            f"- 合并: {flex.get('merge_note_cn') or '—'}",
            "",
            "### 今日最小动作",
            "",
        ]
        for b in flex.get("minimal_actions") or []:
            lines.append(
                f"- **{b.get('action_cn') or b.get('action')}** {b.get('instrument_display') or b.get('name')} "
                f"| 目标 {b.get('weight_hint') or '—'} | {b.get('entry')} → {b.get('exit')}"
            )
        lines += ["", "### 新开 / 超配", ""]
        for b in flex.get("buy_list") or []:
            lines.append(
                f"- **{b.get('action_cn') or '新开'}** {b.get('instrument_display') or b.get('name')} "
                f"| {b.get('weight_hint')} | {b.get('entry')} → {b.get('exit')}"
            )
            if b.get("etf_quality_cn"):
                lines.append(f"  - 映射: {b.get('etf_quality_cn')} {b.get('etf_note') or ''}".rstrip())
        lines += ["", "### 持有", ""]
        holds = flex.get("hold_list") or []
        if not holds:
            lines.append("- （无）")
        for b in holds:
            lines.append(f"- **持有** {b.get('instrument_display') or b.get('name')} | 剩 {b.get('days_remaining', '—')} 日")
        lines += ["", "### 平仓 CLOSE", ""]
        sells = flex.get("close_list") or flex.get("sell_list") or []
        if not sells:
            lines.append("- （无）")
        for b in sells:
            lines.append(f"- **平仓** {b.get('instrument_display') or b.get('name')}")
        lines += ["", "### 回避 AVOID（条件）", ""]
        avoids = flex.get("avoid_list") or []
        if not avoids:
            lines.append("- （无）")
        for b in avoids:
            lines.append(
                f"- **回避** {b.get('instrument_display') or b.get('name')} — {b.get('condition_cn') or b.get('why')}"
            )
        risk = flex.get("risk_dashboard") or {}
        if risk:
            lines += [
                "",
                "### 风险仪表",
                "",
                f"- β≈{risk.get('estimated_beta')} · {risk.get('estimated_daily_vol_cn')} · 暴露 {risk.get('total_exposure')}",
                f"- {risk.get('correlation_note')}",
                f"- {risk.get('circuit_breaker_cn')}",
            ]
        lines.append("")

    if p["primary_stage"].get("notes"):
        for n in p["primary_stage"]["notes"]:
            lines.append(f"- 注: {n}")
        lines.append("")

    lines += ["## 可执行清单（按优先级）", ""]
    for i, ins in enumerate(p.get("actionable_instructions") or [], 1):
        title = ins.get("instrument_display") or ins.get("instrument") or ins.get("name")
        lines.append(
            f"{i}. **[{ins.get('priority')}] {ins.get('side')}** `{title}`  "
            f"| 触发: {ins.get('trigger')} | 进: {ins.get('entry')} | 出: {ins.get('exit')}"
        )
        if ins.get("etf_code"):
            lines.append(f"   - ETF: **{ins.get('etf_code')}** {ins.get('etf_name')}（{ins.get('etf_quality_cn')}）")
        if ins.get("why"):
            lines.append(f"   - 依据: {ins['why']}")
        if ins.get("win_rate") is not None:
            lines.append(f"   - 历史胜率≈{_pct(ins.get('win_rate'))} n={ins.get('n', '--')}")
        lines.append(f"   - {ins.get('disclaimer', '')}")
        lines.append("")

    lines += ["## 全阶段规则目录（静态）", ""]
    for s in p.get("all_stage_catalog") or []:
        lines.append(f"### {s['name_cn']} (`{s['stage_id']}`)")
        lines.append(f"- 识别: `{s.get('detect')}`")
        lines.append(f"- 指数: {s.get('csi300_action')}")
        lines.append(f"- 超配: {', '.join(s.get('long') or []) or '—'}")
        lines.append(f"- 低配: {', '.join(s.get('short') or []) or '—'}")
        lines.append("")

    lines += [
        "## 核心指数规则（最高优先级）",
        "",
        "| 项目 | 规则 |",
        "|------|------|",
        "| 买入 | 60 ≤ RT < 80 且 沪深300 60日回撤 ≤ -5% |",
        "| 卖出 | 持有 5 个交易日 |",
        "| 执行 | T 收盘信号 → T+1 开盘 |",
        "| 样本 | n=49，胜率63.3%，年化~10.3%，OOS胜率63.6% |",
        "",
        "## 重要限制",
        "",
        "1. 板块指令是 **相对沪深300超额** 的研究偏好，不是官方资金流。",
        "2. 小样本阶段（恐慌、n<30）只允许观察仓。",
        "3. 同一时间主策略最多 1 笔；持仓中忽略重复信号。",
        "4. 非投资建议。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
