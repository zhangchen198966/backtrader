# -*- coding: utf-8 -*-
"""模板存储与市场模块测试（网络部分全部 mock，离线可跑）"""
import json
import os

import pytest

from webapp.strategy_market import (MarketError, catalog, extract_strategy,
                                    find_market, import_from_market)
from webapp.templatestore import (TemplateError, add_custom, delete_custom,
                                  find_custom, list_custom,
                                  parse_params_metadata,
                                  validate_strategy_code)

GOOD_CODE = """\
import backtrader as bt


class MyStrategy(bt.Strategy):
    params = dict(fast=10, slow=30, ratio=0.5)

    def __init__(self):
        self.sma = bt.ind.SMA(period=self.p.fast)

    def next(self):
        pass
"""

TUPLE_CODE = """\
import backtrader as bt


class TupleStrategy(bt.Strategy):
    params = (
        ('macd1', 12),
        ('macd2', 26),
        ('atrdist', 3.0),
    )

    def next(self):
        pass
"""


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """每个测试用独立存储文件，互不污染"""
    import webapp.templatestore as ts
    monkeypatch.setattr(ts, 'STORE_PATH', str(tmp_path / 'tpl.json'))
    yield ts


# ------------------------------------------------ 校验

def test_validate_ok():
    names = validate_strategy_code(GOOD_CODE)
    assert names == ['MyStrategy']


def test_validate_syntax_error():
    with pytest.raises(TemplateError, match='语法错误'):
        validate_strategy_code('class Broken(bt.Strategy)\n')


def test_validate_no_strategy():
    with pytest.raises(TemplateError, match='未找到'):
        validate_strategy_code('x = 1\n')
    with pytest.raises(TemplateError, match='为空'):
        validate_strategy_code('')


def test_validate_exec_error():
    with pytest.raises(TemplateError, match='执行错误'):
        validate_strategy_code('import backtrader as bt\nraise ValueError("boom")\n')


# ------------------------------------------------ 参数元数据解析

def test_parse_params_dict_form():
    metas = parse_params_metadata(GOOD_CODE)
    by_name = {m['name']: m for m in metas}
    assert set(by_name) == {'fast', 'slow', 'ratio'}
    assert by_name['fast']['int'] is True and by_name['fast']['default'] == 10
    assert by_name['ratio']['int'] is False and by_name['ratio']['default'] == 0.5


def test_parse_params_tuple_form():
    metas = parse_params_metadata(TUPLE_CODE)
    by_name = {m['name']: m for m in metas}
    assert by_name['macd1']['default'] == 12 and by_name['macd1']['int'] is True
    assert by_name['atrdist']['default'] == 3.0 and by_name['atrdist']['int'] is False


def test_parse_params_base_class_attribute_form():
    """class X(bt.Strategy) 的 Attribute 基类也能识别（曾出过 bug）"""
    metas = parse_params_metadata(GOOD_CODE)
    assert metas, 'bt.Strategy 基类形式必须能解析到 params'


def test_parse_params_none_or_bad():
    assert parse_params_metadata('class A(bt.Strategy):\n    pass\n') == []
    assert parse_params_metadata('not code') == []


# ------------------------------------------------ 存储增删查

def test_add_and_find(isolated_store):
    e = add_custom('我的模板', GOOD_CODE)
    assert e['id'].startswith('custom-')
    assert find_custom(e['id'])['name'] == '我的模板'
    assert len(list_custom()) == 1
    # 参数元数据自动生成
    assert {m['name'] for m in find_custom(e['id'])['params']} == {'fast', 'slow', 'ratio'}


def test_add_same_name_overwrites(isolated_store):
    add_custom('同名', GOOD_CODE)
    add_custom('同名', TUPLE_CODE)
    entries = list_custom()
    assert len(entries) == 1
    assert {m['name'] for m in entries[0]['params']} == {'macd1', 'macd2', 'atrdist'}


def test_add_validates(isolated_store):
    with pytest.raises(TemplateError):
        add_custom('坏的', 'no strategy here')
    assert list_custom() == []
    with pytest.raises(TemplateError, match='名称'):
        add_custom('', GOOD_CODE)


def test_delete(isolated_store):
    e = add_custom('待删', GOOD_CODE)
    assert delete_custom(e['id']) is True
    assert delete_custom(e['id']) is False
    assert list_custom() == []


# ------------------------------------------------ 市场目录与抽取

def test_catalog_filter():
    assert len(catalog()) >= 7
    r = catalog('MACD')
    assert any(m['id'] == 'mk-macd-settings' for m in r)
    assert catalog('不存在的关键词xyz') == []
    assert find_market('mk-psar')['class'] == 'St'
    assert find_market('nope') is None


def test_catalog_entries_have_fields():
    for m in catalog():
        for k in ('id', 'name', 'desc', 'tags', 'repo', 'path', 'class'):
            assert m.get(k), f"{m['id']} 缺字段 {k}"


def test_extract_strategy_success():
    code = extract_strategy(TUPLE_CODE, 'TupleStrategy')
    assert 'class TupleStrategy' in code
    assert 'import backtrader as bt' in code
    validate_strategy_code(code)  # 不抛即通过


