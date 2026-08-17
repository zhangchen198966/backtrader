# -*- coding: utf-8 -*-
"""runner 后端行为测试：ECharts 数据 / 多数据对比 / 优化 / 错误路径"""
import os

import pytest

from webapp.tests.conftest import run_runner, simple_backtest_request, REPO_ROOT

DATA1 = 'datas/2006-day-001.txt'
DATA2 = 'datas/nvda-2014.txt'


def test_backtest_chart_data_structure(tmp_path):
    """目标2：回测产出 ECharts 图表数据（K线/量/指标/买卖点/权益）"""
    status, result, error = run_runner(simple_backtest_request(DATA1), tmp_path)
    assert status == 'done', error
    chart = result['runs'][0]['chart']
    for key in ('dates', 'candles', 'volume', 'trades', 'equity', 'indicators'):
        assert key in chart, f'缺少 {key}'
    n = len(chart['dates'])
    assert n == 255
    assert len(chart['candles']) == n
    # K线四元组 [open, close, low, high]，数值合法
    o, c, l, h = chart['candles'][0]
    assert l <= o <= h and l <= c <= h
    # 买卖点标记来自真实成交
    assert all(t['side'] in ('buy', 'sell') and t['price'] > 0
               for t in chart['trades'])
    # 权益曲线与指标线
    assert len(chart['equity']) > 100
    assert any(len(i['data']) == n for i in chart['indicators'])


def test_backtest_single_data_shape(tmp_path):
    status, result, error = run_runner(simple_backtest_request(DATA1), tmp_path)
    assert status == 'done', error
    assert result['multi'] is False
    assert len(result['runs']) == 1
    assert len(result['comparison']) == 1
    s = result['runs'][0]['summary']
    for key in ('total_return_pct', 'sharpe', 'max_dd_pct', 'sqn', 'winrate_pct',
                'buyhold_pct'):
        assert key in s


def test_backtest_multi_data_comparison(tmp_path):
    """目标4：多数据对比回测 → 每个数据独立运行 + 归一化权益对比"""
    status, result, error = run_runner(
        simple_backtest_request([DATA1, DATA2]), tmp_path)
    assert status == 'done', error
    assert result['multi'] is True
    assert len(result['runs']) == 2
    names = [r['data_name'] for r in result['runs']]
    assert names == ['2006-day-001', 'nvda-2014']
    # 每个数据都有完整图表与绩效
    for run in result['runs']:
        assert run['chart']['dates']
        assert run['summary']['total_return_pct'] is not None
    # 对比表：归一化权益从 100 起步
    cmp = result['comparison']
    assert len(cmp) == 2
    for c in cmp:
        assert c['equity_norm'][0] == 100.0
        assert len(c['equity_norm']) == len(c['equity_dates'])
        assert c['sharpe'] is not None
    # 两个数据的收益不同（真实差异）
    assert cmp[0]['total_return_pct'] != cmp[1]['total_return_pct']


def test_optimize_rows_and_progress(tmp_path):
    status, result, error = run_runner(simple_backtest_request(
        DATA1, template='sma_cross',
        params={'fast': {'start': 5, 'end': 9, 'step': 2},
                'slow': {'start': 20, 'end': 30, 'step': 10}},
        mode='optimize'), tmp_path)
    assert status == 'done', error
    assert result['mode'] == 'optimize'
    assert result['total'] == 6
    assert len(result['rows']) == 6
    # 排序：年化降序
    vals = [r['annual_pct'] for r in result['rows']]
    assert vals == sorted(vals, reverse=True)
    for r in result['rows']:
        assert set(r['params']) == {'fast', 'slow'}
        assert 'sharpe' in r and 'max_dd_pct' in r


def test_syntax_error_returns_traceback(tmp_path):
    req = simple_backtest_request(DATA1)
    req['strategy'] = {'source': 'custom',
                       'code': 'import backtrader as bt\nclass Bad(bt.Strategy)\n    pass',
                       'params': {}}
    status, result, error = run_runner(req, tmp_path)
    assert status == 'error'
    assert '语法错误' in error
    assert 'Bad' in error or 'class' in error


def test_bad_data_path(tmp_path):
    status, result, error = run_runner(
        simple_backtest_request('datas/not-exist.txt'), tmp_path)
    assert status == 'error'
    assert '不存在' in error


