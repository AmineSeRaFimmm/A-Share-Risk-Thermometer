# A股风险温度计 · Expo App

杂志风格移动端壳层。**生产默认读 GitHub Pages**（Actions 盘中/盘后更新），手机可独立使用（TestFlight）。

## 架构（TestFlight 推荐）

```
AKShare 等免费源
    ↓
GitHub Actions（交易日盘中 realtime + 盘后 update_daily）
    ↓
GitHub Pages 静态站 + data/*.json
    ↓
Expo / TestFlight WebView
  EXPO_PUBLIC_WEB_URL=https://amineserafimmm.github.io/A-Share-Risk-Thermometer/
```

- 纯 Pages：**无** `/api/refresh`，页内「实时/日更」按钮会隐藏。  
- 右上角 ↻：重新加载 Pages 上最新文件。  
- 本机 `app_server` 仅开发调试用。

## TestFlight

```bash
cd mobile
npm install
eas login
# 默认已写入 Pages URL（见 eas.json / app.config.js）
eas build -p ios --profile production
eas submit -p ios --latest
```

## 本地开发（可选：本机数据平面）

```bash
# 终端 1
python3 scripts/app_server.py --auto-refresh realtime

# 终端 2
cd mobile
EXPO_PUBLIC_WEB_URL=http://$(ipconfig getifaddr en0):8787 npx expo start --lan
```

有 `/api` 时页内可点「实时 / 日更」触发本机流水线。

## 功能保留清单

- 风险温度 0–100 + 区间着色
- 盘中 nowcast / 正式收盘口径
- 8 因子组件图
- 温度历史 + 区间切换
- Flex 执行台（进取/保守、真实/模拟账本、点买记账）
- RT 研究观察
- S3/S4 策略信号
- AVIX vs QVIX / 沪深300 图表
- 数据健康
- Methodology / Data 页
- 本机 localStorage 账本（WebView 持久化）

## 环境变量

| 变量 | 说明 |
|------|------|
| `EXPO_PUBLIC_WEB_URL` | WebView 加载的仪表盘根 URL |

## 同步前端到 docs

改 `web/` 后：

```bash
# 仓库根目录
rsync -a --exclude 'data' web/ docs/
```

或走既有 `scripts/build_site_data.py` 发布流程。
