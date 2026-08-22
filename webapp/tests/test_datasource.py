# -*- coding: utf-8 -*-
"""在线数据源测试：清洗函数（纯逻辑） + fetch_to_csv 校验 + API（mock 抓取）"""
import json
import os

import pytest
from fastapi.testclient import TestClient

from webapp.datasource import (FetchError, PROVIDERS, fetch_to_csv,
                               normalize_ohlc_rows, rows_to_csv)

from webapp.tests.conftest import REPO_ROOT  # noqa: F401


# ------------------------------------------------ normalize_ohlc_rows

AK_KEYS = {'date': '日期', 'open': '开盘', 'high': '最高',
           'low': '最低', 'close': '收盘', 'volume': '成交量'}


def test_normalize_akshare_chinese_columns():
    """AkShare A股的中文列名映射 + 数值化 + 升序"""
    rows = [
        {'日期': '2024-01-03', '开盘': '10.5', '最高': '11', '最低': '10', '收盘': '10.8', '成交量': '200'},
        {'日期': '2024-01-02', '开盘': '10', '最高': '10.5', '最低': '9.8', '收盘': '10.2', '成交量': '100'},
    ]
    out = normalize_ohlc_rows(rows, AK_KEYS)
    assert len(out) == 2
    assert out[0][0] == '2024-01-02'  # 升序
    assert out[0][1] == 10.0 and out[1][2] == 11.0
    assert out[0][-1] == 0  # OpenInterest 固定 0


def test_normalize_skips_incomplete_rows():
    """缺失关键字段的行被剔除，不抛错"""
    rows = [
        {'日期': '2024-01-02', '开盘': '10', '最高': '11', '最低': '9', '收盘': '10'},   # 缺 volume → volume=0 保留
        {'日期': '2024-01-03', '开盘': None, '最高': '11', '最低': '9', '收盘': '10'},    # 缺 open → 剔除
        {'日期': 'bad-date', '开盘': '10', '最高': '11', '最低': '9', '收盘': '10'},      # 坏日期 → 剔除
        {'日期': '', '开盘': '10', '最高': '11', '最低': '9', '收盘': '10'},              # 空日期 → 剔除
    ]
    out = normalize_ohlc_rows(rows, AK_KEYS)
    assert len(out) == 1
    assert out[0][5] == 0


def test_normalize_dedup_and_date_formats():
    """同日去重 + 多种日期输入形态（datetime对象/20240102/斜杠）"""
    from datetime import datetime
    rows = [
        {'日期': datetime(2024, 1, 2), '开盘': 10, '最高': 11, '最低': 9, '收盘': 10, '成交量': 5},
        {'日期': '2024-01-02', '开盘': 99, '最高': 99, '最低': 99, '收盘': 99, '成交量': 9},  # 重复日 → 剔除
        {'日期': '2024/01/03', '开盘': 10, '最高': 11, '最低': 9, '收盘': 10, '成交量': 5},
        {'日期': '20240104', '开盘': 10, '最高': 11, '最低': 9, '收盘': 10, '成交量': 5},
    ]
    out = normalize_ohlc_rows(rows, AK_KEYS)
    assert [r[0] for r in out] == ['2024-01-02', '2024-01-03', '2024-01-04']


def test_rows_to_csv_format():
    out = normalize_ohlc_rows(
        [{'日期': '2024-01-02', '开盘': 10, '最高': 11, '最低': 9, '收盘': 10, '成交量': 5}], AK_KEYS)
    csv_text = rows_to_csv(out)
    lines = csv_text.strip().split('\n')
    assert lines[0] == 'Date,Open,High,Low,Close,Volume,OpenInterest'
    assert lines[1] == '2024-01-02,10.0,11.0,9.0,10.0,5.0,0'


# ------------------------------------------------ fetch_to_csv 参数校验（不触网）

def test_fetch_validates_provider():
    with pytest.raises(FetchError, match='未知数据源'):
        fetch_to_csv('nope', '600519', '2024-01-01', '2024-12-31', '/tmp/x')


@pytest.mark.parametrize('provider,symbol,msg', [
    ('akshare-a', '60051', '6 位'),
    ('akshare-a', '600519.SH', '6 位'),
    ('akshare-index', '000001', 'sh000001'),
    ('akshare-index', '600519', 'sh000001'),
    ('yfinance', 'AA PP', 'Yahoo'),
])
def test_fetch_validates_symbol(provider, symbol, msg):
    with pytest.raises(FetchError, match=msg):
        fetch_to_csv(provider, symbol, '2024-01-01', '2024-12-31', '/tmp/x')