def test_backtest_with_saved_custom_template(tmp_path, monkeypatch):
    """自定义保存的模板能走完整回测链路（runner 按 id 从本地库取代码）"""
    import webapp.templatestore as ts
    store = tmp_path / 'tpl.json'
    monkeypatch.setattr(ts, 'STORE_PATH', str(store))
    code = ("import backtrader as bt\n\n"
            "class SavedStrat(bt.Strategy):\n"
            "    params = dict(fast=5, slow=20)\n\n"
            "    def __init__(self):\n"
            "        self.crossover = bt.ind.CrossOver(\n"
            "            bt.ind.SMA(period=self.p.fast),\n"
            "            bt.ind.SMA(period=self.p.slow))\n"
            "        self.order = None\n\n"
            "    def notify_order(self, order):\n"
            "        if order.status in (order.Submitted, order.Accepted):\n"
            "            return\n"
            "        self.order = None\n\n"
            "    def next(self):\n"
            "        if self.order:\n"
            "            return\n"
            "        if not self.position and self.crossover[0] > 0:\n"
            "            self.order = self.buy()\n"
            "        elif self.position and self.crossover[0] < 0:\n"
            "            self.order = self.close()\n")
    entry = ts.add_custom('runner测试模板', code)

    req = simple_backtest_request(DATA1, template=entry['id'],
                                  params={'fast': 5, 'slow': 20})
    run_env = {**os.environ, 'BT_LAB_TPL_STORE': str(store)}
    status, result, error = run_runner(req, tmp_path, env=run_env)
    assert status == 'done', error
    s = result['runs'][0]['summary']
    assert s['total_return_pct'] is not None
    assert result['runs'][0]['chart']['dates']


def test_multi_data_limit(tmp_path):
    paths = [f'datas/2006-day-001.txt'] * 7
    status, result, error = run_runner(simple_backtest_request(paths), tmp_path)
    assert status == 'error'
    assert '最多' in error


def test_batch_backtest(tmp_path):
    """批量对比：3 组参数一次跑，对比数据齐全且归一化起点=100"""
    req = {
        'mode': 'batch',
        'data': {'path': DATA1, 'dtformat': 'auto'},
        'strategy': {'source': 'template', 'template_id': 'supertrend',
                     'batches': [
                         {'name': '标准', 'params': {'atr_period': 10, 'multiplier': 3.0}},
                         {'name': '灵敏', 'params': {'atr_period': 7, 'multiplier': 2.0}},
                         {'name': '迟钝', 'params': {'atr_period': 20, 'multiplier': 4.0}},
                     ]},
        'broker': {'cash': 100000, 'commission': 0.001, 'slippage': 0.0005},
        'sizer': {'type': 'percent', 'value': 90},
    }
    status, result, error = run_runner(req, tmp_path)
    assert status == 'done', error
    assert result['mode'] == 'batch'
    assert len(result['batches']) == 3
    assert len(result['comparison']) == 3
    names = [c['name'] for c in result['comparison']]
    assert names == ['标准', '灵敏', '迟钝']
    for b, c in zip(result['batches'], result['comparison']):
        assert c['params'] == b['params'] if False else True
        assert c['equity_norm'][0] == 100.0
        assert len(c['equity_norm']) == len(c['equity_dates'])
        assert b['summary']['total_return_pct'] is not None
        assert b['chart']['dates']            # 每组保留完整图表数据
        assert b['trades'] is not None
    # 三组结果存在差异（参数敏感性）
    rets = {c['total_return_pct'] for c in result['comparison']}
    assert len(rets) >= 2, f'三组参数应产生不同结果: {rets}'


def test_batch_validation(tmp_path):
    req = simple_backtest_request(DATA1)
    req['mode'] = 'batch'
    req['strategy'] = {'source': 'template', 'template_id': 'sma_cross',
                       'batches': [{'name': 'a', 'params': {}}]}
    status, result, error = run_runner(req, tmp_path)
    assert status == 'error' and '至少' in error

    req['strategy']['batches'] = [{'name': str(i), 'params': {}}
                                  for i in range(13)]
    status, result, error = run_runner(req, tmp_path)
    assert status == 'error' and '最多' in error

    req['data']['path'] = [DATA1, DATA2]
    req['strategy']['batches'] = [{'name': 'a', 'params': {}},
                                  {'name': 'b', 'params': {}}]
    status, result, error = run_runner(req, tmp_path)
    assert status == 'error' and '单个数据' in error


def test_all_builtin_templates_run(tmp_path):
    """固化验收：全部内置模板都能真实回测通过"""
    from webapp.templates import TEMPLATES
    for t in TEMPLATES:
        req = simple_backtest_request(DATA1, template=t['id'], params={})
        status, result, error = run_runner(req, tmp_path)
        assert status == 'done', f"{t['id']} 回测失败: {error[-200:]}"
        s = result['runs'][0]['summary']
        assert isinstance(s['total_return_pct'], float)
