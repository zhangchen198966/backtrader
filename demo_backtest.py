# -*- coding: utf-8 -*-
"""
backtrader 完整回测演示
======================
策略：双均线择时 + RSI 强度过滤入场 + ATR 跟踪止损离场
数据：仓库自带 datas/2006-day-001.txt（2006 年日线，255 根 bar）

演示的能力点：
  - GenericCSVData 加载 CSV 数据
  - 内置指标 SMA / RSI / ATR / CrossOver
  - 订单回调 notify_order / 交易回调 notify_trade
  - 佣金 + 滑点 + 百分比 Sizer 仓位管理
  - 分析器：Sharpe / DrawDown / Returns / TradeAnalyzer / SQN / 年度收益
  - 与买入持有基准对比

运行：python3 demo_backtest.py
"""
import os

import backtrader as bt


class SmaRsiAtrStrategy(bt.Strategy):
    """快慢均线金叉做多（RSI 过滤），死叉或跌破 ATR 跟踪止损离场"""

    params = dict(
        fast=10,        # 快均线周期
        slow=30,        # 慢均线周期
        rsi_period=14,  # RSI 周期
        rsi_min=50,     # 入场要求 RSI 高于该值（确认趋势强度）
        atr_period=14,  # ATR 周期
        atr_mult=3.0,   # 止损距离 = 3 倍 ATR
    )

    def log(self, txt, dt=None):
        dt = dt or self.data.datetime.date(0)
        print(f'[{dt}] {txt}')

    def __init__(self):
        self.sma_fast = bt.ind.SMA(self.data.close, period=self.p.fast)
        self.sma_slow = bt.ind.SMA(self.data.close, period=self.p.slow)
        self.crossover = bt.ind.CrossOver(self.sma_fast, self.sma_slow)
        self.rsi = bt.ind.RSI(self.data.close, period=self.p.rsi_period)
        self.atr = bt.ind.ATR(self.data, period=self.p.atr_period)

        self.order = None       # 当前挂单（有挂单时不重复下单）
        self.stop_price = None  # ATR 跟踪止损价

    def notify_order(self, order):
        if order.status in (order.Submitted, order.Accepted):
            return  # 只关心终态

        if order.status == order.Completed:
            side = '买入' if order.isbuy() else '卖出'
            self.log(f'{side}成交: 价={order.executed.price:9.2f} '
                     f'量={order.executed.size:6.2f} '
                     f'佣金={order.executed.comm:.2f}')
        elif order.status in (order.Canceled, order.Margin, order.Rejected):
            self.log(f'订单未成交: {order.getstatusname()}')

        self.order = None  # 挂单结束（无论成败），允许下一笔

    def notify_trade(self, trade):
        if trade.isclosed:
            self.log(f'>>> 交易平仓: 毛利={trade.pnl:9.2f} 净利={trade.pnlcomm:9.2f}')

    def next(self):
        if self.order:  # 等待中的订单，不重复发单
            return

        if not self.position:
            # 入场：金叉且 RSI 确认趋势强度
            if self.crossover[0] > 0 and self.rsi[0] > self.p.rsi_min:
                self.order = self.buy()
                self.stop_price = self.data.close[0] - self.p.atr_mult * self.atr[0]
        else:
            # 持仓期间：止损只上移不下移（跟踪止损）
            new_stop = self.data.close[0] - self.p.atr_mult * self.atr[0]
            self.stop_price = max(self.stop_price, new_stop)

            # 离场：死叉 或 跌破跟踪止损
            if self.crossover[0] < 0 or self.data.close[0] < self.stop_price:
                reason = '死叉离场' if self.crossover[0] < 0 else '止损离场'
                self.log(f'-- {reason} (止损线={self.stop_price:.2f})')
                self.order = self.close()

    def stop(self):
        print(f'\n[策略结束] 期末现金={self.broker.getcash():.2f}')


