# -*- coding: utf-8 -*-
"""
backtrader 参数寻优演示
======================
用 cerebro.optstrategy 对 demo_backtest.py 的策略做参数网格搜索，
按年化收益率排序输出 Top 10。

运行：python3 demo_optimize.py
"""
import os

import backtrader as bt
from demo_backtest import SmaRsiAtrStrategy


class QuietStrategy(SmaRsiAtrStrategy):
    """优化时静默：屏蔽逐笔日志输出"""

    def log(self, txt, dt=None):
        pass

    def stop(self):
        pass


def main():
    datafile = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'datas', '2006-day-001.txt')

    cerebro = bt.Cerebro(optreturn=True)  # 只返回参数+分析器，更快
    cerebro.adddata(bt.feeds.GenericCSVData(dataname=datafile,
                                            dtformat='%Y-%m-%d'), name='DAY001')
    cerebro.broker.setcash(100000.0)
    cerebro.broker.setcommission(commission=0.001)
    cerebro.broker.set_slippage_perc(perc=0.0005)
    cerebro.addsizer(bt.sizers.PercentSizer, percents=90)
    cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')

    # 参数网格：6 x 5 x 2 x 2 = 120 组合
    cerebro.optstrategy(
        QuietStrategy,
        fast=range(5, 16, 2),          # 5,7,9,11,13,15
        slow=range(20, 45, 5),         # 20,25,30,35,40
        rsi_min=[45, 55],              # 两档过滤强度
        atr_mult=[2.0, 4.0],           # 两档止损宽度
    )

    results = cerebro.run(maxcpus=1)   # 单进程顺序跑，避免 macOS spawn 开销

    rows = []
    for rlist in results:              # run() 返回 list[list[OptReturn]]
        r = rlist[0]
        ret = r.analyzers.returns.get_analysis()
        rows.append((r.params.fast, r.params.slow, r.params.rsi_min,
                     r.params.atr_mult, ret['rnorm100']))
    rows.sort(key=lambda x: x[-1], reverse=True)

    print(f'共测试 {len(rows)} 组参数，按年化收益率 Top 10：\n')
    print(f'{"fast":>5} {"slow":>5} {"rsi_min":>8} {"atr_mult":>9} {"年化收益率":>10}')
    print('-' * 44)
    for fast, slow, rsi_min, mult, ann in rows[:10]:
        print(f'{fast:>5} {slow:>5} {rsi_min:>8} {mult:>9.1f} {ann:>9.2f}%')

    worst = rows[-1]
    print(f'\n最差组合: fast={worst[0]}, slow={worst[1]}, rsi_min={worst[2]}, '
          f'atr_mult={worst[3]:.0f} → {worst[4]:.2f}%')


if __name__ == '__main__':
    main()