@pytest.mark.parametrize('start,end,msg', [
    ('2024/01/01', '2024-12-31', '日期格式'),
    ('2024-12-31', '2024-01-01', '早于'),
])
def test_fetch_validates_dates(start, end, msg):
    with pytest.raises(FetchError, match=msg):
        fetch_to_csv('akshare-a', '600519', start, end, '/tmp/x')


def test_fetch_rejects_too_few_rows(tmp_path, monkeypatch):
    """不足 20 行报错（避免空数据落盘）"""
    import webapp.datasource as ds
    monkeypatch.setattr(ds, 'FETCHERS',
                        {'akshare-a': lambda *a: [[f'2024-01-{i:02d}', 1, 1, 1, 1, 0, 0]
                                                  for i in range(1, 11)]})
    with pytest.raises(FetchError, match='不足 20'):
        ds.fetch_to_csv('akshare-a', '600519', '2024-01-01', '2024-12-31',
                        str(tmp_path))


def test_fetch_happy_path_writes_standard_csv(tmp_path, monkeypatch):
    """mock 抓取 → 落盘标准 CSV → 探测器可直接识别"""
    import webapp.datasource as ds
    fake = [{'日期': f'2024-01-{d:02d}', '开盘': 10 + d, '最高': 11 + d,
             '最低': 9 + d, '收盘': 10.5 + d, '成交量': 100 * d}
            for d in range(1, 26)]  # 25 行
    monkeypatch.setattr(ds, 'FETCHERS', {'akshare-a': lambda *a: ds.normalize_ohlc_rows(fake, AK_KEYS)})
    r = ds.fetch_to_csv('akshare-a', '600519', '2024-01-01', '2024-12-31',
                        str(tmp_path))
    assert r['rows'] == 25
    from webapp.datainspect import inspect_csv
    info = inspect_csv(r['path'])
    assert info['ok'], info['error']
    assert info['dtformat'] == '%Y-%m-%d'
    assert info['rows'] == 25


# ------------------------------------------------ API（mock 网络）

client = TestClient(app := __import__('webapp.server', fromlist=['app']).app)


def test_providers_api():
    r = client.get('/api/datas/providers')
    assert r.status_code == 200
    ps = {p['id'] for p in r.json()['providers']}
    assert ps == {'akshare-a', 'akshare-index', 'yfinance'}


def test_fetch_api_validation_400():
    r = client.post('/api/datas/fetch', json={
        'provider': 'akshare-a', 'symbol': 'abc',
        'start': '2024-01-01', 'end': '2024-12-31'})
    assert r.status_code == 400
    assert '6 位' in r.json()['detail']


def test_fetch_api_bad_provider_400():
    r = client.post('/api/datas/fetch', json={
        'provider': 'nope', 'symbol': '600519',
        'start': '2024-01-01', 'end': '2024-12-31'})
    assert r.status_code == 400
    assert '未知数据源' in r.json()['detail']


# ------------------------------------------------ 股票名称搜索

SEED_LIST = [
    {'code': '600519', 'name': '贵州茅台'},
    {'code': '000858', 'name': '五粮液'},
    {'code': '300750', 'name': '宁德时代'},
    {'code': '000002', 'name': '万  科Ａ'},   # 名称含全角/空格
    {'code': '601127', 'name': '赛力斯'},
    {'code': '600036', 'name': '招商银行'},
    {'code': '000001', 'name': '平安银行'},
]


@pytest.fixture()
def seeded_list(monkeypatch):
    import webapp.datasource as ds
    monkeypatch.setattr(ds, '_stock_list', SEED_LIST)
    return ds


def test_search_by_chinese_name(seeded_list):
    r = seeded_list.search_stocks('茅台')
    assert r and r[0]['code'] == '600519' and r[0]['name'] == '贵州茅台'


def test_search_name_normalizes_spaces(seeded_list):
    """'万科A' 能匹配 '万  科Ａ'"""
    r = seeded_list.search_stocks('万科')
    assert any(x['code'] == '000002' for x in r)


def test_search_code_prefix_ranked_first(seeded_list):
    r = seeded_list.search_stocks('600')
    assert r[0]['code'] == '600036'
    assert all(x['code'].startswith('600') for x in r)
    assert r[0]['code'] < r[-1]['code'] or len(r) <= 1


def test_search_empty_query(seeded_list):
    assert seeded_list.search_stocks('') == []
    assert seeded_list.search_stocks('   ') == []


def test_search_no_match(seeded_list):
    assert seeded_list.search_stocks('不存在的公司xyz') == []


