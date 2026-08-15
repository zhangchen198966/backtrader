# bt-lab 开发指南

> 适用版本：commit `0cdea0f`（2026-08-15）· 测试基线 68/68 全绿
> 本文档面向后续迭代开发者，覆盖架构、前后端实现、测试体系、踩坑记录与常见迭代操作。

---

## 1. 项目概览

bt-lab 是 backtrader 的 Web 可视化回测台：浏览器配置数据/策略/费用 → 后端子进程跑回测或参数优化 → 页内展示绩效卡片、ECharts 交互图表、交易明细。

核心特性：亮暗主题、ECharts 本地化图表（Y 轴随缩放自适应）、57 条术语即点即释、Python 语法高亮编辑器、多数据对比回测、参数网格优化（热力图/柱状图）、在线数据拉取（AkShare 新浪源 + yfinance，中文名搜索）、历史任务管理、独立日志坞。

```
浏览器 (localhost:8600)
   │ fetch / JSON / 1s 轮询
   ▼
FastAPI (webapp/server.py)  ── 任务队列(串行) + 子进程执行
   │ python -m webapp.runner <task_dir>   (cwd=仓库根)
   ▼
runner.py ── 装配 cerebro → run → 产出 ECharts 数据
   │ 写 webapp/tasks/<id>/{status, result.json, progress.jsonl, error.txt}
   ▼
前端轮询读取 → 渲染（echarts.min.js + bt-lab.js 本地化，无 CDN）
```

**关键设计决策**

| 决策 | 原因 |
|---|---|
| 每任务一个子进程 | 隔离用户代码崩溃/死循环（可 kill）；隔离全局状态；不阻塞服务 |
| 任务目录即通信/持久化 | 服务重启不丢历史；前端可回放任意历史任务 |
| 串行任务队列 | 本地单用户；避免 CPU 争抢与并发写冲突 |
| ECharts 完全本地化 | 离线可用；不受 CDN 网络影响 |
| 前端纯函数抽到 bt-lab.js（UMD） | node 直接 require 做单元测试 |
| 图表数据由后端预计算 | runner 产出 dates/candles/volume/trades/equity/indicators，前端只管渲染 |

## 2. 快速开始

```bash
cd backtrader                      # 仓库根
.venv/bin/python -m uvicorn webapp.server:app --port 8600   # 启动服务
.venv/bin/python -m pytest webapp/tests -v                  # 全量测试（68 项）
node webapp/static/tests/run_js_tests.mjs                   # 仅前端纯函数单测
```

依赖都在 `.venv`：fastapi、uvicorn、python-multipart、akshare、yfinance、pytest、httpx、playwright（首次需 `playwright install chromium`）。backtrader 本体零依赖，源码直接 import。

## 3. 目录结构

```
webapp/
├── server.py        # FastAPI + TaskManager（队列/子进程/kill/超时30min）
├── runner.py        # 回测执行器：backtest / optimize 两种模式
├── datasource.py    # 在线数据源：抓取/清洗/落盘 + 名称清单缓存 + 搜索
├── datainspect.py   # CSV 结构探测（分隔符/表头/日期格式/时间列）
├── templates.py     # 4 个内置策略模板（源码+参数元数据）
├── glossary.py      # 57 条术语表（/api/terms）
├── static/
│   ├── index.html   # 单页应用（CSS/主逻辑内联）
│   ├── bt-lab.js    # 前端纯函数库（UMD，node 可测）
│   ├── vendor/echarts.min.js
│   └── tests/run_js_tests.mjs
├── tests/           # pytest 套件（conftest 含 8601 测试服务 fixture）
├── uploads/         # 用户上传/在线拉取的 CSV（gitignore）
├── cache/           # 股票名称清单缓存（gitignore，7天TTL）
└── tasks/<id>/      # 任务目录（gitignore）
```

## 4. 后端详解

### 4.1 API 契约（server.py）

