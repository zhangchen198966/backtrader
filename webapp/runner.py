# -*- coding: utf-8 -*-
"""bt-lab 回测执行器

以子进程方式运行：`python -m webapp.runner <task_dir>`（cwd=仓库根）
从任务目录读 request.json，执行回测或参数优化，写回：
  status        —— queued|running|done|error|killed
  result.json   —— 绩效结果 + ECharts 图表数据
  progress.jsonl —— 优化进度（optimize 模式，每行一个已完成组合）
  error.txt     —— 异常 traceback

图表数据（chart 字段）供前端 ECharts 渲染，不再依赖 matplotlib：
  dates / candles / volume / trades（买卖点标记）/
  equity（权益曲线）/ indicators（策略指标线）
"""
import json
import math
import os
import sys
import time
import traceback
import types

import backtrader as bt

from webapp.datainspect import inspect_csv
from webapp.templatestore import find_custom
from webapp.templates import get_template

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE, STATUS_ERROR, STATUS_KILLED = \
    'queued', 'running', 'done', 'error', 'killed'


class RequestError(Exception):
    """请求本身的错误（数据/代码/参数不合法），消息直接展示给用户"""


# ---------------------------------------------------------------- 数据

def data_paths(spec):
    """数据配置 → 数据文件路径列表（支持单路径或多路径对比）"""
    raw = spec.get('path')
    if isinstance(raw, list):
        paths = [str(p) for p in raw if p]
    else:
        paths = [str(raw)] if raw else []
    if not paths:
        raise RequestError('未选择数据文件')
    if len(paths) > 6:
        raise RequestError('对比数据最多 6 个')
    return paths


def load_data_feed(spec, path):
    if not os.path.isabs(path):
        path = os.path.join(REPO_ROOT, path)
    if not os.path.isfile(path):
        raise RequestError(f'数据文件不存在: {path}')

    info = inspect_csv(path)
    cols = spec.get('columns') or {}

    dtformat = spec.get('dtformat') or 'auto'
    if dtformat in ('auto', ''):
        if not info['ok']:
            raise RequestError(f'数据自动识别失败: {info["error"]}')
        dtformat = info['dtformat']
        if cols.get('time') is None and info['time_col'] >= 0:
            cols = {'time': info['time_col'], 'open': 2, 'high': 3, 'low': 4,
                    'close': 5, 'volume': 6, 'openinterest': 7}

    kw = dict(dataname=path, dtformat=dtformat,
              separator=info.get('sep') or ',')
    name2param = {'datetime': 'datetime', 'time': 'time', 'open': 'open',
                  'high': 'high', 'low': 'low', 'close': 'close',
                  'volume': 'volume', 'openinterest': 'openinterest'}
    for key, col in cols.items():
        if key in name2param and col is not None and int(col) >= 0:
            kw[name2param[key]] = int(col)

    data = bt.feeds.GenericCSVData(**kw)
    data._name = os.path.splitext(os.path.basename(path))[0]
    return data


# ---------------------------------------------------------------- 策略

