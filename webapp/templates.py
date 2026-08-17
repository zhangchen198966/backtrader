# -*- coding: utf-8 -*-
"""bt-lab: 内置策略模板（源码 + 参数元数据）

每个模板 code 是一段可 exec 的源码，在命名空间 {'bt': backtrader} 下执行后
应产生一个 bt.Strategy 子类。params 元数据驱动前端表单渲染。
"""

TEMPLATES = [
    {
        'id': 'sma_cross',
        'name': '双均线交叉',
        'desc': '快慢均线金叉做多、死叉平仓。最经典的趋势跟踪入门策略。',
        'params': [
            {'name': 'fast', 'label': '快均线', 'default': 10, 'min': 2, 'max': 100, 'step': 1, 'int': True, 'unit': 'bar 数'},
            {'name': 'slow', 'label': '慢均线', 'default': 30, 'min': 3, 'max': 250, 'step': 1, 'int': True, 'unit': 'bar 数'},
        ],
        'code': '''\
import backtrader as bt


class SmaCross(bt.Strategy):
    params = dict(fast=10, slow=30)

    def __init__(self):
        self.fast = bt.ind.SMA(self.data.close, period=self.p.fast)
        self.slow = bt.ind.SMA(self.data.close, period=self.p.slow)
        self.crossover = bt.ind.CrossOver(self.fast, self.slow)
        self.order = None

    def notify_order(self, order):
        if order.status in (order.Submitted, order.Accepted):
            return
        self.order = None

    def next(self):
        if self.order:
            return
        if not self.position:
            if self.crossover[0] > 0:
                self.order = self.buy()
        elif self.crossover[0] < 0:
            self.order = self.close()
''',
    },
    {
        'id': 'sma_rsi_atr',
        'name': '双均线 + RSI过滤 + ATR跟踪止损',
        'desc': '金叉且 RSI 确认趋势强度时做多；死叉或跌破 3 倍 ATR 跟踪止损线离场。',
        'params': [
            {'name': 'fast', 'label': '快均线', 'default': 10, 'min': 2, 'max': 100, 'step': 1, 'int': True, 'unit': 'bar 数'},
            {'name': 'slow', 'label': '慢均线', 'default': 30, 'min': 3, 'max': 250, 'step': 1, 'int': True, 'unit': 'bar 数'},
            {'name': 'rsi_period', 'label': 'RSI周期', 'default': 14, 'min': 2, 'max': 100, 'step': 1, 'int': True, 'unit': 'bar 数'},
            {'name': 'rsi_min', 'label': 'RSI下限', 'default': 50, 'min': 0, 'max': 100, 'step': 1, 'int': True, 'unit': '0~100'},
            {'name': 'atr_period', 'label': 'ATR周期', 'default': 14, 'min': 2, 'max': 100, 'step': 1, 'int': True, 'unit': 'bar 数'},
            {'name': 'atr_mult', 'label': 'ATR止损倍数', 'default': 3.0, 'min': 0.5, 'max': 10.0, 'step': 0.5, 'int': False, 'unit': '倍数'},
        ],
        'code': '''\
import backtrader as bt


class SmaRsiAtrStrategy(bt.Strategy):
    params = dict(fast=10, slow=30, rsi_period=14, rsi_min=50,
                  atr_period=14, atr_mult=3.0)

    def __init__(self):
        self.sma_fast = bt.ind.SMA(self.data.close, period=self.p.fast)
        self.sma_slow = bt.ind.SMA(self.data.close, period=self.p.slow)
        self.crossover = bt.ind.CrossOver(self.sma_fast, self.sma_slow)
        self.rsi = bt.ind.RSI(self.data.close, period=self.p.rsi_period)
        self.atr = bt.ind.ATR(self.data, period=self.p.atr_period)
        self.order = None
        self.stop_price = None

    def notify_order(self, order):
        if order.status in (order.Submitted, order.Accepted):
            return
        self.order = None

    def next(self):
        if self.order:
            return

        if not self.position:
            if self.crossover[0] > 0 and self.rsi[0] > self.p.rsi_min:
                self.order = self.buy()
                self.stop_price = self.data.close[0] - self.p.atr_mult * self.atr[0]
        else:
            new_stop = self.data.close[0] - self.p.atr_mult * self.atr[0]
            self.stop_price = max(self.stop_price, new_stop)
            if self.crossover[0] < 0 or self.data.close[0] < self.stop_price:
                self.order = self.close()
''',
    },
    {
        'id': 'bbands_reversal',
        'name': '布林带反转',
        'desc': '收盘价跌破下轨买入博反弹，回到中轨或跌破止损线离场（均值回归思路）。',
        'params': [
            {'name': 'period', 'label': '布林周期', 'default': 20, 'min': 5, 'max': 100, 'step': 1, 'int': True, 'unit': 'bar 数'},
            {'name': 'dev', 'label': '标准差倍数', 'default': 2.0, 'min': 0.5, 'max': 4.0, 'step': 0.25, 'int': False, 'unit': '标准差倍数'},
            {'name': 'stop_pct', 'label': '止损幅度%', 'default': 5.0, 'min': 1.0, 'max': 30.0, 'step': 0.5, 'int': False, 'unit': '%'},
        ],
        'code': '''\
import backtrader as bt


class BbandsReversal(bt.Strategy):
    params = dict(period=20, dev=2.0, stop_pct=5.0)

    def __init__(self):
        self.bbands = bt.ind.BollingerBands(self.data.close,
                                            period=self.p.period,
                                            devfactor=self.p.dev)
        self.order = None
        self.entry = None

    def notify_order(self, order):
        if order.status in (order.Submitted, order.Accepted):
            return
        self.order = None

    def next(self):
        if self.order:
            return
        close = self.data.close[0]
        if not self.position:
            if close < self.bbands.lines.bot[0]:
                self.order = self.buy()
                self.entry = close
        else:
            stop = self.entry * (1 - self.p.stop_pct / 100.0)
            if close >= self.bbands.lines.mid[0] or close < stop:
                self.order = self.close()
''',
    },
    {
        'id': 'macd_signal',
        'name': 'MACD 信号',
        'desc': 'MACD 信号线上穿做多、下穿平仓，可调快慢均线与信号周期。',
        'params': [
            {'name': 'fast', 'label': '快EMA', 'default': 12, 'min': 2, 'max': 50, 'step': 1, 'int': True, 'unit': 'bar 数'},
            {'name': 'slow', 'label': '慢EMA', 'default': 26, 'min': 3, 'max': 200, 'step': 1, 'int': True, 'unit': 'bar 数'},
            {'name': 'signal', 'label': '信号EMA', 'default': 9, 'min': 2, 'max': 50, 'step': 1, 'int': True, 'unit': 'bar 数'},
        ],
        'code': '''\
import backtrader as bt


class MacdSignal(bt.Strategy):
    params = dict(fast=12, slow=26, signal=9)

    def __init__(self):
        self.macd = bt.ind.MACD(self.data.close,
                                period_me1=self.p.fast,
                                period_me2=self.p.slow,
                                period_signal=self.p.signal)
        self.crossover = bt.ind.CrossOver(self.macd.macd, self.macd.signal)
        self.order = None

    def notify_order(self, order):
        if order.status in (order.Submitted, order.Accepted):
            return
        self.order = None

    def next(self):
        if self.order:
            return
        if not self.position:
            if self.crossover[0] > 0:
                self.order = self.buy()
        elif self.crossover[0] < 0:
            self.order = self.close()
''',
    },

    {
        'id': 'supertrend',
        'name': '超级趋势 SuperTrend（ATR 通道翻转）',
        'desc': '源自 TradingView 经典规则：价格 ± N 倍 ATR 构成动态通道，趋势随价格'
                '突破上下轨翻转，翻多做多、翻空平仓。趋势行情利器，震荡市易被反复打脸。',
        'params': [
            {'name': 'atr_period', 'label': 'ATR周期', 'default': 10, 'min': 2, 'max': 100, 'step': 1, 'int': True, 'unit': 'bar 数'},
            {'name': 'multiplier', 'label': '通道倍数', 'default': 3.0, 'min': 0.5, 'max': 10.0, 'step': 0.5, 'int': False, 'unit': '倍 ATR'},
        ],
        'code': '''\
import backtrader as bt


class SuperTrendInd(bt.Indicator):
    """SuperTrend 指标（TradingView 标准算法）"""
    params = dict(period=10, multiplier=3.0)
    lines = ('supertrend', 'direction')
    plotinfo = dict(subplot=False)

    def __init__(self):
        atr = bt.ind.ATR(self.data, period=self.p.period)
        hl2 = (self.data.high + self.data.low) / 2.0
        basic_upper = hl2 + self.p.multiplier * atr
        basic_lower = hl2 - self.p.multiplier * atr
        self.l.supertrend = bt.ind.If(self.l.direction < 0, basic_upper, basic_lower)

    def next(self):
        d = self.l.direction
        if len(self) < 2:
            d[0] = 1
            return
        prev = d[-1]
        if prev > 0:
            d[0] = -1 if self.data.close[0] < self.l.supertrend[-1] else 1
        else:
            d[0] = 1 if self.data.close[0] > self.l.supertrend[-1] else -1


class SuperTrendStrategy(bt.Strategy):
    params = dict(atr_period=10, multiplier=3.0)

    def __init__(self):
        self.st = SuperTrendInd(self.data, period=self.p.atr_period,
                                multiplier=self.p.multiplier)
        self.order = None

    def notify_order(self, order):
        if order.status in (order.Submitted, order.Accepted):
            return
        self.order = None

    def next(self):
        if self.order or len(self) < self.p.atr_period + 2:
            return
        d = self.st.lines.direction[0]
        pd = self.st.lines.direction[-1]
        if not self.position and d > 0 and pd < 0:
            self.order = self.buy()
        elif self.position and d < 0 and pd > 0:
            self.order = self.close()
''',
    },
    {
        'id': 'turtle_donchian',
        'name': '海龟法则 · Donchian 通道突破（简化版）',
        'desc': '源自知乎《史上最详尽的海龟交易法则笔记》：突破 N1 日最高价入场、'
                '跌破 N2 日最低价离场（经典 20/10），可选 ATR 止损。趋势跟踪经典。',
        'params': [
            {'name': 'entry_period', 'label': '入场突破周期', 'default': 20, 'min': 5, 'max': 100, 'step': 1, 'int': True, 'unit': 'bar 数'},
            {'name': 'exit_period', 'label': '离场突破周期', 'default': 10, 'min': 3, 'max': 100, 'step': 1, 'int': True, 'unit': 'bar 数'},
            {'name': 'atr_stop', 'label': 'ATR止损倍数(0=关)', 'default': 2.0, 'min': 0, 'max': 10.0, 'step': 0.5, 'int': False, 'unit': '倍 ATR'},
        ],
        'code': '''\
import backtrader as bt


class TurtleDonchian(bt.Strategy):
    params = dict(entry_period=20, exit_period=10, atr_stop=2.0)

    def __init__(self):
        self.upper = bt.ind.Highest(self.data.high, period=self.p.entry_period)
        self.lower = bt.ind.Lowest(self.data.low, period=self.p.exit_period)
        self.atr = bt.ind.ATR(self.data, period=14)
        self.order = None
        self.stop_price = None  # 不能叫 self.stop：会覆盖 Strategy.stop() 生命周期方法

    def notify_order(self, order):
        if order.status in (order.Submitted, order.Accepted):
            return
        self.order = None

    def next(self):
        if self.order or len(self) < self.p.entry_period + 1:
            return
        close = self.data.close[0]
        if not self.position:
            if close > self.upper[-1]:
                self.order = self.buy()
                self.stop_price = (close - self.p.atr_stop * self.atr[0]
                                   if self.p.atr_stop > 0 else None)
        else:
            if self.stop_price is not None:
                self.stop_price = max(self.stop_price,
                                      close - self.p.atr_stop * self.atr[0])
            if close < self.lower[-1] or                     (self.stop_price is not None and close < self.stop_price):
                self.order = self.close()
                self.stop_price = None
''',
    },
    {
        'id': 'squeeze_momentum',
        'name': '布林挤压 Squeeze Momentum',
        'desc': '源自通道突破对比研究：布林带收窄进肯特纳通道=波动挤压，挤压释放时'
                '按动量方向入场，动量转弱离场。等待波动率扩张的入场时机。',
        'params': [
            {'name': 'bb_period', 'label': '布林周期', 'default': 20, 'min': 5, 'max': 100, 'step': 1, 'int': True, 'unit': 'bar 数'},
            {'name': 'bb_dev', 'label': '布林标准差倍数', 'default': 2.0, 'min': 0.5, 'max': 4.0, 'step': 0.25, 'int': False, 'unit': '标准差倍数'},
            {'name': 'kc_mult', 'label': '肯特纳通道倍数', 'default': 1.5, 'min': 0.5, 'max': 4.0, 'step': 0.25, 'int': False, 'unit': '倍 ATR'},
        ],
        'code': '''\
import backtrader as bt


class SqueezeMomentum(bt.Strategy):
    params = dict(bb_period=20, bb_dev=2.0, kc_mult=1.5)

    def __init__(self):
        self.bb = bt.ind.BollingerBands(self.data.close, period=self.p.bb_period,
                                        devfactor=self.p.bb_dev)
        kc_mid = bt.ind.EMA(self.data.close, period=self.p.bb_period)
        atr = bt.ind.ATR(self.data, period=self.p.bb_period)
        self.kc_upper = kc_mid + self.p.kc_mult * atr
        self.kc_lower = kc_mid - self.p.kc_mult * atr
        mid = (self.bb.lines.mid + kc_mid) / 2.0
        self.mom = bt.ind.EMA(self.data.close - mid, period=3)
        self.order = None

    def notify_order(self, order):
        if order.status in (order.Submitted, order.Accepted):
            return
        self.order = None

    def next(self):
        if self.order or len(self) < self.p.bb_period + 3:
            return
        squeeze_on = (self.bb.lines.top[0] < self.kc_upper[0] and
                      self.bb.lines.bot[0] > self.kc_lower[0])
        squeeze_was_on = (self.bb.lines.top[-1] < self.kc_upper[-1] and
                          self.bb.lines.bot[-1] > self.kc_lower[-1])
        released = squeeze_was_on and not squeeze_on
        if not self.position:
            if released and self.mom[0] > 0:
                self.order = self.buy()
        else:
            if self.mom[0] < 0 or self.data.close[0] < self.bb.lines.bot[0]:
                self.order = self.close()
''',
    },
]

TEMPLATE_IDS = [t['id'] for t in TEMPLATES]


def get_template(tid):
    for t in TEMPLATES:
        if t['id'] == tid:
            return t
    return None
