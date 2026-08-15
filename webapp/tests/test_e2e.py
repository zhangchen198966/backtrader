# -*- coding: utf-8 -*-
"""端到端测试（Playwright + Chromium 无头浏览器）

逐项验证 5 个目标在真实浏览器中的行为：
  1. 亮/暗主题切换（含持久化）
  2. 图表由浏览器组件（ECharts canvas）渲染
  3. 专业名词点击弹出 tips
  4. 优化结果出图 + 多数据对比回测
  5. 自定义代码语法高亮
"""
import re
import time

import pytest
from playwright.sync_api import sync_playwright, expect

from webapp.tests.conftest import simple_backtest_request  # noqa: F401

DATA1 = 'datas/2006-day-001.txt'
DATA2 = 'datas/nvda-2014.txt'

pytestmark = pytest.mark.e2e


def wait_done(page, timeout=120):
    """等待任务完成：卡片/优化图/错误框任一可见（:visible 防止锚定隐藏元素）"""
    page.wait_for_selector('.card:visible, #optChart:visible, #errorBox:visible',
                           timeout=timeout * 1000)


@pytest.fixture(scope='module')
def pw():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-proxy-server'])
        yield browser
        browser.close()


@pytest.fixture
def page(pw, live_server):
    ctx = pw.new_context(viewport={'width': 1440, 'height': 1000})
    page = ctx.new_page()
    page.goto(live_server)
    page.wait_for_selector('#dataList input', timeout=15000)
    yield page
    ctx.close()


def uncheck_all_data(page):
    for cb in page.query_selector_all('.dataChk'):
        if cb.is_checked():
            cb.click()


def check_data(page, value):
    cb = page.query_selector(f'.dataChk[value="{value}"]')
    assert cb is not None, f'数据选项不存在: {value}'
    if not cb.is_checked():
        cb.click()


# ---------------------------------------------------------------- 目标1：主题

def test_theme_toggle_and_persist(page):
    html = page.locator('html')
    expect(html).to_have_attribute('data-theme', 'dark')
    page.wait_for_timeout(400)  # 等初始过渡结束

    body_bg_dark = page.evaluate(
        'getComputedStyle(document.body).backgroundColor')
    page.click('#themeBtn')
    expect(html).to_have_attribute('data-theme', 'light')
    page.wait_for_timeout(400)  # 等 background 过渡（.25s）结束再取色

    body_bg_light = page.evaluate(
        'getComputedStyle(document.body).backgroundColor')
    assert body_bg_dark != body_bg_light, '切换主题后背景色应改变'

    # 持久化到 localStorage
    saved = page.evaluate('localStorage.getItem("btlab-theme")')
    assert saved == 'light'

    # 刷新后保持亮色
    page.reload()
    page.wait_for_selector('#dataList input', timeout=15000)
    expect(page.locator('html')).to_have_attribute('data-theme', 'light')
    assert page.evaluate('getComputedStyle(document.body).backgroundColor') \
        == body_bg_light

    # 切回暗色（不污染后续测试）
    page.click('#themeBtn')
    expect(html).to_have_attribute('data-theme', 'dark')


# ---------------------------------------------------------------- 目标3：术语 tips

def test_term_click_shows_tooltip(page):
    # 等模板与术语表加载（参数标签里就有术语）
    page.wait_for_selector('.term', timeout=15000)
    first_term = page.locator('.term').first
    term_text = first_term.inner_text()

    first_term.click()
    tip = page.locator('#tipbox')
    expect(tip).to_be_visible()
    assert tip.locator('.tip-title').inner_text() == term_text
    assert len(tip.locator('.tip-body').inner_text()) >= 8

    # 点空白处关闭
    page.locator('header h1').click(force=True)
    expect(tip).not_to_be_visible()


# ---------------------------------------------------------------- 目标5：语法高亮

def test_editor_syntax_highlight(page):
    page.click('input[name=stratSrc][value=custom]')
    page.wait_for_selector('#editor:visible', timeout=5000)
    page.click('#loadTplBtn')  # 载入模板代码（触发高亮渲染）

    hl = page.locator('#hl')
    expect(hl.locator('span.tk-kw').first).to_be_visible(timeout=5000)
    assert hl.locator('span.tk-kw').count() >= 3   # class/def/import...
    assert hl.locator('span.tk-bi').count() >= 2   # self/bt
    assert 'class' in hl.inner_text()

    # 编辑代码 → 高亮同步更新
    page.fill('#editor', 'x = 1  # 注释\n')
    assert page.locator('#hl span.tk-com').count() == 1
    assert page.locator('#hl span.tk-num').count() == 1


# ---------------------------------------------------------------- 目标2：ECharts 回测图

def test_backtest_renders_echarts_canvas(page):
    uncheck_all_data(page)
    check_data(page, DATA1)
    page.select_option('#tplSel', 'sma_rsi_atr')
    page.click('#runBtn')

    wait_done(page)
    assert page.locator('#errorBox').is_hidden()

    # 绩效卡片 + 术语包装在结果中也生效
    assert page.locator('.card').count() == 12
    assert page.locator('.card .term').count() >= 1

    # 图表是浏览器组件渲染（canvas），且不再是 matplotlib SVG
    chart_div = page.locator('#kchart0')
    expect(chart_div).to_be_visible()
    assert chart_div.locator('canvas').count() >= 1
    box = chart_div.bounding_box()
    assert box and box['width'] > 400 and box['height'] > 400
    assert page.locator('#resultArea svg').count() == 0, '不应再有 SVG 位图'

    # 交易明细
    assert page.locator('.tablewrap tbody tr').count() >= 1
    # ECharts 实例真实存在且已挂载
    assert page.evaluate(
        'echarts.getInstanceByDom(document.getElementById("kchart0")) !== undefined')