def build_strategy_class(spec):
    source = spec.get('source', 'template')
    params = dict(spec.get('params') or {})

    if source == 'template':
        tpl = get_template(spec.get('template_id')) or \
            find_custom(spec.get('template_id'))
        if tpl is not None:
            code = tpl['code']
            param_metas = tpl.get('params') or []
        elif spec.get('code'):
            # 服务端提交时已解析的本地模板（子进程无需读存储文件）
            code = spec['code']
            param_metas = spec.get('resolved_params_meta') or []
        else:
            raise RequestError(f'未知策略模板: {spec.get("template_id")}')
        # 按模板元数据把 int 参数从 float 归一（HTML number 输入可能带来 10.0）
        for meta in param_metas:
            val = params.get(meta['name'])
            if meta.get('int') and isinstance(val, (int, float)):
                params[meta['name']] = int(val)
    else:
        code = spec.get('code') or ''
        if not code.strip():
            raise RequestError('自定义策略代码为空')

    # 注册为真实模块：backtrader 实例化策略时会查 sys.modules[cls.__module__]
    modname = 'btlab_strategy_%d' % int(time.time() * 1000000)
    mod = types.ModuleType(modname)
    mod.bt = bt
    sys.modules[modname] = mod
    try:
        exec(compile(code, '<strategy-code>', 'exec'), mod.__dict__)
    except SyntaxError:
        del sys.modules[modname]
        raise RequestError('策略代码语法错误:\n' + traceback.format_exc())
    except Exception:
        del sys.modules[modname]
        raise RequestError('策略代码执行错误（模块级）:\n' + traceback.format_exc())
    ns = mod.__dict__

    cands = [v for v in ns.values()
             if isinstance(v, type) and issubclass(v, bt.Strategy)
             and v.__module__ == modname
             and v not in (bt.Strategy, bt.SignalStrategy, bt.StrategyBase)]
    if not cands:
        raise RequestError('代码中未找到 bt.Strategy 的子类'
                           '（请定义一个继承 backtrader.Strategy 的类）')

    base = cands[0]

    # 网格字典（optimize 模式）由 cerebro.optstrategy 处理，不做子类覆写
    overrides = {k: v for k, v in params.items()
                 if not isinstance(v, dict)}
    if overrides:

        class ConfiguredStrategy(base):
            params = overrides

        return ConfiguredStrategy
    return base


def make_logged(cls):
    """包装策略：收集运行日志、逐 bar 权益、成交标记（不改变逻辑）"""
    class LoggedStrategy(cls):
        _log = []
        _marks = []   # 买卖点标记 [{date, price, side}]
        _equity = []  # 权益曲线 [{date, value}]

        def notify_order(self, order):
            if order.status == order.Completed:
                side = 'buy' if order.isbuy() else 'sell'
                cn = '买入' if order.isbuy() else '卖出'
                dt = self.data.datetime.date(0).isoformat()
                self._log.append(
                    '[%s] %s成交 价=%.2f 量=%.2f 佣金=%.2f'
                    % (dt, cn, order.executed.price,
                       order.executed.size, order.executed.comm))
                self._marks.append({'date': dt, 'price': order.executed.price,
                                    'side': side})
            elif order.status in (order.Canceled, order.Margin, order.Rejected):
                self._log.append('订单未成交: %s' % order.getstatusname())
            super(LoggedStrategy, self).notify_order(order)

        def notify_trade(self, trade):
            if trade.isclosed:
                self._log.append('>>> 平仓 净利=%.2f' % trade.pnlcomm)
            super(LoggedStrategy, self).notify_trade(trade)

        def next(self):
            super(LoggedStrategy, self).next()
            self._equity.append(
                (self.data.datetime.date(0).isoformat(),
                 round(self.broker.getvalue(), 2)))

    return LoggedStrategy


# ---------------------------------------------------------------- 工具

def _set_status(task_dir, status):
    with open(os.path.join(task_dir, 'status'), 'w') as f:
        f.write(status)


def _fmt_dt(num):
    s = bt.num2date(num).isoformat()
    if s[11:19] in ('00:00:00', '23:59:59'):  # 日线数据时间无意义
        return s[:10]
    return s[:19]


def _num(v, nd=2):
    return round(float(v), nd) if v is not None else None


def _clean(v):
    """NaN → None（JSON null，ECharts 断线）"""
    if v is None:
        return None
    v = float(v)
    return None if math.isnan(v) else round(v, 6)


