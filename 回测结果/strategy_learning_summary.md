# 40+策略深度学习总结

## 一、策略分类

### 已学习的主策略

| 策略 | 类型 | 核心逻辑 | 对Volty的启发 |
|------|------|---------|-------------|
| Volty EMA50 | ATR突破+趋势 | 裸K线突破+EMA50过滤 | 当前基准 |
| OCC Series | 跨周期MA交叉 | TEMA(8)在高周期上的交叉 | 延迟信号减少假突破 |
| SuperTrend | ATR尾随 | close穿过ATR带=趋势改变 | 动态止损思路 |
| PMax | MA+ATR | MA±3×ATR=通道 | 同SuperTrend |
| Hull Suite | Hull MA | 降噪均线交叉 | 减少滞后 |
| Ichimoku+HULL+MACD | 多确认 | 5条件同时满足 | 过滤严格=信号少 |

### 学到的核心创新

| 来源策略 | 创新技术 | 价值 |
|---------|---------|------|
| **Rob Booker ADX Breakout** | 箱体动态止盈止损 | 🏆 最高价值 |
| **Parabolic SAR** | 加速尾随止损 | 🏆 最高价值 |
| **3Commas Bot** | ATR尾随+摆动高低点 | ⭐ 高价值 |
| **Greedy Strategy** | 每日最大交易数 | ⭐ 实用 |
| **Keltner Channels** | 挂单+自动取消 | ⭐ 防假突破 |
| **Bollinger Bands** | OCA双向订单管理 | 订单管理 |
| **ANN v2** | 神经网络预测 | 不可复现 |

### 技术指标类(22个)核心发现
- Bollinger: 标准差通道 vs ATR通道
- Keltner: ATR通道更适合作突破基准
- ADX Breakout: 箱体宽度动态调整策略参数
- PSAR: 加速因子让利润奔跑
- 其余(MACD/RSI/Stochastic等): 在ETH上单独使用基本无效

### 社区热门(15个)核心发现
- 3Commas: 成熟的机构级退出管理
- Ichimoku: 多时间框架一站式
- Hull Suite: 平滑均线减少噪音
- 其余: 过度拟合或过于简单

## 二、可用于优化Volty的技术

### 1. 动态止盈止损(来自ADX Breakout + PSAR)

```
当前: 固定5% SL + 7% TP
优化: 
  - SL起始=3%, 用PSAR加速因子锁利润
  - 每根盈利K线: AF+=0.02, 新SL=旧SL+AF×(最高价-旧SL)
  - 最大AF=0.2
```

### 2. 箱体动态仓位(来自ADX Breakout)

```
当前: 固定60%×3x
优化:
  - 计算最近20根K线箱体宽度
  - 宽箱体=小仓位, 窄箱体=大仓位
  - 仓位=基础仓位×(中位ATR/当前ATR)
```

### 3. 每日最大交易(来自Greedy Strategy)

```
每日最多5笔开仓, 超过则当日休息
防止震荡市反复摩擦
```

### 4. 止盈7% + 尾随止损(组合)

```
6.5年数据: TP=7%最优 ($70,230)
叠加PSAR尾随: 进一步减少DD
```

## 三、下一步

将这些技术整合进Volty, 跑6.5年回测对比:
1. Volty + TP=7% (已测试)
2. Volty + TP=7% + PSAR尾随
3. Volty + TP=7% + 动态仓位
4. Volty + TP=7% + 全部