# ---------------------------------------------------------------- 目标4a：优化出图

def test_optimize_renders_chart_and_table(page):
    uncheck_all_data(page)
    check_data(page, DATA1)
    page.select_option('#tplSel', 'sma_cross')
    page.check('#gridMode')
    page.fill('input[data-gname="fast"][data-gpart="start"]', '5')
    page.fill('input[data-gname="fast"][data-gpart="end"]', '9')
    page.fill('input[data-gname="fast"][data-gpart="step"]', '2')
    page.fill('input[data-gname="slow"][data-gpart="start"]', '20')
    page.fill('input[data-gname="slow"][data-gpart="end"]', '30')
    page.fill('input[data-gname="slow"][data-gpart="step"]', '10')

    page.click('#optBtn')
    wait_done(page)
    assert page.locator('#errorBox').is_hidden()

    # 双参数网格 → 热力图（canvas 渲染）
    opt = page.locator('#optChart')
    expect(opt).to_be_visible()
    assert opt.locator('canvas').count() >= 1
    # 结果表格 6 行
    assert page.locator('#optTable tbody tr').count() == 6

    # 点击行 → 参数回填 & 退出网格模式
    page.locator('#optTable tbody tr').first.click()
    assert not page.locator('#gridMode').is_checked()
    fast_val = page.locator('input[data-pname="fast"]').input_value()
    slow_val = page.locator('input[data-pname="slow"]').input_value()
    assert fast_val == '9' and slow_val == '20'


# ---------------------------------------------------------------- 目标4b：多数据对比

def test_multi_data_comparison(page):
    uncheck_all_data(page)
    check_data(page, DATA1)
    check_data(page, DATA2)
    page.select_option('#tplSel', 'sma_rsi_atr')
    page.click('#runBtn')

    wait_done(page, timeout=180)
    assert page.locator('#errorBox').is_hidden()

    # 对比权益曲线图（canvas）
    cmp_chart = page.locator('#cmpChart')
    expect(cmp_chart).to_be_visible()
    assert cmp_chart.locator('canvas').count() >= 1

    # 对比摘要表 2 行、两个数据名都在
    rows = page.locator('.tablewrap tbody tr')
    assert rows.count() >= 2
    table_text = page.locator('#resultArea').inner_text()
    assert '2006-day-001' in table_text and 'nvda-2014' in table_text

    # 每个数据各有一张 K 线图
    assert page.locator('#kchart0').count() == 1
    assert page.locator('#kchart1').count() == 1
    assert page.locator('#kchart0 canvas').count() >= 1
    assert page.locator('#kchart1 canvas').count() >= 1

    # 绩效卡片按数据分块（2 × 12）
    assert page.locator('.card').count() == 24


def test_kline_yaxis_rescales_on_zoom(page):
    """缩放后 Y 轴按可见窗口自适应（看得出局部差异）"""
    uncheck_all_data(page)
    check_data(page, DATA1)
    page.select_option('#tplSel', 'sma_rsi_atr')
    page.click('#runBtn')
    wait_done(page)
    assert page.locator('#errorBox').is_hidden()

    def yaxis_range():
        return page.evaluate('''(() => {
            const c = echarts.getInstanceByDom(document.getElementById("kchart0"));
            const y = c.getOption().yAxis[0];
            return { min: y.min, max: y.max };
        })()''')

    full = yaxis_range()
    assert full['min'] is not None and full['max'] is not None, \
        '初始渲染后 Y 轴应有显式范围'
    full_span = full['max'] - full['min']

    # 滚轮/滑块缩放到前 5% 区间
    page.evaluate('''echarts.getInstanceByDom(
        document.getElementById("kchart0"))
        .dispatchAction({type: "dataZoom", start: 0, end: 5})''')
    page.wait_for_timeout(400)

    zoomed = yaxis_range()
    zoom_span = zoomed['max'] - zoomed['min']
    assert zoomed['min'] > full['min'], '放大局部后 Y 下限应抬高'
    assert zoom_span < full_span * 0.5, \
        f'放大后 Y 轴跨度应显著小于全量 ({zoom_span} vs {full_span})'

    # 再缩回全量，范围恢复
    page.evaluate('''echarts.getInstanceByDom(
        document.getElementById("kchart0"))
        .dispatchAction({type: "dataZoom", start: 0, end: 100})''')
    page.wait_for_timeout(400)
    restored = yaxis_range()
    assert abs((restored['max'] - restored['min']) - full_span) < full_span * 0.05


# ---------------------------------------------------------------- 回归：错误体验

def test_custom_code_error_still_shows_traceback(page):
    uncheck_all_data(page)
    check_data(page, DATA1)
    page.click('input[name=stratSrc][value=custom]')
    page.fill('#editor', 'class Broken(  # 缺少冒号\n')
    page.click('#runBtn')
    page.wait_for_selector('#errorBox', timeout=30000)
    box = page.locator('#errorBox')
    expect(box).to_be_visible()
    assert '语法错误' in box.inner_text()
    # 配置保留：代码还在编辑器里
    assert 'Broken' in page.locator('#editor').input_value()
