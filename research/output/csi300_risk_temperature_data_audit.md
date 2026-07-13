# 沪深300风险温度数据核查

- risk_components.csv 是否存在: True
- sh000300.csv 是否存在: True
- risk_components 日期: 2019-12-23 至 2026-07-02
- sh000300 日期: 2002-01-04 至 2026-07-06
- risk_components 最新日期: 2026-07-02
- sh000300 最新日期: 2026-07-06
- 有效 inner join 样本数: 1579
- 有效对齐区间: 2019-12-23 至 2026-07-02
- risk_temperature 缺失值: 0
- sh000300 open 缺失值: 0
- sh000300 close 缺失值: 0
- risk_components 重复日期: 0
- sh000300 重复日期: 0
- 日期错位数量（对称差）: 4363
- risk_temperature 晚于指数行情: False
- 指数行情晚于 risk_temperature: True
- 是否存在未来数据风险: False
- 回测执行口径: T+1 open

## quality 分布

- WARN_BREADTH_PROXY: 1177
- WARN_BREADTH_PROXY|WARN_NOT_BRACKET_30D: 276
- WARN_BREADTH_PROXY|WARN_QVIX_MISSING: 96
- WARN_BREADTH_PROXY|WARN_NOT_BRACKET_30D|WARN_QVIX_MISSING: 27
- LOW_NO_CHAIN: 2
- BAD_NO_TERM: 1

## model_confidence 分布

- 46.0: 3
- 84.0: 123
- 96.0: 1453