def collect_trades(strat):
    """从 strat._trades 收集逐笔交易（含未平仓）。

    已平仓的 Trade.size 会被归零、price 变为均价，因此开启 tradehistory
    后从 history 的首/末事件取开仓手数与两腿成交价。
    """
    rows = []
    for data, by_tid in list(strat._trades.items()):
        for tid, tlist in list(by_tid.items()):
            for t in tlist:
                if not t.isopen and not t.history:
                    continue  # 空壳记录
                if t.history:
                    open_size = t.history[0].status.size
                    open_price = t.history[0].event.price
                    exit_price = None if t.isopen else t.history[-1].event.price
                else:
                    open_size = t.size
                    open_price = t.price
                    exit_price = None if t.isopen else t.price
                rows.append({
                    'open': _fmt_dt(t.dtopen),
                    'close': None if t.isopen else _fmt_dt(t.dtclose),
                    'size': _num(open_size, 4),
                    'open_price': _num(open_price),
                    'close_price': _num(exit_price),
                    'pnl': _num(t.pnl),
                    'pnlcomm': _num(t.pnlcomm),
                    'bars_held': getattr(t, 'barlen', None),
                })
    rows.sort(key=lambda r: r['open'])
    return rows


def collect_chart_data(strat):
    """从跑完的策略/数据上收集 ECharts 图表数据"""
    d = strat.datas[0]
    n = len(d)
    dates = []
    for i in range(n):
        dates.append(_fmt_dt(d.datetime.array[i]))
    candles = [[_clean(d.open.array[i]), _clean(d.close.array[i]),
                _clean(d.low.array[i]), _clean(d.high.array[i])]
               for i in range(n)]
    volume = [_clean(d.volume.array[i]) for i in range(n)]

    # 指标线（最多 8 条，跳过关闭绘图的）。嵌套指标常无 _name，用 类名+序号 兜底
    indicators = []
    for idx, ind in enumerate(strat.getindicators(), 1):
        try:
            if not getattr(ind.plotinfo, 'plot', True):
                continue
            name = getattr(ind, '_name', None) or \
                '%s%d' % (type(ind).__name__, idx)
            for i, line in enumerate(ind.lines):
                lname = getattr(line, '_name', None) or 'line%d' % i
                full = f'{name}·{lname}' if len(ind.lines) > 1 else name
                data = [_clean(line.array[j]) for j in range(n)]
                if any(v is not None for v in data):
                    indicators.append({'name': full, 'data': data})
                if len(indicators) >= 8:
                    break
        except Exception:
            continue
        if len(indicators) >= 8:
            break

    return {
        'dates': dates,
        'candles': candles,
        'volume': volume,
        'trades': list(getattr(strat, '_marks', [])),
        'equity': list(getattr(strat, '_equity', [])),
        'indicators': indicators,
    }


def _apply_broker(cerebro, broker_spec):
    b = broker_spec or {}
    cerebro.broker.setcash(float(b.get('cash', 100000.0)))
    comm = float(b.get('commission', 0.0))
    if comm:
        cerebro.broker.setcommission(commission=comm)
    slip = float(b.get('slippage', 0.0))
    if slip:
        cerebro.broker.set_slippage_perc(perc=slip)


def _apply_sizer(cerebro, sizer_spec):
    s = sizer_spec or {}
    stype = s.get('type', 'percent')
    value = float(s.get('value', 90))
    if stype == 'fixed':
        cerebro.addsizer(bt.sizers.FixedSize, stake=max(1, int(value)))
    else:
        cerebro.addsizer(bt.sizers.PercentSizer,
                         percents=max(1.0, min(100.0, value)))


# ---------------------------------------------------------------- backtest