| 接口 | 方法 | 说明 |
|---|---|---|
| `/` | GET | index.html |
| `/api/datas` | GET | {builtin:[], uploads:[]}（含行数/日期范围探测） |
| `/api/datas/inspect` | POST | {path} → 探测结果 |
| `/api/datas/upload` | POST | multipart → 存 uploads/ → 探测 |
| `/api/datas/providers` | GET | 在线数据源列表 |
| `/api/datas/search` | GET | ?q=&provider= 代码/中文名搜索 |
| `/api/datas/fetch` | POST | {provider,symbol,start,end} → 拉取落盘+探测 |
| `/api/datas` | DELETE | ?path= 删除上传文件（**仅限 uploads/**，realpath 校验） |
| `/api/strategy/templates` | GET | 模板列表 |
| `/api/terms` | GET | 术语表 |
| `/api/run` | POST | 提交任务 → {task_id}；path 支持 str 或 list（多数据） |
| `/api/task/{id}` | GET | 状态+结果+优化进度；queued/running/done/error/killed |
| `/api/task/{id}/kill` | POST | 终止子进程 |
| `/api/tasks` | GET | 最近 20 条（含策略/参数/结果摘要，历史面板用） |
| `/api/task/{id}` | DELETE | 删除历史任务（运行中拒删） |

### 4.2 TaskManager（server.py）

- `submit()`：生成任务目录 + `request.json` + status=queued，入队
- worker 线程：取队首 → `Popen([sys.executable, '-m', 'webapp.runner', task_dir], cwd=REPO_ROOT)` → `wait(timeout=30min)` 超时 kill
- kill：terminate→5s→kill；已 kill 的排队任务执行前跳过

### 4.3 runner.py 要点

- **策略装配**（最重要的一处坑）：`exec` 用户代码前必须创建真实模块并注册 `sys.modules`，backtrader 实例化策略时会查 `sys.modules[cls.__module__]`（metabase.py），伪造 `__name__` 会 KeyError。模块名带时间戳防冲突。
- 参数覆写用"子类 + params=dict"：注意类体内 `params = dict(params)` 会 NameError（类作用域闭包陷阱），必须先赋中间变量 `overrides`。
- 图表数据（`collect_chart_data`）：dates/candles[[o,c,l,h]]/volume/买卖点标记（notify_order Completed 时收集）/权益曲线（wrapper 的 next 里逐 bar 记录 broker.getvalue）/指标线（`strat.getindicators()`，嵌套指标无 _name 用 类名+序号 兜底，NaN→null）。
- 交易明细（`collect_trades`）：**已平仓的 Trade.size 会归零、price 变均价**，必须开 `Cerebro(tradehistory=True)` 从 `trade.history[0]/[-1]` 取开仓量与两腿成交价；持仓 bar 数是 `trade.barlen`。
- 多数据：`data.path` 为 list 时逐数据独立跑，产出 `runs[]` + `comparison[]`（归一化权益起点=100）；上限 6 个。
- 优化：`optreturn=True` + `maxcpus=1`（顺序执行进度才有意义），`cerebro.optcbs` 回调逐组合写 `progress.jsonl`。

### 4.4 datasource.py 要点

- 源选择：**A股个股/指数用新浪源**（`stock_zh_a_daily`/`stock_zh_index_daily`）。东财接口对 python 的 TLS 指纹做了拦截（requests 直连被断、curl 却通），勿改回 `stock_zh_a_hist`。
- 个股代码自动加市场前缀：6/9 开头→sh，0/3 开头→sz。
- 统一清洗 `normalize_ohlc_rows`：列映射、日期多形态归一（datetime/date/str/20240102/斜杠）、剔缺失行、同日去重、升序、volume 缺省 0。
- 名称清单：`stock_info_a_code_name()` 全量约 37 秒，**必须走磁盘缓存**（cache/stock_list.json，7 天 TTL，5543 只）。
- 文件名含股票中文名（`_safe_name` 清洗空白与非法字符）。
- yfinance：国内网络常需代理且 Yahoo 有频率限制（429），错误信息已做引导；无中文名库不提供搜索。

## 5. 前端详解

### 5.1 index.html 分区

顶栏（主题切换 + 服务徽章）→ 左配置（数据[分组折叠+多选+删除+在线获取] / 策略[模板|自定义] / 参数[单值|网格] / 资金费用）→ 右结果（状态条→卡片→对比→年度图→K线图→明细表）→ 底部固定停靠区（**运行日志 | 历史任务** 双 Tab）。

### 5.2 bt-lab.js（纯函数，node 可测）

- 主题：`THEME_COLORS` 双色板 / `normalizeTheme` / `getInitialTheme(storage)` / `saveTheme` / `toggleTheme` / `applyTheme(doc,theme)`
- 高亮：`tokenizePython`（注释/三引号字符串/数字/关键字/内置名/运算符）→ `highlightPython`（转义+着色 span）
- 术语：`wrapTerms(text, glossary)` —— **长词优先**防嵌套，占位符两段替换
- 图表 option：`buildCandleOption`（三 grid：K线+指标+买卖点 / 成交量 / 权益）、`buildOptChartOption`（恰 2 个变化参数→热力图，否则柱状图）、`buildCompareOption`（time 轴归一化权益）
- Y 轴自适应：`computeVisibleRange(chartData, pct0, pct1)` + `attachAutoScale(chart, chartData)`（监听 datazoom；**主轴只按 K 线 low/high 算**，RSI 等异量纲指标不参与，否则压扁价格轴）

### 5.3 关键机制

- **主题**：`:root`（暗）+ `html[data-theme="light"]`（亮）双套 CSS 变量；高亮配色 `--tk-*` 也走变量；切换时 dispose 所有图表重渲染（`redrawCharts`）。
- **编辑器**：`pre#hl`（着色层）叠 `textarea#editor`（透明文字+可见光标），input/scroll 同步，Tab 插 4 空格。
- **术语系统**：动态文本用 `T(k)`（=wrapTerms）；静态标签加 `class="tlabel"`，init 时 `termifyStatic()` 统一处理；模板描述/数据源 hint 用 `wrapTerms` 注入。点击 `.term` → `#tipbox` 浮层（边界翻转防溢出）。
- **轮询**：1s 间隔查 `/api/task/{id}`；优化任务读 progress 显示进度条。
- **默认勾选**：仅默认勾第一个可用自带样例；上传区一律不勾；上传/拉取后只勾新文件。

## 6. 测试体系（68 项）

| 层 | 文件 | 数量 | 说明 |
|---|---|---|---|
| JS 单元 | static/tests/run_js_tests.mjs | 22 | 主题/高亮/术语/option 构建/Y轴范围，node 直跑 |
| API+runner | test_api.py / test_runner_data.py / test_datasource.py | 28 | TestClient + 子进程真跑 runner；网络相关全部 mock（fetch 用 monkeypatch FETCHERS） |
| E2E | test_e2e.py | 18 | Playwright Chromium 真实浏览器+真实回测；conftest 起 8601 测试服务并**预置迷你股票清单缓存**避免触网 |

**E2E 约定**：`wait_done()` 用 `.card:visible, #optChart:visible, #errorBox:visible`（`:visible` 防锚定隐藏元素）；折叠分组内的 checkbox 操作前先 `open_data_groups(page)`；对折叠内容的等待用 `state='attached'`；删除断言按 data-id 而非行数（列表有上限会补位）；dialog 用 `page.on('dialog', accept)`。

## 7. 踩坑记录（迭代前必读）

1. **backtrader 动态策略必须注册真实模块**：`sys.modules[cls.__module__]` 会被查（metabase.py:244）。exec 前建 `types.ModuleType` 并注册，用完可不管（进程即用即弃）。
2. **已平仓 Trade 的 size=0**：明细要从 `tradehistory` 的首/末事件取；持仓天数属性叫 `barlen`（不是 barsheld）。
3. **matplotlib 在 macOS 无头环境卡死**（历史教训，现已移除该依赖）：backtrader 会强制切 MacOSX 后端且 `plt.show()` 阻塞。若未来 reintroduce：Agg → import backtrader.plot → 再 use('Agg')。
4. **东财接口 TLS 指纹封锁**：python requests 直连 `RemoteDisconnected`，curl 正常。不是代理、不是 UA，是 TLS 栈指纹。用新浪源。
5. **macOS 系统代理坑**：httpx `trust_env=True`（默认）会走系统代理，对 `127.0.0.1` 返回 502。测试代码一律 `trust_env=False`；Playwright 启动加 `--no-proxy-server`。requests 不支持 `no_proxy='*'`。
6. **ECharts dataZoom 不自动重算 Y 轴**：必须监听 datazoom 自己算可见区间 min/max（见 attachAutoScale）。
7. **CSS 伪元素造成的"多余数据"**：零轴上的 `::after content:'0%'` 被用户当成多出的数据点。教训：**UI 测试要审计渲染后的文字内容（innerText 逐行正则）而不只是几何位置**。
8. **Playwright wait_for_selector 多元素时锚定第一个**：折叠/隐藏元素会让可见等待永远超时。用 `:visible` 伪类或 `state='attached'`。
9. **测试服务端口残留**：8601 被上轮残留 uvicorn 占用会让整批 E2E 报 `Address already in use`。跑套件前 `lsof -ti :8601 | xargs kill -9`。
10. **类体作用域陷阱**：`class C: params = dict(params)` 中右侧 params 解析到类命名空间 → NameError。先 `overrides = dict(params)`。
11. **python-multipart**：FastAPI 文件上传（UploadFile）必须装，否则启动即崩。
12. **Playwright 浏览器单独装**：`pip install playwright` 后还需 `playwright install chromium`（约 95MB）。
13. **中文 URL 参数**：curl 测中文 query 必须 `--data-urlencode -G`，裸中文会 `Invalid HTTP request`（浏览器 fetch 自动编码，不受影响）。
14. **任务历史列表上限 20**：相关断言用任务 id 是否消失，不要断言行数减一。
15. **静态文件即时生效，Python 改动需重启**：index.html/bt-lab.js 改完刷新页面即可；server.py/runner.py 改动需重启 uvicorn。

## 8. 常见迭代操作

**加一个策略模板**：`templates.py` 的 TEMPLATES 加一项（id/name/desc/params 元数据/code）。code 里 `params = dict(...)` 的键必须与元数据 name 一致；int 参数标 `'int': True`（runner 会做 float→int 归一）。无后端改动，自动出现在下拉里。

**加一个在线数据源**：`datasource.py` 加 fetcher（返回 `normalize_ohlc_rows` 的标准行）→ 注册进 FETCHERS + PROVIDERS（含 symbol_re 校验与提示文案）→（如需中文名搜索）`lookup_symbol_name` 加分支。

**加术语**：`glossary.py` 加词条即可，前后端自动生效（前端经 /api/terms 拉取）。注意长词优先机制：新词条若是现有词的子串，会优先匹配长词。

**加分析器/绩效指标**：runner.py `run_one_backtest` 里 `cerebro.addanalyzer(...)` → `summarize()` 提取 → 前端 `renderBacktest` 加卡片（label 走 `T()` 自动获得术语 tips）。

**改图表**：option 构建全在 bt-lab.js 纯函数里，配套 node 单测同步改；改完跑 `node webapp/static/tests/run_js_tests.mjs`。

**前端 UI**：改 index.html 后至少跑 E2E（选择器变化会体现在测试里）；注意任何新交互元素在折叠区内时的测试等待语义（见 §6）。

## 9. 已知限制与后续方向

- 优化不支持自定义代码的参数网格（自定义代码无参数元数据），仅模板可网格。
- yfinance 受 Yahoo 频控/代理影响，时好时坏；可考虑加 Tushare（需 token）或 baostock 作为国内备用源。
- 对比回测上限 6 个数据；多策略同数据对比未做。
- 优化结果图在 >2 个网格参数时退化为柱状图（热力图仅支持二维）。
- 任务队列串行，无并发（单用户够用；多用户需引入任务优先级/并发池）。
- 安全模型：本工具面向本地单用户，自定义代码执行无沙箱，**切勿暴露到公网**。