def test_search_indexes_static():
    from webapp.datasource import search_indexes
    r = search_indexes('上证')
    assert any(x['code'] == 'sh000001' for x in r)
    r = search_indexes('sh000')
    assert any(x['code'] == 'sh000001' for x in r)
    assert search_indexes('不存在的') == []
    # 空查询返回前几个
    assert len(search_indexes('')) > 0


def test_stock_list_disk_cache(tmp_path, monkeypatch):
    """磁盘缓存命中时不触网；过期后重新拉取"""
    import webapp.datasource as ds
    cache = tmp_path / 'stock_list.json'
    cache.write_text(json.dumps(SEED_LIST, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(ds, 'STOCK_LIST_CACHE', str(cache))
    monkeypatch.setattr(ds, '_stock_list', None)

    lst = ds.get_stock_list()  # 走磁盘缓存，不触网
    assert lst == SEED_LIST

    # 缓存过期 → 走网络（mock 掉 akshare）
    old = os.path.getmtime(str(cache)) - ds.STOCK_LIST_TTL - 10
    os.utime(str(cache), (old, old))
    import types as _types
    fake_ak = _types.SimpleNamespace(
        stock_info_a_code_name=lambda: __import__('pandas').DataFrame(SEED_LIST))
    monkeypatch.setitem(__import__('sys').modules, 'akshare', fake_ak)
    lst2 = ds.get_stock_list()
    assert lst2 == SEED_LIST


def test_search_api(seeded_list, monkeypatch):
    import webapp.server as srv
    monkeypatch.setattr(srv, 'search_stocks', seeded_list.search_stocks)
    r = client.get('/api/datas/search', params={'q': '茅台', 'provider': 'akshare-a'})
    assert r.status_code == 200
    res = r.json()['results']
    assert res and res[0]['code'] == '600519'

    r = client.get('/api/datas/search', params={'q': '上证', 'provider': 'akshare-index'})
    res = r.json()['results']
    assert any(x['code'] == 'sh000001' for x in res)

    r = client.get('/api/datas/search', params={'q': 'AAPL', 'provider': 'yfinance'})
    assert r.json()['results'][0]['code'] == 'AAPL'


def test_search_api_graceful_on_load_failure(monkeypatch):
    import webapp.server as srv
    def boom(q, limit=15):
        raise RuntimeError('清单拉取超时')
    monkeypatch.setattr(srv, 'search_stocks', boom)
    r = client.get('/api/datas/search', params={'q': '茅台', 'provider': 'akshare-a'})
    assert r.status_code == 200
    body = r.json()
    assert body['results'] == []
    assert '清单暂不可用' in body['error']


def test_fetch_filename_includes_stock_name(tmp_path, monkeypatch):
    """拉取落盘的文件名包含股票中文名"""
    import webapp.datasource as ds
    fake = [{'日期': f'2024-01-{d:02d}', '开盘': 10 + d, '最高': 11 + d,
             '最低': 9 + d, '收盘': 10.5 + d, '成交量': 100 * d}
            for d in range(1, 26)]
    monkeypatch.setattr(ds, 'FETCHERS',
                        {'akshare-a': lambda *a: ds.normalize_ohlc_rows(fake, AK_KEYS)})
    monkeypatch.setattr(ds, '_stock_list',
                        [{'code': '600519', 'name': '贵州 茅台'}])
    r = ds.fetch_to_csv('akshare-a', '600519', '2024-01-01', '2024-12-31',
                        str(tmp_path))
    assert '600519' in os.path.basename(r['path'])
    assert '贵州茅台' in os.path.basename(r['path']), \
        '文件名应含清洗后的股票名（去空格）'

    # 指数源同样带名称（静态表）
    monkeypatch.setattr(ds, 'FETCHERS',
                        {'akshare-index': lambda *a: ds.normalize_ohlc_rows(
                            [{'date': f'2024-01-{d:02d}', 'open': 10, 'high': 11,
                              'low': 9, 'close': 10, 'volume': 1}
                             for d in range(1, 26)],
                            {'date': 'date', 'open': 'open', 'high': 'high',
                             'low': 'low', 'close': 'close', 'volume': 'volume'})})
    r2 = ds.fetch_to_csv('akshare-index', 'sh000001', '2024-01-01',
                         '2024-12-31', str(tmp_path))
    assert '上证指数' in os.path.basename(r2['path'])


def test_lookup_symbol_name_graceful(monkeypatch):
    import webapp.datasource as ds
    monkeypatch.setattr(ds, '_stock_list', [{'code': '600519', 'name': '贵州茅台'}])
    assert ds.lookup_symbol_name('akshare-a', '600519') == '贵州茅台'
    assert ds.lookup_symbol_name('akshare-a', '999999') is None
    assert ds.lookup_symbol_name('akshare-index', 'sh000001') == '上证指数'
    assert ds.lookup_symbol_name('yfinance', 'AAPL') is None
    # 清单加载失败不抛错
    def boom():
        raise RuntimeError('x')
    monkeypatch.setattr(ds, '_stock_list', None)
    monkeypatch.setattr(ds, 'get_stock_list', boom)
    assert ds.lookup_symbol_name('akshare-a', '600519') is None


# ------------------------------------------------ 数据文件删除 API

def test_delete_upload_file(tmp_path):
    """上传文件可删除：存在 → 200 且文件消失"""
    import webapp.server as srv
    uploads = os.path.join(srv.UPLOADS_DIR)
    fname = 'zz-api-delete-test.csv'
    fpath = os.path.join(uploads, fname)
    with open(fpath, 'w') as f:
        f.write('Date,Open,High,Low,Close,Volume,OpenInterest\n')
        for d in range(1, 25):
            f.write(f'2024-01-{d:02d},10,11,9,10,100,0\n')
    rel = os.path.relpath(fpath, srv.REPO_ROOT)
    r = client.delete('/api/datas', params={'path': rel})
    assert r.status_code == 200
    assert not os.path.exists(fpath)


def test_delete_forbids_builtin_datas():
    """框架自带数据（datas/）不允许删除"""
    r = client.delete('/api/datas', params={'path': 'datas/2006-day-001.txt'})
    assert r.status_code == 403
    # 路径穿越也不行
    r = client.delete('/api/datas', params={'path': '../webapp/server.py'})
    assert r.status_code == 403


def test_delete_missing_file_404():
    r = client.delete('/api/datas', params={'path': 'webapp/uploads/no-such.csv'})
    assert r.status_code == 404


def test_fetch_api_network_error_message(monkeypatch):
    """抓取异常 → 400 且提示网络"""
    import webapp.server as srv
    import webapp.datasource as ds

    def boom(*a):
        raise RuntimeError('connection reset')
    monkeypatch.setattr(ds, 'FETCHERS', {'akshare-a': boom})
    monkeypatch.setattr(srv, 'fetch_to_csv', ds.fetch_to_csv)
    r = client.post('/api/datas/fetch', json={
        'provider': 'akshare-a', 'symbol': '600519',
        'start': '2024-01-01', 'end': '2024-12-31'})
    assert r.status_code == 400
    assert '网络' in r.json()['detail'] or '抓取失败' in r.json()['detail']


# ------------------------------------------------ Yahoo 代码搜索

def test_search_yahoo_by_chinese_name():
    from webapp.datasource import search_yahoo
    r = search_yahoo('苹果')
    assert r and r[0]['code'] == 'AAPL'
    r = search_yahoo('腾讯')
    codes = {x['code'] for x in r}
    assert '00700.HK' in codes and 'TCEHY' in codes


def test_search_yahoo_by_english():
    from webapp.datasource import search_yahoo
    # 英文名包含（大小写不敏感）
    assert search_yahoo('apple')[0]['code'] == 'AAPL'
    assert any(x['code'] == 'TSLA' for x in search_yahoo('tesla'))
    # 代码前缀（大小写不敏感）优先
    assert search_yahoo('aapl')[0]['code'] == 'AAPL'
    assert search_yahoo('007')[0]['code'] == '00700.HK'
    # 港股后缀
    assert any(x['code'].endswith('.HK') for x in search_yahoo('hk')[:6])


def test_search_yahoo_edges():
    from webapp.datasource import search_yahoo
    assert search_yahoo('') != []           # 空查询返回推荐列表
    assert search_yahoo('不存在的xyzabc') == []


def test_search_yahoo_table_quality():
    from webapp.datasource import YAHOO_SYMBOLS
    assert len(YAHOO_SYMBOLS) >= 45
    codes = [x['code'] for x in YAHOO_SYMBOLS]
    assert len(codes) == len(set(codes)), '代码不得重复'
    for x in YAHOO_SYMBOLS:
        assert x['code'] and x['name'] and x['en']


def test_search_api_yahoo_provider():
    import webapp.server as srv
    from webapp.datasource import search_yahoo
    monkey = None
    r = client.get('/api/datas/search',
                   params={'q': '苹果', 'provider': 'yfinance'})
    assert r.status_code == 200
    res = r.json()['results']
    assert res and res[0]['code'] == 'AAPL'