def summarize(strat, start_value, end_value):
    r = strat.analyzers.returns.get_analysis()
    sh = strat.analyzers.sharpe.get_analysis()
    sha = strat.analyzers.sharpe_a.get_analysis()
    dd = strat.analyzers.dd.get_analysis()
    sqn = strat.analyzers.sqn.get_analysis()
    ta = strat.analyzers.ta.get_analysis()

    won = ta.get('won', {})
    lost = ta.get('lost', {})
    nw, nl = won.get('total', 0), lost.get('total', 0)
    total = ta.get('total', {}).get('closed', 0)

    # 买入持有基准
    try:
        d = strat.datas[0]
        bh = (d.close.array[len(d) - 1] / d.close.array[0] - 1) * 100
    except Exception:
        bh = None

    return {
        'start_value': round(start_value, 2),
        'end_value': round(end_value, 2),
        'total_return_pct': round((end_value / start_value - 1) * 100, 2),
        'annual_pct': _num(r.get('rnorm100'), 2),
        'sharpe': _num(sh.get('sharperatio'), 3),
        'sharpe_annual': _num(sha.get('sharperatio'), 3),
        'max_dd_pct': _num(dd.max.drawdown, 2),
        'max_dd_money': _num(dd.max.moneydown, 2),
        'max_dd_len': dd.max.len,
        'sqn': _num(sqn.sqn, 2),
        'closed_total': total,
        'won': nw,
        'lost': nl,
        'winrate_pct': round(nw / total * 100, 1) if total else None,
        'avg_win': _num(won.get('pnl', {}).get('average')),
        'avg_loss': _num(lost.get('pnl', {}).get('average')),
        'best_trade': _num(won.get('pnl', {}).get('max')),
        'worst_trade': _num(lost.get('pnl', {}).get('max')),
        'buyhold_pct': _num(bh),
    }


def run_one_backtest(req, path):
    """在单个数据上跑一次完整回测，返回该数据的全部结果"""
    data = load_data_feed(req['data'], path)
    stratcls = make_logged(build_strategy_class(req['strategy']))

    cerebro = bt.Cerebro(tradehistory=True)
    cerebro.adddata(data, name=data._name)
    cerebro.addstrategy(stratcls)
    _apply_broker(cerebro, req.get('broker'))
    _apply_sizer(cerebro, req.get('sizer'))

    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe',
                        timeframe=bt.TimeFrame.Days, riskfreerate=0.0)
    cerebro.addanalyzer(bt.analyzers.SharpeRatio_A, _name='sharpe_a',
                        timeframe=bt.TimeFrame.Days, riskfreerate=0.0)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name='dd')
    cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
    cerebro.addanalyzer(bt.analyzers.SQN, _name='sqn')
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='ta')
    cerebro.addanalyzer(bt.analyzers.TimeReturn, _name='tr',
                        timeframe=bt.TimeFrame.Years)

    start_value = cerebro.broker.getvalue()
    strat = cerebro.run()[0]
    end_value = cerebro.broker.getvalue()

    yearly = {}
    for k, v in sorted(strat.analyzers.tr.get_analysis().items()):
        yearly[str(getattr(k, 'year', k))] = _num(v * 100)

    return {
        'data_name': data._name,
        'summary': summarize(strat, start_value, end_value),
        'yearly': yearly,
        'trades': collect_trades(strat),
        'log': list(getattr(strat, '_log', [])),
        'chart': collect_chart_data(strat),
    }


def run_backtest(req, task_dir):
    paths = data_paths(req['data'])
    runs = [run_one_backtest(req, p) for p in paths]

    # 对比视图：每个数据的摘要 + 归一化权益曲线（起点=100）
    comparison = []
    for run in runs:
        eq = run['chart']['equity']
        eq_norm = []
        if eq:
            base = eq[0][1] or 1.0
            eq_norm = [round(v / base * 100, 2) for _, v in eq]
        comparison.append({
            'data_name': run['data_name'],
            'total_return_pct': run['summary']['total_return_pct'],
            'annual_pct': run['summary']['annual_pct'],
            'sharpe': run['summary']['sharpe'],
            'max_dd_pct': run['summary']['max_dd_pct'],
            'winrate_pct': run['summary']['winrate_pct'],
            'buyhold_pct': run['summary']['buyhold_pct'],
            'equity_dates': [d for d, _ in eq],
            'equity_norm': eq_norm,
        })

    result = {'mode': 'backtest', 'multi': len(runs) > 1,
              'runs': runs, 'comparison': comparison}
    with open(os.path.join(task_dir, 'result.json'), 'w') as f:
        json.dump(result, f, ensure_ascii=False)


