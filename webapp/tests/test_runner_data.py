# -*- coding: utf-8 -*-
"""runner 后端行为测试：ECharts 数据 / 多数据对比 / 优化 / 错误路径"""
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


def test_multi_data_limit(tmp_path):
    paths = [f'datas/2006-day-001.txt'] * 7
    status, result, error = run_runner(simple_backtest_request(paths), tmp_path)
    assert status == 'error'
    assert '最多' in error