def build_cerebro(datafile):
    cerebro = bt.Cerebro()

    # ---- 数据 ----
    data = bt.feeds.GenericCSVData(
        dataname=datafile,
        dtformat='%Y-%m-%d',
        # 列序 Date,Open,High,Low,Close,Volume,OpenInterest 恰好是默认 0-6，无需指定
    )
    cerebro.adddata(data, name='DAY001')

    # ---- 策略 ----
    cerebro.addstrategy(SmaRsiAtrStrategy)

    # ---- 资金 / 佣金 / 滑点 ----
    cerebro.broker.setcash(100000.0)
    cerebro.broker.setcommission(commission=0.001)          # 0.1% 佣金
    cerebro.broker.set_slippage_perc(perc=0.0005)           # 0.05% 滑点

    # ---- 仓位管理：用 90% 可用现金自动算手数 ----
    cerebro.addsizer(bt.sizers.PercentSizer, percents=90)

    # ---- 分析器 ----
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
    return cerebro


def report(strat, start_value, end_value, datafile):
    """输出回测绩效报告"""
    ret = (end_value / start_value - 1) * 100
    print('\n' + '=' * 62)
    print('回测绩效报告')
    print('=' * 62)
    print(f'数据文件     : {os.path.basename(datafile)}')
    print(f'期初资金     : {start_value:>14,.2f}')
    print(f'期末资金     : {end_value:>14,.2f}')
    print(f'总收益率     : {ret:>13.2f}%')

    r = strat.analyzers.returns.get_analysis()
    print(f'年化收益率   : {r["rnorm100"]:>13.2f}%')

    sh = strat.analyzers.sharpe.get_analysis()
    sha = strat.analyzers.sharpe_a.get_analysis()
    sh_str = f'{sh["sharperatio"]:.3f}' if sh['sharperatio'] is not None else 'N/A'
    print(f'夏普比率     : {sh_str:>14} (年化: {sha["sharperatio"]:.3f})')

    dd = strat.analyzers.dd.get_analysis()
    print(f'最大回撤     : {dd.max.drawdown:>13.2f}% (持续 {dd.max.len} bar, '
          f'金额 {dd.max.moneydown:,.2f})')

    sqn = strat.analyzers.sqn.get_analysis()
    print(f'SQN 系统质量 : {sqn.sqn:>14.2f}')

    tr = strat.analyzers.tr.get_analysis()
    for year, val in sorted(tr.items()):
        print(f'  {year} 年收益: {val * 100:>9.2f}%')

    ta = strat.analyzers.ta.get_analysis()
    total = ta.get('total', {}).get('closed', 0)
    won = ta.get('won', {})
    lost = ta.get('lost', {})
    nw, nl = won.get('total', 0), lost.get('total', 0)
    if total:
        winrate = nw / total * 100
        print(f'交易统计     : 共平仓 {total} 笔 | 盈 {nw} / 亏 {nl} '
              f'| 胜率 {winrate:.1f}%')
        if nw:
            print(f'  盈利单均值 : {won.pnl.average:>13.2f} '
                  f'(最好 {won.pnl.max:,.2f})')
        if nl:
            print(f'  亏损单均值 : {lost.pnl.average:>13.2f} '
                  f'(最差 {lost.pnl.max:,.2f})')
    else:
        print('交易统计     : 无已平仓交易')

    # 买入持有基准（同一数据）
    with open(datafile) as f:
        f.readline()  # 跳过表头
        first = float(f.readline().split(',')[4])
        for line in f:
            last = float(line.split(',')[4])
    bh = (last / first - 1) * 100
    print('-' * 62)
    print(f'买入持有基准 : {bh:>13.2f}%   →  策略{"跑赢" if ret > bh else "跑输"}基准 '
          f'{abs(ret - bh):.2f} 个百分点')
    print('=' * 62)


def main():
    datafile = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'datas', '2006-day-001.txt')

    cerebro = build_cerebro(datafile)
    start_value = cerebro.broker.getvalue()
    print(f'期初资金: {start_value:,.2f}\n')

    results = cerebro.run()
    strat = results[0]

    end_value = cerebro.broker.getvalue()
    report(strat, start_value, end_value, datafile)

    # 保存交易明细（stdstats 默认观察器数据在此不做展示，绘图见 demo_plot.py）


if __name__ == '__main__':
    main()