def test_extract_with_local_base_class():
    """目标类继承文件内定义的基类时，基类一并抽取"""
    src = GOOD_CODE + """

class Child(MyStrategy):
    params = dict(k=1)

    def next(self):
        super().next()
"""
    code = extract_strategy(src, 'Child')
    assert 'class Child' in code and 'class MyStrategy' in code
    validate_strategy_code(code)


def test_extract_missing_class():
    with pytest.raises(MarketError, match='未找到策略类'):
        extract_strategy('import backtrader as bt\n', 'Nope')


def test_import_from_market_offline(monkeypatch, isolated_store):
    """mock 下载层：完整导入流程（下载→抽取→校验→入库）离线可测"""
    import webapp.strategy_market as sm
    # mk-stoptrail 的目标类名是 St，mock 源码需同名
    st_code = """import backtrader as bt


class St(bt.Strategy):
    params = (('rsi_per', 14), ('rsi_upper', 65.0))

    def next(self):
        pass
"""
    monkeypatch.setattr(sm, 'download_source', lambda repo, path: st_code)
    entry = import_from_market('mk-stoptrail', name_override='离线导入测试')
    assert entry['name'] == '离线导入测试'
    assert '市场导入' in entry['source']
    assert find_custom(entry['id']) is not None


def test_import_from_market_network_failure(monkeypatch, isolated_store):
    import webapp.strategy_market as sm

    def boom(repo, path):
        raise MarketError('下载失败（可稍后重试）: all cdns down')
    monkeypatch.setattr(sm, 'download_source', boom)
    with pytest.raises(MarketError, match='下载失败'):
        import_from_market('mk-stoptrail')
    assert list_custom() == []  # 失败不入库


def test_import_unknown_market_id(isolated_store):
    with pytest.raises(MarketError, match='未知市场模板'):
        import_from_market('mk-nope')


# ------------------------------------------------ 文章型数据源

ARTICLE_HTML = """<html><body>
<p>intro</p>
<pre><code>import backtrader as bt


class BlogStrategy(bt.Strategy):
    params = dict(fast=10, slow=30)

    def __init__(self):
        self.cross = bt.ind.CrossOver(bt.ind.SMA(period=self.p.fast),
                                      bt.ind.SMA(period=self.p.slow))

    def next(self):
        if not self.position and self.cross[0] > 0:
            self.buy()</code></pre>
<pre><code>{ css: not python }</code></pre>
<pre><code>print('fragment without class')</code></pre>
</body></html>"""


def test_extract_code_blocks_filters_python():
    from webapp.strategy_market import extract_code_blocks
    code = extract_code_blocks(ARTICLE_HTML)
    assert 'class BlogStrategy' in code
    assert 'css' not in code            # 非代码块被过滤
    assert "print('fragment" not in code  # 无 class 特征的片段被过滤
    validate_strategy_code(code)         # 拼接结果可直接过校验


def test_extract_code_blocks_empty():
    from webapp.strategy_market import extract_code_blocks
    assert extract_code_blocks('<html><p>no code</p></html>') == ''
    assert extract_code_blocks('<pre><code>x = 1</code></pre>') == ''


def test_download_article_invalid(tmp_path, monkeypatch):
    import webapp.strategy_market as sm
    # 网页无策略代码
    monkeypatch.setattr(sm, 'urllib.request.urlopen',
                        lambda *a, **k: None) if False else None
    import urllib.request as _ur

    class FakeResp:
        def __init__(self, data):
            self._d = data.encode()
        def read(self):
            return self._d
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(sm.urllib.request, 'urlopen',
                        lambda req, timeout=30: FakeResp('<html>empty</html>'))
    with pytest.raises(MarketError, match='未找到'):
        sm.download_article('https://example.com/x')


def test_download_article_success(monkeypatch):
    import webapp.strategy_market as sm
    import urllib.request as _ur

    class FakeResp:
        def read(self):
            return ARTICLE_HTML.encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(sm.urllib.request, 'urlopen',
                        lambda req, timeout=30: FakeResp())
    code = sm.download_article('https://example.com/strategy')
    assert 'class BlogStrategy' in code


def test_import_market_article_entry(monkeypatch, isolated_store):
    """文章型条目：无 repo/url 下载 → 代码块 → 类名自动识别 → 入库"""
    import webapp.strategy_market as sm
    monkeypatch.setattr(sm, 'MARKET', [
        {'id': 'mk-blog-test', 'name': '博客测试策略', 'desc': 'd', 'tags': 't',
         'provider': 'community', 'site': 'article',
         'url': 'https://example.com/strategy'}])
    import urllib.request as _ur

    class FakeResp:
        def read(self):
            return ARTICLE_HTML.encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(sm.urllib.request, 'urlopen',
                        lambda req, timeout=30: FakeResp())
    entry = sm.import_from_market('mk-blog-test')
    assert entry['name'] == '博客测试策略'
    assert 'BlogStrategy' in entry['code']
