# -*- coding: utf-8 -*-
"""API 与静态资源测试：术语表 / 静态资源本地化 / 前端引用完整性"""
import os
import re

import httpx
from fastapi.testclient import TestClient

from webapp.glossary import GLOSSARY
from webapp.server import app
from webapp.tests.conftest import REPO_ROOT

client = TestClient(app)

INDEX = os.path.join(REPO_ROOT, 'webapp', 'static', 'index.html')
BT_LAB_JS = os.path.join(REPO_ROOT, 'webapp', 'static', 'bt-lab.js')
ECHARTS = os.path.join(REPO_ROOT, 'webapp', 'static', 'vendor', 'echarts.min.js')


def test_index_served():
    r = client.get('/')
    assert r.status_code == 200
    assert 'Backtrader 回测实验室' in r.text


def test_terms_api():
    """目标3：术语表 API 返回且包含核心量化名词"""
    r = client.get('/api/terms')
    assert r.status_code == 200
    terms = r.json()['terms']
    for must in ('夏普比率', '最大回撤', 'SQN', '年化收益率', '胜率', '买入持有',
                 'RSI', 'ATR', 'MACD', '布林带', '网格优化', '权益曲线'):
        assert must in terms, f'术语表缺少: {must}'
        assert len(terms[must]) >= 8  # 解释不是空壳


def test_terms_cover_param_labels_and_config():
    """术语表需覆盖全部策略参数标签与配置区术语（页面披露完整性）"""
    r = client.get('/api/terms')
    terms = r.json()['terms']
    # 模板参数标签（来自 templates.py 的 label 字段）
    from webapp.templates import TEMPLATES
    import re as _re
    param_labels = set()
    for t in TEMPLATES:
        for meta in t['params']:
            param_labels.add(meta['label'])
    # 配置区/交易明细静态标签
    html = open(INDEX, encoding='utf-8').read()
    static_labels = set(_re.findall(r'class="tlabel">([^<]+)<', html))
    # 网格模式在 span 里
    static_labels |= set(_re.findall(r'<span class="tlabel">([^<]+)</span>', html))

    glossary_keys = set(terms)
    missing_terms = []
    for label in param_labels | static_labels:
        # 标签至少能命中术语表中的某个词条（自身或其中包含的子串）
        hit = label in glossary_keys or any(
            k in label for k in glossary_keys if len(k) >= 2)
        if not hit:
            missing_terms.append(label)
    assert not missing_terms, f'这些标签完全没有术语解释: {missing_terms}'

    # 参数级概念必须有专门解释（而不是只蹭 RSI/ATR 的子串）
    for must in ('RSI周期', 'RSI下限', 'ATR周期', 'ATR止损倍数', '布林周期',
                 '标准差倍数', '止损幅度', '快EMA', '慢EMA', '信号EMA',
                 '手数', '开仓价', '平仓价', '持仓bar', '初始现金',
                 'Sizer 类型', '网格模式', '前复权', '策略-基准', '盈/亏笔数'):
        assert must in terms, f'术语表缺少参数/配置术语: {must}'


def test_terms_used_by_frontend_exist_in_glossary():
    """前端 T('...') 引用的术语必须都在术语表中（tips 才能弹出来）"""
    html = open(INDEX, encoding='utf-8').read()
    used = set(re.findall(r"\bT\('([^']+)'\)", html))
    assert used, '前端没有使用 T() 术语包装'
    missing = used - set(GLOSSARY)
    assert not missing, f'前端引用但术语表缺失: {missing}'


def test_static_assets_local_no_cdn():
    """目标2：ECharts 本地化，页面无任何 CDN 外链（离线可用）"""
    r1 = client.get('/static/bt-lab.js')
    r2 = client.get('/static/vendor/echarts.min.js')
    assert r1.status_code == 200 and len(r1.content) > 5000
    assert r2.status_code == 200 and len(r2.content) > 500000  # 完整 echarts
    html = open(INDEX, encoding='utf-8').read()
    assert '/static/vendor/echarts.min.js' in html
    assert '/static/bt-lab.js' in html
    assert not re.search(r'src=["\']https?://', html), '不允许外链脚本'
    assert not re.search(r'href=["\']https?://', html), '不允许外链样式'


