# Backtrader 回测实验室（bt-lab）设计文档

日期：2026-08-15
状态：已确认（用户授权自主决策）

## 1. 目标

为 backtrader（本仓库源码，v1.9.78.123）构建一个本地 Web 可视化操作台，替代命令行：
配置数据/策略/资金费用 → 页面上一键运行回测或参数优化 → 绩效报告、原生图表（SVG）、
交易明细直接在页面内展示。

非目标（v1 明确不做）：实盘交易接入（IB/Oanda/VC）、多用户/鉴权、数据持久化到数据库、
在线数据源下载（Yahoo/Quandl）。

## 2. 总体架构

```
浏览器 (localhost:8600)
   │  fetch / JSON / 轮询
   ▼
FastAPI 服务 (webapp/server.py)        —— 参数校验、任务调度、静态页面
   │  subprocess: .venv python runner.py <task_dir>
   ▼
回测执行器 (webapp/runner.py)          —— 装配 cerebro → run → 产出结果文件
   │  写入 webapp/tasks/<id>/{status,result.json,chart.svg,trades.csv}
   ▼
API 轮询任务状态 → 前端渲染
```

关键决策：
- 每个任务跑在独立子进程：隔离用户代码崩溃/死循环（可 kill）、隔离 matplotlib
  后端切换的全局副作用、优化任务不阻塞服务。
- 任务目录即持久化：request.json/status/result.json/chart.svg；服务重启后历史可回看。
- 同时只跑 1 个任务，后来者排队（本地单用户）。
- 每次运行使用独立临时 CWD 无必要；进程间只通过任务目录文件通信。

## 3. 后端 API

| 接口 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 单页应用 index.html |
| `/api/datas` | GET | {builtin:[…样例文件], uploads:[…已上传]} |
| `/api/datas/inspect` | POST | {path} → 探测 dtformat/列序/行数/起止日期 |
| `/api/datas/upload` | POST | multipart CSV → 存 uploads/ → 返回 inspect 结果 |
| `/api/strategy/templates` | GET | 模板列表：id/名称/描述/源码/参数元数据 |
| `/api/run` | POST | 提交任务（mode=backtest|optimize），返回 {task_id}；重复提交返回排队中任务 |
| `/api/task/{id}` | GET | {status: queued|running|done|error|killed, detail…}；done 时含 result.json+chart.svg |
| `/api/task/{id}/kill` | POST | 终止运行中的子进程 |

运行请求 JSON 契约（backtest 模式）：
```json
{
  "mode": "backtest",
  "data": {"path": "datas/2006-day-001.txt", "dtformat": "auto", "columns": null},
  "strategy": {"source": "template", "template_id": "sma_rsi_atr", "code": null,
                "params": {"fast": 10, "slow": 30}},
  "broker": {"cash": 100000, "commission": 0.001, "slippage": 0.0005},
  "sizer": {"type": "percent", "percents": 90},
  "plot": {"style": "candle", "volume": true}
}
```
optimize 模式：strategy.params 里每个值是 {"start":…,"end":…,"step":…} 网格定义。

## 4. 执行器（webapp/runner.py）

- 数据：GenericCSVData；dtformat=auto 时按 [%Y-%m-%d, %Y-%m-%d %H:%M:%S,
  %Y/%m/%d, %d/%m/%Y, %m/%d/%Y] 顺序尝试解析第 2 行；列序默认标准 OHLCV。
- 策略：
  - template/自定义统一走"源码 + 参数"。模板源码内置 4 个：sma_cross（双均线）、
    sma_rsi_atr（双均线+RSI 过滤+ATR 跟踪止损）、bbands_reversal（布林带反转）、
    macd_signal（MACD 信号）。每个模板带参数元数据（名称/默认/最小/最大/步长/整数标记）。
  - 自定义代码 exec(code, ns)，在 ns 中找 bt.Strategy 的非抽象子类；多个时取第一个。
    异常捕获 → traceback 全文写入 status，前端红框显示。
- 优化：cerebro.optstrategy(optreturn=True, maxcpus=1)，每组结果 append 进度文件
  progress.jsonl（前端进度条 = 已完成组数/总组数）；完成后汇总排序。
- 图表：Agg 后端三行修复固化（use('Agg') → import backtrader.plot → use('Agg')）；
  cerebro.plot(style, volume) → figs[0][0] savefig SVG 字符串 → chart.svg。
- 绩效：Sharpe/Sharpe_A/DrawDown/Returns/SQN/TradeAnalyzer/TimeReturn(年度)/
  买入持有对比 + 逐笔交易明细（从策略通知层面收集：日期/方向/价/量/佣金/平仓净利）。
- 安全边界：仅本地使用，代码执行不做沙箱；子进程 30 分钟硬超时自动 kill。

## 5. 前端（webapp/static/index.html，单文件零构建零 CDN）

布局：
- 顶栏：标题 + Tab（回测运行/参数优化）+ 服务状态点。
- 回测 Tab：左配置右结果。配置区折叠分组：数据（下拉+上传+高级列序）、策略
  （模板选择/参数表单自动渲染 ↔ 自定义代码编辑器 textarea 等宽字体，可"从模板载入"）、
  资金与费用、Sizer。结果区：状态条 → 绩效卡片网格（8 张）→ SVG 图表容器（横向滚动）→
  年度收益 → 交易明细表。
- 优化 Tab：复用数据/策略配置；参数区变为起/止/步长网格输入；结果为可排序表格，
  点击行 → 参数回填回测 Tab 并切换。
- 底部任务历史条：最近 10 个任务（时间+状态+摘要），点击回看。

错误处理：语法/运行时错误 traceback 红框；任务运行中可终止；重复提交提示排队。

## 6. 目录结构（新增，不侵入框架源码）

```
webapp/
├── server.py        # FastAPI + 任务调度（串行队列）
├── runner.py        # 子进程执行器：backtest/optimize 两种模式
├── templates.py     # 内置策略模板与参数元数据
├── datainspect.py   # CSV 探测
├── static/index.html
├── uploads/         # 上传 CSV
└── tasks/<id>/      # request.json status result.json chart.svg progress.jsonl
```
依赖：.venv 中新增 fastapi、uvicorn；backtrader 继续源码直接 import。

## 7. 测试

浏览器端到端验证三条主路径：
1. 模板策略全流程：选数据 → 选模板 → 运行 → 绩效卡片/SVG 图/明细齐全。
2. 错误体验：提交语法错误自定义代码 → traceback 红框、配置保留。
3. 优化闭环：小网格优化 → 进度条 → 排序表格 → 点击行参数回填 → 再运行回测。
API 层用 curl 验证 JSON 契约（inspect/templates/run/task）。
