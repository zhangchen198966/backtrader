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
            {'name': 'fast', 'label': '快均线', 'default': 10, 'min': 2, 'max': 100, 'step': 1, 'int': True},
            {'name': 'slow', 'label': '慢均线', 'default': 30, 'min': 3, 'max': 250, 'step': 1, 'int': True},
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
            {'name': 'fast', 'label': '快均线', 'default': 10, 'min': 2, 'max': 100, 'step': 1, 'int': True},
            {'name': 'slow', 'label': '慢均线', 'default': 30, 'min': 3, 'max': 250, 'step': 1, 'int': True},
            {'name': 'rsi_period', 'label': 'RSI周期', 'default': 14, 'min': 2, 'max': 100, 'step': 1, 'int': True},
            {'name': 'rsi_min', 'label': 'RSI下限', 'default': 50, 'min': 0, 'max': 100, 'step': 1, 'int': True},
            {'name': 'atr_period', 'label': 'ATR周期', 'default': 14, 'min': 2, 'max': 100, 'step': 1, 'int': True},
            {'name': 'atr_mult', 'label': 'ATR止损倍数', 'default': 3.0, 'min': 0.5, 'max': 10.0, 'step': 0.5, 'int': False},
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
            {'name': 'period', 'label': '布林周期', 'default': 20, 'min': 5, 'max': 100, 'step': 1, 'int': True},
            {'name': 'dev', 'label': '标准差倍数', 'default': 2.0, 'min': 0.5, 'max': 4.0, 'step': 0.25, 'int': False},
            {'name': 'stop_pct', 'label': '止损幅度%', 'default': 5.0, 'min': 1.0, 'max': 30.0, 'step': 0.5, 'int': False},
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
            {'name': 'fast', 'label': '快EMA', 'default': 12, 'min': 2, 'max': 50, 'step': 1, 'int': True},
            {'name': 'slow', 'label': '慢EMA', 'default': 26, 'min': 3, 'max': 200, 'step': 1, 'int': True},
            {'name': 'signal', 'label': '信号EMA', 'default': 9, 'min': 2, 'max': 50, 'step': 1, 'int': True},
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
]

TEMPLATE_IDS = [t['id'] for t in TEMPLATES]


def get_template(tid):
    for t in TEMPLATES:
        if t['id'] == tid:
            return t
    return None