# ---------------------------------------------------------------- optimize

def run_optimize(req, task_dir):
    data = load_data_feed(req['data'], data_paths(req['data'])[0])
    stratcls = build_strategy_class(req['strategy'])

    grid = {}
    for name, val in (req['strategy'].get('params') or {}).items():
        if isinstance(val, dict) and 'start' in val:
            step = float(val.get('step') or 1)
            v, end = float(val['start']), float(val['end'])
            vals = []
            while v <= end + 1e-9:
                vals.append(int(v) if step >= 1 and float(v).is_integer()
                            else round(v, 6))
                v += step
            if len(vals) > 400:
                raise RequestError(
                    f'参数 {name} 网格点数 {len(vals)} 超过 400，请增大步长')
            grid[name] = vals
        else:
            grid[name] = [val]

    total = 1
    for v in grid.values():
        total *= len(v)

    cerebro = bt.Cerebro(optreturn=True)
    cerebro.adddata(data, name=data._name)
    cerebro.optstrategy(stratcls, **grid)
    _apply_broker(cerebro, req.get('broker'))
    _apply_sizer(cerebro, req.get('sizer'))
    cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe',
                        timeframe=bt.TimeFrame.Days, riskfreerate=0.0)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name='dd')

    progress_path = os.path.join(task_dir, 'progress.jsonl')
    rows = []

    def cb(runstrats):
        rs = runstrats[0]
        ret = rs.analyzers.returns.get_analysis()
        sh = rs.analyzers.sharpe.get_analysis()
        dd = rs.analyzers.dd.get_analysis()
        row = {
            'params': {k: getattr(rs.params, k, None) for k in grid},
            'annual_pct': _num(ret.get('rnorm100'), 2),
            'total_pct': _num((ret.get('rtot', 0) or 0) * 100, 2),
            'sharpe': _num(sh.get('sharperatio'), 3),
            'max_dd_pct': _num(dd.max.drawdown, 2),
        }
        rows.append(row)
        with open(progress_path, 'a') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

    cerebro.optcbs.append(cb)

    t0 = time.time()
    cerebro.run(maxcpus=1)
    elapsed = time.time() - t0

    rows.sort(key=lambda x: (x['annual_pct'] is None, x['annual_pct']),
              reverse=True)
    result = {'mode': 'optimize', 'data_name': data._name,
              'rows': rows, 'total': total, 'elapsed': round(elapsed, 1)}
    with open(os.path.join(task_dir, 'result.json'), 'w') as f:
        json.dump(result, f, ensure_ascii=False)


# ---------------------------------------------------------------- main

def main():
    if len(sys.argv) != 2:
        print('usage: python -m webapp.runner <task_dir>', file=sys.stderr)
        sys.exit(2)
    task_dir = sys.argv[1]
    try:
        with open(os.path.join(task_dir, 'request.json')) as f:
            req = json.load(f)
    except Exception as e:
        _set_status(task_dir, STATUS_ERROR)
        with open(os.path.join(task_dir, 'error.txt'), 'w') as f:
            f.write('读取请求失败: %s' % e)
        sys.exit(2)

    _set_status(task_dir, STATUS_RUNNING)
    try:
        if req.get('mode') == 'optimize':
            run_optimize(req, task_dir)
        else:
            run_backtest(req, task_dir)
        _set_status(task_dir, STATUS_DONE)
    except RequestError as e:
        with open(os.path.join(task_dir, 'error.txt'), 'w') as f:
            f.write(str(e))
        _set_status(task_dir, STATUS_ERROR)
        sys.exit(1)
    except Exception:
        with open(os.path.join(task_dir, 'error.txt'), 'w') as f:
            f.write(traceback.format_exc())
        _set_status(task_dir, STATUS_ERROR)
        sys.exit(1)


if __name__ == '__main__':
    main()
