# -*- coding: utf-8 -*-
"""
backtrader 绘图演示
==================
跑一遍 demo_backtest 的策略，用 cerebro.plot 输出内置图表并保存为 PNG。
无 GUI 环境，使用 Agg 后端。

运行：python3 demo_plot.py
"""
import os

import matplotlib
matplotlib.use('Agg')

import backtrader as bt
from demo_backtest import SmaRsiAtrStrategy


def main():
    datafile = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'datas', '2006-day-001.txt')

    cerebro = bt.Cerebro()
    cerebro.adddata(bt.feeds.GenericCSVData(dataname=datafile,
                                            dtformat='%Y-%m-%d'), name='DAY001')
    cerebro.addstrategy(SmaRsiAtrStrategy)
    cerebro.broker.setcash(100000.0)
    cerebro.broker.setcommission(commission=0.001)
    cerebro.broker.set_slippage_perc(perc=0.0005)
    cerebro.addsizer(bt.sizers.PercentSizer, percents=90)

    cerebro.run()

    # backtrader 在 macOS 上会把后端强制切到 MacOSX（交互窗口，无头环境会卡死在
    # plt.show()）。这里在它导入绘图模块后、创建 figure 前，切回 Agg 无头后端。
    from backtrader import plot as _  # noqa: F401 触发 backtrader 的后端切换
    matplotlib.use('Agg')

    # style='candle' K线；volume 显示在子图； observers 展示 Cash/Value/买卖点
    figs = cerebro.plot(style='candle', volume=True, iplot=False,
                        numfigs=1, figsize=(16, 9))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'demo_backtest.png')
    figs[0][0].savefig(out, dpi=100, bbox_inches='tight')
    print(f'图表已保存: {out}')


if __name__ == '__main__':
    main()