def test_theme_css_in_index():
    """目标1：亮/暗双主题 CSS 存在且由 data-theme 切换"""
    html = open(INDEX, encoding='utf-8').read()
    assert 'html[data-theme="light"]' in html
    assert 'html[data-theme="dark"]' in html or ':root{' in html
    assert 'id="themeBtn"' in html
    js = open(BT_LAB_JS, encoding='utf-8').read()
    assert 'data-theme' in js and 'btlab-theme' in js


def test_editor_highlight_dom_present():
    """目标5：高亮编辑器结构（pre 叠加透明 textarea）与配色类存在"""
    html = open(INDEX, encoding='utf-8').read()
    assert 'id="hl"' in html and 'id="editor"' in html
    assert 'caret-color' in html  # 透明文字 + 可见光标
    for cls in ('.tk-kw', '.tk-str', '.tk-num', '.tk-com', '.tk-bi'):
        assert cls in html, f'缺少高亮配色 {cls}'


def test_term_tip_dom_present():
    """目标3：tips 浮层结构存在"""
    html = open(INDEX, encoding='utf-8').read()
    assert 'id="tipbox"' in html
    assert '.term' in html  # 术语样式与 span.term 渲染约定


def test_datas_and_templates_api():
    r = client.get('/api/datas')
    assert r.status_code == 200
    data = r.json()
    assert data['builtin'], '应列出仓库自带数据'
    r = client.get('/api/strategy/templates')
    assert r.status_code == 200
    tpls = r.json()['templates']
    assert {t['id'] for t in tpls} >= {'sma_cross', 'sma_rsi_atr',
                                       'bbands_reversal', 'macd_signal'}


def test_builtin_template_params_have_units():
    """目标1：所有内置模板参数必须带单位说明"""
    from webapp.templates import TEMPLATES
    n = 0
    for t in TEMPLATES:
        for p in t['params']:
            assert p.get('unit'), f"{t['id']}.{p['name']} 缺少 unit"
            n += 1
    assert n >= 14


def test_template_apis_crud(tmp_path, monkeypatch):
    """自定义模板：保存→合并列表→删除；非法代码 400"""
    import webapp.templatestore as ts
    monkeypatch.setattr(ts, 'STORE_PATH', str(tmp_path / 't.json'))
    import webapp.server as srv
    monkeypatch.setattr(srv, 'add_custom', ts.add_custom)
    monkeypatch.setattr(srv, 'list_custom', ts.list_custom)
    monkeypatch.setattr(srv, 'delete_custom', ts.delete_custom)

    code = ('import backtrader as bt\n\nclass T(bt.Strategy):\n'
            '    params = dict(n=5)\n    def next(self):\n        pass\n')
    r = client.post('/api/strategy/templates/custom',
                    json={'name': 'API测试模板', 'code': code})
    assert r.status_code == 200
    tid = r.json()['template']['id']

    r = client.get('/api/strategy/templates')
    assert tid in r.json()['custom_ids']

    r = client.post('/api/strategy/templates/custom',
                    json={'name': '坏的', 'code': 'nope'})
    assert r.status_code == 400

    r = client.delete(f'/api/strategy/templates/custom/{tid}')
    assert r.status_code == 200
    r = client.delete(f'/api/strategy/templates/custom/{tid}')
    assert r.status_code == 404


def test_market_apis():
    r = client.get('/api/strategy/market')
    assert r.status_code == 200
    items = r.json()['market']
    assert len(items) >= 14
    r = client.get('/api/strategy/market', params={'q': '止损'})
    assert any('mk-stoptrail' == m['id'] for m in r.json()['market'])
    # provider 过滤
    r = client.get('/api/strategy/market', params={'provider': 'community'})
    comm = r.json()['market']
    assert len(comm) == 7 and all(m['provider'] == 'community' for m in comm)
    r = client.get('/api/strategy/market', params={'provider': 'official'})
    assert all(m['provider'] == 'official' for m in r.json()['market'])

def test_meta_api():
    r = client.get('/api/meta')
    assert r.status_code == 200
    body = r.json()
    assert body['commit'] and body['started']
    r = client.post('/api/strategy/market/import', json={'id': 'mk-nope'})
    assert r.status_code == 400


