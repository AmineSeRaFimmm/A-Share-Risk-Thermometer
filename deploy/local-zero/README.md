# 真·0 元本机生产（自用 TestFlight）

**不依赖 GitHub。** 数据在你的 Mac 上算，经 **Cloudflare Tunnel（免费）** 暴露 HTTPS，TestFlight 安装包只读该 URL。

```
交易日盘中 每10分钟 → update_realtime_avix  → nowcast 温度
交易日盘后 15:20–18:30 窗口内 → update_daily + build_site_data → 正式收盘
app_server :8787 常驻
cloudflared → https://你的域名 → :8787
TestFlight App → EXPO_PUBLIC_WEB_URL=https://你的域名
```

电脑需：**开机、联网、能跑 Python 流水线**。关机则 App 读不到新数据。

---

## 一、本机数据面（先跑通）

```bash
cd /path/to/a-share-risk-thermometer

# 依赖（一次性）
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 手动试跑
bash deploy/local-zero/run_intraday.sh   # 非盘中会 skip
bash deploy/local-zero/run_eod.sh        # 非盘后窗口会 skip
bash deploy/local-zero/run_app_server.sh # 另开终端，保持运行
curl -s http://127.0.0.1:8787/api/status | head
```

### 安装 macOS 定时任务

```bash
bash deploy/local-zero/install_launchd.sh
```

| Agent | 作用 |
|-------|------|
| `com.ashare.rt.app-server` | 常驻 UI+JSON+API |
| `com.ashare.rt.intraday` | 每 10 分钟；脚本内判断上海盘中 |
| `com.ashare.rt.eod` | 每 20 分钟；仅 15:20–18:30 上海时间跑正式日更 |
| `com.ashare.rt.tunnel` | 可选，配好 cloudflared 后安装 |

日志：`deploy/local-zero/logs/`

卸载：

```bash
bash deploy/local-zero/uninstall_launchd.sh
```

**建议 Mac 时区设为 `Asia/Shanghai`。** 窗口判断用上海时间；launchd 触发间隔用本机时钟。

---

## 二、Cloudflare Tunnel（免费公网 HTTPS）

### 有自己的域名（推荐 TestFlight）

```bash
brew install cloudflared
cloudflared tunnel login
cloudflared tunnel create ashare-rt
# 记下 UUID，凭证在 ~/.cloudflared/<UUID>.json

# DNS（域名需已接入 Cloudflare）
cloudflared tunnel route dns ashare-rt rt.example.com

cp deploy/local-zero/cloudflared/config.example.yml \
   deploy/local-zero/cloudflared/config.yml
# 编辑 tunnel / credentials-file / hostname

cloudflared tunnel --config deploy/local-zero/cloudflared/config.yml run
# 或重新 install_launchd.sh 以加载 tunnel agent
```

验证：

```bash
curl -s https://rt.example.com/api/status
```

### 临时试通（URL 会变，**不能**稳定进 TestFlight 包）

```bash
cloudflared tunnel --url http://127.0.0.1:8787
```

---

## 三、App 打进 TestFlight

### 1. 准备 Apple 账号

- Apple Developer 付费账号（TestFlight **不是** 0 元，账号年费是必须的）
- App Store Connect 建 App，Bundle ID：`com.ashare.riskthermometer`

「真·0 元」指的是 **服务器/托管 0 元**；上架 TestFlight 仍要开发者账号。

### 2. 安装 EAS

```bash
cd mobile
npm install
npm install -g eas-cli
eas login
eas build:configure
```

### 3. 构建时写入公网数据 URL

```bash
export EXPO_PUBLIC_WEB_URL="https://rt.example.com"
export EXPO_PUBLIC_ALLOW_GITHUB=0

eas build -p ios --profile production
```

`app.config.js` 会把 `EXPO_PUBLIC_WEB_URL` 打进包。  
**换域名必须重新 build**，不能只改电脑配置。

### 4. 提交 TestFlight

```bash
eas submit -p ios --latest
```

或在 [expo.dev](https://expo.dev) 下载 `.ipa` 用 Transporter 上传。

### 5. App 行为预期

| 时段 | 数据 |
|------|------|
| 交易日盘中（Mac 在跑） | `/data/latest.json` 多为 nowcast |
| 盘后日更成功后 | 切到正式收盘 RT |
| Mac 关机 | App 仍可能打开**缓存页**，但无新更新 |

页内可点 **实时 / 日更**（打到本机 API，经 Tunnel）。

---

## 四、日常自检

```bash
# 服务是否活着
curl -s http://127.0.0.1:8787/api/health
curl -s https://rt.example.com/api/status | python3 -m json.tool | head -40

# 日志
tail -50 deploy/local-zero/logs/pipeline.log
tail -50 deploy/local-zero/logs/intraday.log
tail -50 deploy/local-zero/logs/eod.log

# launchd
launchctl print gui/$(id -u)/com.ashare.rt.app-server | head -20
```

---

## 五、限制（心里有数）

| 项 | 说明 |
|----|------|
| 电脑关机 | 无更新、Tunnel 断 |
| A 股节假日 | 脚本只跳过周末，**不识别法定假日**（自用可接受） |
| 免费源 | 盘中/盘后可能偶发失败，看 log 重跑 |
| Cloudflare | 免费 Tunnel 够自用；域名可买最便宜的（非必须 0 元） |
| 无域名 | 无法稳定 TestFlight，只能临时 trycloudflare URL 自测 |

---

## 六、最小路径 checklist

1. [ ] `pip install -r requirements.txt`  
2. [ ] `bash deploy/local-zero/install_launchd.sh`  
3. [ ] `curl localhost:8787/api/status` 有 RT  
4. [ ] Cloudflare Tunnel → 固定 HTTPS  
5. [ ] `EXPO_PUBLIC_WEB_URL=https://… eas build -p ios`  
6. [ ] TestFlight 安装，交易日看盘中/盘后口径  
