# 量化交易系统

基于币安合约的多用户量化交易平台。每个用户独立注册、独立配置币安 API 密钥、独立运行策略机器人，数据和资金完全隔离。

## 核心功能

### 多用户体系
- 账户注册/登录/密码重置，PBKDF2 加盐密码存储
- 会话 Token 管理，7 天有效期自动过期
- 每个用户独立配置自己的币安 API Key、Secret Key、代理地址
- 数据完全隔离：`user_data/<用户名>/` 下独立存储交易记录、策略、自选

### 策略交易
- **TEMA 策略**（OCC v8.13）：跨周期均线交叉 + 延迟确认趋势跟随
- **VOLTY 策略**（Volty Expan Close）：ATR 通道波动性突破，Bar 收盘确认
- 多币种并行交易，每个币种独立策略参数
- 百分比仓位管理 + 杠杆 + 止损 + 止盈
- 支持做多/做空/双向交易
- 支持合约双向持仓模式（Hedge Mode）

### 实时看板
- 实时行情（BTC、ETH 等合约价格和涨跌幅）
- 持仓展示：未实现盈亏、资金费率、强平价、手续费预估
- 累计盈亏曲线图
- 全市场行情排名

### 数据分析
- 胜率、盈亏比、最佳/最差交易
- 各交易对偏好统计
- 系统运行日志实时查看

### 安全设计
- PBKDF2 + 随机盐密码哈希，兼容旧格式自动升级
- 用户密钥存于 `user_data/<用户名>/.env`，不提交 Git
- API 访问 Token 鉴权，无登录返回 401
- 管理员密码从环境变量读取，不硬编码

## 系统架构

```
用户浏览器
    │  http://服务器:8000
    ▼
web_ui.py (HTTP Server)  ←→  accounts.db (用户账户)
    │
    ├── public_binance (共享，公开行情)
    ├── private_binance (每用户独立，API Key)
    │
    ├── strategy_bot.py --user <用户名> (子进程)
    │
    └── user_data/<用户名>/
        ├── .env            # 币安 API Key / Secret / 代理
        ├── trades.json     # 交易记录
        ├── strategies.json # 策略配置
        └── watchlist.json  # 自选列表
```

## 快速开始

### 部署

```bash
git clone git@github.com:GGhuazi/trading-system.git
cd trading-system
pip install ccxt pandas numpy python-dotenv fastapi uvicorn
cp .env.example .env
# 编辑 .env，设置 ADMIN_PASSWORD 和 PROXY_URL
python web_ui.py
```

### 使用

1. 打开浏览器访问 `http://服务器IP:8000`
2. 点击「立即注册」创建账号
3. 进入「设置中心」，填入币安 API Key / Secret / 代理地址
4. 去「策略配置」设置交易参数
5. 点击「启动策略」开始自动交易

## 页面功能

| 页面 | 功能 |
|------|------|
| 仪表盘 | 实时持仓、余额、盈亏曲线、币安行情 |
| 策略配置 | 策略参数设置、启动/停止机器人 |
| 数据监控 | 全市场行情排名 |
| 资产分析 | 胜率、盈亏比、交易偏好 |
| 系统日志 | 策略运行实时日志 |
| 设置中心 | 币安 API 密钥和代理配置 |

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ADMIN_PASSWORD` | 管理员密码 | 无 |
| `PROXY_URL` | 默认代理地址 | 空 |
| `SERVER_HOST` | 服务器 IP | localhost |
| `UI_PORT` | Web 端口 | 8000 |

## 项目结构

```
trading-system/
├── web_ui.py           # Web 主程序（HTTP Server + API + HTML）
├── strategy_bot.py     # 策略机器人（独立子进程运行）
├── bot_server.py       # TradingView Webhook 桥接器
├── db.py               # 用户数据库模块（SQLite）
├── chart.js            # Chart.js 图表库（本地加载）
├── .env.example        # 环境变量模板
├── MANUAL.md           # 详细使用手册
├── accounts.db         # 用户数据库（本地，不提交 Git）
└── user_data/          # 用户数据目录（本地，不提交 Git）
    └── <用户名>/
        ├── .env
        ├── trades.json
        ├── strategies.json
        └── watchlist.json
```

## 常见问题

**Q: 币安显示离线？**  
检查代理 URL 是否正确，VPN 是否开启。设置中心的代理格式：`http://127.0.0.1:7897`

**Q: 怎么添加多个交易币种？**  
策略配置页 → 点击「添加策略」，每个币种独立配置参数。

**Q: 如何修改密码？**  
目前不支持自助修改密码（下个版本计划中），可联系管理员。

**Q: 预设账户？**  
首次启动自动创建 `A8` / `13590703676`。