def test_run_validation():
    """run 参数校验：mode / 数据缺失 → 400，不产生任务"""
    r = client.post('/api/run', json={'mode': 'bad'})
    assert r.status_code == 400
    r = client.post('/api/run', json={'mode': 'backtest', 'data': {}})
    assert r.status_code == 400
    r = client.post('/api/run', json={'mode': 'backtest',
                                      'data': {'path': []}})
    assert r.status_code == 400
    # batch 模式合法
    r = client.post('/api/run', json={
        'mode': 'batch',
        'data': {'path': ['datas/2006-day-001.txt']},
        'strategy': {'source': 'template', 'template_id': 'sma_cross',
                     'batches': [{'name': 'a', 'params': {'fast': 5}},
                                 {'name': 'b', 'params': {'fast': 10}}]},
        'broker': {'cash': 100000}, 'sizer': {'type': 'percent', 'value': 90}})
    assert r.status_code == 200

    # 合法请求（多数据 list 形式）能拿到 task_id（排队但不真正等待执行）
    r = client.post('/api/run', json={
        'mode': 'backtest',
        'data': {'path': ['datas/2006-day-001.txt']},
        'strategy': {'source': 'template', 'template_id': 'sma_cross',
                     'params': {'fast': 10, 'slow': 30}},
        'broker': {'cash': 100000},
        'sizer': {'type': 'percent', 'value': 90}})
    assert r.status_code == 200
    assert 'task_id' in r.json()


def test_task_not_found():
    r = client.get('/api/task/not-exist-id')
    assert r.status_code == 404


# ------------------------------------------------ 历史任务批量删除

def _mk_task(tasks_dir, tid, status='done'):
    import pathlib
    d = pathlib.Path(tasks_dir) / tid
    d.mkdir(parents=True, exist_ok=True)
    (d / 'status').write_text(status)
    (d / 'request.json').write_text('{}')
    return str(d)


def test_tasks_batch_delete_selected(tmp_path, monkeypatch):
    import webapp.server as srv
    monkeypatch.setattr(srv, 'TASKS_DIR', str(tmp_path))
    _mk_task(tmp_path, '20260101-100000-aaaa')
    _mk_task(tmp_path, '20260101-100001-bbbb')
    _mk_task(tmp_path, '20260101-100002-cccc')

    r = client.post('/api/tasks/batch-delete',
                    json={'ids': ['20260101-100000-aaaa', '20260101-100001-bbbb']})
    assert r.status_code == 200
    body = r.json()
    assert len(body['deleted']) == 2
    assert not (tmp_path / '20260101-100000-aaaa').exists()
    assert (tmp_path / '20260101-100002-cccc').exists()  # 未选中的保留


def test_tasks_batch_delete_blocks_running(tmp_path, monkeypatch):
    import webapp.server as srv
    monkeypatch.setattr(srv, 'TASKS_DIR', str(tmp_path))
    _mk_task(tmp_path, '20260101-100000-run1', status='running')
    _mk_task(tmp_path, '20260101-100001-done')
    monkeypatch.setattr(srv.manager, 'current', '20260101-100000-run1')

    r = client.post('/api/tasks/batch-delete',
                    json={'ids': ['20260101-100000-run1', '20260101-100001-done']})
    body = r.json()
    assert body['blocked'] == ['20260101-100000-run1']
    assert '20260101-100001-done' in body['deleted']
    assert (tmp_path / '20260101-100000-run1').exists()


def test_tasks_batch_delete_all_completed(tmp_path, monkeypatch):
    import webapp.server as srv
    monkeypatch.setattr(srv, 'TASKS_DIR', str(tmp_path))
    _mk_task(tmp_path, '20260101-100000-d1', status='done')
    _mk_task(tmp_path, '20260101-100001-e1', status='error')
    _mk_task(tmp_path, '20260101-100002-run', status='running')
    monkeypatch.setattr(srv.manager, 'current', '20260101-100002-run')

    r = client.post('/api/tasks/batch-delete', json={'all_completed': True})
    body = r.json()
    assert set(body['deleted']) == {'20260101-100000-d1', '20260101-100001-e1'}
    assert (tmp_path / '20260101-100002-run').exists()  # 运行中不动


def test_tasks_batch_delete_invalid_id(tmp_path, monkeypatch):
    import webapp.server as srv
    monkeypatch.setattr(srv, 'TASKS_DIR', str(tmp_path))
    r = client.post('/api/tasks/batch-delete', json={'ids': ['../etc/passwd']})
    body = r.json()
    assert body['deleted'] == [] and 'invalid' not in body['missing'][0]
    assert len(body['missing']) == 1
