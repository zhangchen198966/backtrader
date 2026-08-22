# -*- coding: utf-8 -*-
"""端到端测试（Playwright + Chromium 无头浏览器）

逐项验证 5 个目标在真实浏览器中的行为：
  1. 亮/暗主题切换（含持久化）
  2. 图表由浏览器组件（ECharts canvas）渲染
  3. 专业名词点击弹出 tips
  4. 优化结果出图 + 多数据对比回测
  5. 自定义代码语法高亮
"""
import os
import re
import time

import pytest
from playwright.sync_api import sync_playwright, expect

from webapp.tests.conftest import simple_backtest_request  # noqa: F401

DATA1 = 'datas/2006-day-001.txt'
DATA2 = 'datas/nvda-2014.txt'

pytestmark = pytest.mark.e2e


def wait_done(page, timeout=120):
    """等待任务完成：卡片/优化图/批量对比/错误框任一可见"""
    page.wait_for_selector('.card:visible, #optChart:visible, '
                           '#batchChart:visible, #errorBox:visible',
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
    # 等元素存在即可：自带分组默认折叠，首个 checkbox 不可见
    page.wait_for_selector('#dataList input', state='attached', timeout=15000)
    yield page
    ctx.close()


def open_data_groups(page):
    """展开数据分组（折叠状态下的 checkbox 不可点击）"""
    page.evaluate('() => document.querySelectorAll(".data-group")'
                  '.forEach(d => { d.open = true; })')


def uncheck_all_data(page):
    open_data_groups(page)
    for cb in page.query_selector_all('.dataChk'):
        if cb.is_checked():
            cb.click()


def check_data(page, value):
    open_data_groups(page)
    cb = page.query_selector(f'.dataChk[value="{value}"]')
    assert cb is not None, f'数据选项不存在: {value}'
    if not cb.is_checked():
        cb.click()


# ---------------------------------------------------------------- 目标1：主题

def test_theme_toggle_and_persist(page):
    html = page.locator('html')
    expect(html).to_have_attribute('data-theme', 'dark')

    def bg_var():
        # 读 CSS 变量而非过渡中的计算样式（变量随属性瞬时切换，稳定可测）
        return page.evaluate(
            'getComputedStyle(document.documentElement)'
            '.getPropertyValue("--bg").trim()')

    bg_dark = bg_var()
    page.click('#themeBtn')
    expect(html).to_have_attribute('data-theme', 'light')
    bg_light = bg_var()
    assert bg_dark != bg_light, '切换主题后 --bg 变量应改变'

    # 持久化到 localStorage
    saved = page.evaluate('localStorage.getItem("btlab-theme")')
    assert saved == 'light'

    # 刷新后保持亮色（加载后无过渡干扰，校验真实背景色）
    page.reload()
    page.wait_for_selector('#dataList input', state='attached', timeout=15000)
    expect(page.locator('html')).to_have_attribute('data-theme', 'light')
    assert bg_var() == bg_light

    # 切回暗色（不污染后续测试）
    page.click('#themeBtn')
    expect(html).to_have_attribute('data-theme', 'dark')


# ---------------------------------------------------------------- 目标3：术语 tips

def test_term_click_shows_tooltip(page):
    # 等模板与术语表加载（参数标签里就有术语）；用可见术语（折叠区内的不可见）
    page.wait_for_selector('.term:visible', timeout=15000)
    first_term = page.locator('.term:visible').first
    term_text = first_term.inner_text()

    first_term.click()
    tip = page.locator('#tipbox')
    expect(tip).to_be_visible()
    assert tip.locator('.tip-title').inner_text() == term_text
    assert len(tip.locator('.tip-body').inner_text()) >= 8

    # 点空白处关闭
    page.locator('header h1').click(force=True)
    expect(tip).not_to_be_visible()


def test_term_tips_cover_params_config_and_tables(page):
    """术语 tips 覆盖：参数区 / 配置区静态标签 / 模板说明 / 结果表格"""
    page.wait_for_selector('.term:visible', timeout=15000)

    def click_term_and_read(page, selector):
        page.locator(selector).first.click()
        tip = page.locator('#tipbox')
        expect(tip).to_be_visible()
        title = tip.locator('.tip-title').inner_text()
        body = tip.locator('.tip-body').inner_text()
        page.locator('header h1').click(force=True)  # 关闭
        return title, body

    # 1) 策略参数区：切换到含 ATR/RSI 参数的模板，点参数术语
    page.select_option('#tplSel', 'sma_rsi_atr')
    page.wait_for_timeout(200)
    t1 = page.locator('.p-label .term[data-term="ATR止损倍数"]')
    assert t1.count() >= 1, '参数「ATR止损倍数」应有术语 tips'
    title, body = click_term_and_read(page, '.p-label .term[data-term="ATR止损倍数"]')
    assert title == 'ATR止损倍数' and '止损距离' in body

    # 2) 配置区静态标签：初始现金 / 网格模式
    assert page.locator('label.tlabel .term[data-term="初始现金"]').count() == 1
    assert page.locator('span.tlabel .term[data-term="网格模式"]').count() == 1
    title, body = click_term_and_read(page, 'label.tlabel .term[data-term="初始现金"]')
    assert title == '初始现金' and '本金' in body

    # 3) 模板描述里的术语（均线/RSI）
    assert page.locator('#tplDesc .term').count() >= 1

    # 4) 数据源提示（含 前复权 等术语）
    page.click('text=在线获取数据（AkShare / Yahoo）')
    page.wait_for_selector('#fetchHint', state='attached')
    page.wait_for_timeout(200)
    n_hint = page.locator('#fetchHint .term').count()
    assert n_hint >= 1, '数据源提示应包含术语（如前复权/代码说明）'

    # 5) 运行后：交易明细表头与优化表头有术语
    uncheck_all_data(page)
    check_data(page, DATA1)
    page.click('#runBtn')
    wait_done(page)
    assert page.locator('.tablewrap thead .term').count() >= 3, \
        '交易明细表头应含术语 tips'
    title, body = click_term_and_read(page, '.tablewrap thead .term[data-term="手数"]')
    assert title == '手数'


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


# ---------------------------------------------------------------- 批量对比回测

def test_batch_backtest_flow(page):
    """任务2：批量回测——多组参数一次跑，同图叠加对比 + 指标表 + 行点击详情"""
    uncheck_all_data(page)
    check_data(page, DATA1)
    page.select_option('#tplSel', 'supertrend')
    page.wait_for_timeout(300)

    # 开启批量模式：编辑器出现 3 行默认参数组，批量按钮可见
    page.check('#batchMode')
    page.wait_for_selector('#batchEditor table tbody tr', timeout=5000)
    rows = page.locator('#batchEditor tbody tr').count()
    assert rows == 3, f'默认应有 3 组参数（实际 {rows}）'
    assert page.locator('#batchBtn').is_visible()

    # 修改第一组名称与参数
    page.fill('#batchEditor tbody tr:first-child input[data-f="name"]', '我的标准组')
    page.fill('#batchEditor tbody tr:first-child input[data-f="p:atr_period"]', '10')
    page.fill('#batchEditor tbody tr:first-child input[data-f="p:multiplier"]', '3')

    # 运行批量
    page.click('#batchBtn')
    wait_done(page, timeout=180)
    assert page.locator('#errorBox').is_hidden()

    # 叠加权益图（ECharts canvas）+ 指标对比表 3 行
    assert page.locator('#batchChart canvas').count() >= 1
    assert page.locator('#batchTable tbody tr').count() == 3
    table_text = page.locator('#batchTable').inner_text()
    assert '我的标准组' in table_text
    assert '总收益率' in page.locator('#batchTable thead').inner_text()

    # ECharts series 数量 = 3 组
    n_series = page.evaluate(
        'echarts.getInstanceByDom(document.getElementById("batchChart"))'
        '.getOption().series.length')
    assert n_series == 3

    # 行点击 → 展开该组完整回测（绩效卡片 + K线图）
    page.locator('#batchTable tbody tr').first.click()
    page.wait_for_selector('.card', timeout=10000)
    assert page.locator('.card').count() == 12
    assert page.locator('#kchart0 canvas').count() >= 1


# ---------------------------------------------------------------- UI：年度条形图 / 服务徽章 / 日志坞

def test_yearly_bars_geometry(page):
    """年度收益率图几何+内容自测：零轴水平、正柱上/负柱下、标签对齐，
    且图内不得出现游离文字（曾出现 0% 伪元素看起来像多出一个数据）"""
    uncheck_all_data(page)
    check_data(page, DATA1)
    page.select_option('#tplSel', 'sma_rsi_atr')
    page.click('#runBtn')
    wait_done(page)

    page.wait_for_selector('.yearlyBars .ycolWrap', timeout=10000)
    audit = page.evaluate("""(() => {
        const sec = document.querySelector('.yearlyBars');
        const cols = [...sec.querySelectorAll('.ycolWrap')];
        return {
            secText: sec.innerText,
            zeroAfter: cols.map(c =>
                getComputedStyle(c.querySelector('.yzero'), '::after').content),
            zones: cols.map(c => {
                const zone = c.querySelector('.yzone').getBoundingClientRect();
                const zero = c.querySelector('.yzero').getBoundingClientRect();
                const bar = c.querySelector('.ybar').getBoundingClientRect();
                const span = c.querySelector('span').getBoundingClientRect();
                return {
                    val: parseFloat(c.dataset.val),
                    zeroHorizontal: Math.abs(zero.top - zone.top - zone.height / 2) <= 1,
                    barBelowZero: bar.top >= zero.bottom - 1,
                    barAboveZero: bar.bottom <= zero.top + 1,
                    barHeight: bar.height,
                    spanTop: span.top,
                    spanInsideCol: span.top >= zone.bottom - 2,
                };
            }),
        };
    })()""")
    zones = audit['zones']
    assert zones, '应至少有一个年份柱'
    # 图内文字只允许纯年份或带符号百分比，杜绝任何额外数据样式的文字
    for line in audit['secText'].split('\n'):
        line = line.strip()
        if not line:
            continue
        assert re.fullmatch(r'\d{4}|[+\-]?\d+(\.\d+)?%', line), \
            f'年度图中出现游离文字: "{line}"'
    # 零轴伪元素必须为空（0% 标签曾被误认为多出的数据点）
    for zc in audit['zeroAfter']:
        assert zc in ('none', 'normal'), f'零轴伪元素应为空: {zc}'
    span_tops = {round(g['spanTop']) for g in zones}
    assert len(span_tops) == 1, f'年份标签应同行对齐: {span_tops}'
    for g in zones:
        assert g['zeroHorizontal'], '零轴应位于区域垂直中线'
        assert g['barHeight'] >= 2, '柱子应有可见高度'
        assert g['spanInsideCol'], '标签应在柱区下方'
        if g['val'] >= 0:
            assert g['barAboveZero'], f"正收益 {g['val']}% 的柱子应在零轴上方"
        else:
            assert g['barBelowZero'], f"负收益 {g['val']}% 的柱子应在零轴下方"


def test_yearly_bars_multi_year(page):
    """20 年数据集：多年份柱方向交替正确、标签对齐、无游离文字"""
    uncheck_all_data(page)
    check_data(page, 'datas/orcl-1995-2014.txt')
    page.select_option('#tplSel', 'sma_rsi_atr')
    page.click('#runBtn')
    wait_done(page, timeout=180)

    page.wait_for_selector('.yearlyBars .ycolWrap', timeout=10000)
    res = page.evaluate("""(() => {
        const sec = document.querySelector('.yearlyBars');
        const cols = [...sec.querySelectorAll('.ycolWrap')];
        const spanTops = new Set(cols.map(c =>
            Math.round(c.querySelector('span').getBoundingClientRect().top)));
        return {
            n: cols.length,
            secText: sec.innerText,
            spanTops: [...spanTops],
            dirs: cols.map(c => {
                const zero = c.querySelector('.yzero').getBoundingClientRect();
                const bar = c.querySelector('.ybar').getBoundingClientRect();
                const val = parseFloat(c.dataset.val);
                const okDir = val >= 0 ? bar.bottom <= zero.top + 1
                                       : bar.top >= zero.bottom - 1;
                return {val, okDir};
            }),
        };
    })()""")
    assert res['n'] >= 15, f"20 年数据应有不少于 15 个年份柱（实际 {res['n']}）"
    assert len(res['spanTops']) == 1, f'多年份标签应对齐: {res["spanTops"]}'
    for d in res['dirs']:
        assert d['okDir'], f"年份 {d['val']}% 柱子方向错误"
    assert '0%' not in res['secText'].replace('-0%', '').replace('+0%', ''), \
        '零轴 0% 标签不得再出现'
    pos = [d['val'] for d in res['dirs'] if d['val'] > 0]
    neg = [d['val'] for d in res['dirs'] if d['val'] < 0]
    assert pos and neg, '20 年数据应同时存在正负收益年份'


def test_service_badge_and_logdock(page):
    uncheck_all_data(page)
    check_data(page, DATA1)
    page.select_option('#tplSel', 'sma_rsi_atr')
    page.click('#runBtn')
    wait_done(page)

    # 服务徽章：明确文字 + 可点击刷新
    badge = page.locator('#srvBadge')
    expect(badge).to_be_visible()
    assert '服务正常' in badge.inner_text()
    badge.click()
    page.wait_for_timeout(300)
    assert '服务正常' in page.locator('#srvBadge').inner_text()

    # 日志坞：默认收起，展开后包含任务与策略日志
    dock = page.locator('#logDock')
    expect(dock).to_be_visible()
    assert not dock.evaluate('el => el.classList.contains("open")'), '默认应收起'
    page.click('#logToggle')
    page.wait_for_timeout(200)
    assert dock.evaluate('el => el.classList.contains("open")'), '点击应展开'
    body_text = page.locator('#logBody').inner_text()
    assert '任务已提交' in body_text, '应有任务生命周期日志'
    assert '运行完成' in body_text
    assert '买入成交' in body_text or '平仓' in body_text, '应有策略逐笔日志'
    page.click('#logToggle')
    page.wait_for_timeout(200)
    assert not dock.evaluate('el => el.classList.contains("open")')


def test_history_panel_table_and_delete(page):
    """历史任务整合为表格：含策略/数据/结果摘要，支持查看与删除"""
    uncheck_all_data(page)
    check_data(page, DATA1)
    page.select_option('#tplSel', 'sma_rsi_atr')
    page.click('#runBtn')
    wait_done(page)

    page.click('#tabHist')
    page.wait_for_selector('#histBody table tbody tr', timeout=10000)

    headers = page.locator('#histBody thead th').all_inner_texts()
    for need in ['时间', '类型', '策略', '数据', '参数', '结果', '状态']:
        assert need in headers, f'历史表缺少列: {need}'

    first_row = page.locator('#histBody tbody tr').first
    row_text = first_row.inner_text()
    assert '回测' in row_text
    assert 'sma_rsi_atr' in row_text or '自定义' in row_text
    assert '2006-day-001' in row_text
    assert '收益' in row_text

    # 查看：点击后主区域渲染出绩效卡片
    first_row.locator('.op.view').click()
    page.wait_for_selector('.card', timeout=10000)
    assert page.locator('.card').count() == 12

    # 删除：删掉最后一行任务，该任务的行应消失
    page.click('#tabHist')
    page.wait_for_selector('#histBody table tbody tr', timeout=5000)
    last_id = page.locator('#histBody tbody tr').last.get_attribute('data-id')
    page.on('dialog', lambda d: d.accept())
    page.locator('#histBody tbody tr').last.locator('.op.del').click()
    page.wait_for_timeout(800)
    gone = page.locator(f'#histBody tbody tr[data-id="{last_id}"]').count()
    assert gone == 0, f'删除后任务 {last_id} 的行应消失'


# ---------------------------------------------------------------- 数据管理：默认勾选 / 删除

def test_data_group_collapse(page):
    """数据列表分组折叠：自带默认折叠、上传默认展开、可切换、计数角标"""
    groups = page.evaluate('''(() => {
        const gs = [...document.querySelectorAll('.data-group')];
        return gs.map(g => ({
            label: g.querySelector('summary').innerText,
            open: g.open,
            count: parseInt(g.querySelector('.dcount').textContent),
            items: g.querySelectorAll('.dataChk').length,
        }));
    })()''')
    by_label = {g['label'][:4]: g for g in groups}
    for g in groups:
        assert g['count'] == g['items'], '计数角标应与实际条目数一致'
    builtin = next((g for g in groups if '自带' in g['label']), None)
    uploads = next((g for g in groups if '上传' in g['label']), None)
    assert builtin is not None, '应有自带样例分组'
    assert builtin['open'] is False, '自带样例默认应折叠'
    if uploads is not None:
        assert uploads['open'] is True, '我的上传默认应展开'

    # 折叠状态下默认勾选依然生效（隐藏元素 checked 可查）
    checked = page.evaluate(
        '() => [...document.querySelectorAll(".dataChk:checked")].map(c => c.value)')
    assert len(checked) == 1 and 'datas/' in checked[0]

    # 点击 summary 展开/收起
    page.evaluate('() => document.querySelector(".data-group").open = false')
    page.click('.data-group > summary')
    assert page.evaluate('() => document.querySelector(".data-group").open') is True
    page.click('.data-group > summary')
    assert page.evaluate('() => document.querySelector(".data-group").open') is False


def test_data_default_selection_and_delete(page):
    """上传数据默认不勾选（只默认勾一个自带样例）；上传数据可删除"""
    uploads = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), 'webapp', 'uploads')
    # 造一个专用于删除测试的文件
    dummy = os.path.join(uploads, 'zz-e2e-delete-me.csv')
    with open(dummy, 'w') as f:
        f.write('Date,Open,High,Low,Close,Volume,OpenInterest\n')
        for d in range(1, 25):
            f.write(f'2024-01-{d:02d},10,11,9,10,100,0\n')
    try:
        page.reload()
        page.wait_for_selector('.dataChk', state='attached', timeout=15000)

        # 1) 上传区的 checkbox 一律不勾选
        upload_checks = page.locator('.dataChk')
        n = upload_checks.count()
        upload_items = page.evaluate('''(() => {
            const items = [...document.querySelectorAll('.dataChk')];
            return items.filter(c => c.value.includes('webapp/uploads/')
                                     && !c.value.includes('zz-e2e-delete-me'));
        })()''')
        if upload_items:
            assert all(not c['checked'] for c in page.evaluate(
                '() => [...document.querySelectorAll(".dataChk")]'
                '.filter(c => c.value.includes("webapp/uploads/"))'
                '.map(c => ({checked: c.checked}))')), \
                '上传数据不应默认勾选'
        # 2) 默认恰好勾选 1 个（自带样例）
        checked = page.evaluate(
            '() => [...document.querySelectorAll(".dataChk:checked")]'
            '.map(c => c.value)')
        assert len(checked) == 1 and 'datas/' in checked[0], \
            f'应默认只勾选一个自带样例: {checked}'

        # 3) 删除按钮只出现在上传区
        dels = page.locator('.data-del')
        assert dels.count() >= 1
        # 自带数据项没有删除按钮
        builtin_has_del = page.evaluate(
            '() => [...document.querySelectorAll(".data-item")]'
            '.some(it => it.querySelector("input").value.startsWith("datas/")'
            ' && it.querySelector(".data-del"))')
        assert not builtin_has_del, '自带数据不应有删除按钮'

        # 4) 删除 dummy 文件 → 列表移除、文件消失
        page.on('dialog', lambda d: d.accept())
        target = page.locator('.data-del[data-path*="zz-e2e-delete-me"]')
        target.click()
        page.wait_for_timeout(600)
        assert page.locator('.data-del[data-path*="zz-e2e-delete-me"]').count() == 0
        assert not os.path.exists(dummy)
    finally:
        if os.path.exists(dummy):
            os.remove(dummy)


# ---------------------------------------------------------------- 参数单位 / 模板市场 / 保存模板

def open_details(page, text):
    """展开指定标题的折叠面板"""
    page.evaluate(
        f'() => [...document.querySelectorAll("details")]'
        f'.filter(d => d.querySelector("summary")'
        f'  && d.querySelector("summary").innerText.includes({text!r}))'
        f'.forEach(d => {{ d.open = true; }})')


def test_param_units_visible(page):
    """目标1：参数标签显示单位（如 bar 数 / 倍数 / 0~100）"""
    page.select_option('#tplSel', 'sma_rsi_atr')
    page.wait_for_timeout(200)
    labels = page.locator('.p-label').all_inner_texts()
    assert any('bar 数' in t for t in labels), f'应显示 bar 数单位: {labels}'
    assert any('倍数' in t for t in labels), 'ATR止损倍数应显示 倍数'
    assert any('0~100' in t for t in labels), 'RSI下限应显示 0~100'
    # MACD 模板
    page.select_option('#tplSel', 'macd_signal')
    page.wait_for_timeout(200)
    labels = page.locator('.p-label').all_inner_texts()
    assert sum('bar 数' in t for t in labels) == 3


def test_save_custom_code_as_template(page):
    """目标3：自定义代码保存为模板 → 出现在下拉与我的模板 → 可运行 → 可删除"""
    page.click('input[name=stratSrc][value=custom]')
    page.wait_for_selector('#editor:visible', timeout=5000)
    page.click('#loadTplBtn')  # 载入当前模板代码
    code = page.locator('#editor').input_value()
    assert 'class' in code

    page.once('dialog', lambda d: d.accept('E2E保存模板'))
    page.click('#saveTplBtn')
    page.wait_for_timeout(800)

    # 下拉出现保存的模板（★ 前缀标记）并选中
    assert '★ E2E保存模板' in page.locator('#tplSel').inner_text()
    sel_val = page.locator('#tplSel').input_value()
    assert sel_val.startswith('custom-')
    # 模板市场 overlay 的「我的模板」分类显示该条目
    page.click('#marketBtn')
    page.locator('.mo-tab[data-cat="mine"]').click()
    page.wait_for_timeout(300)
    assert 'E2E保存模板' in page.locator('#mktGrid').inner_text()
    page.keyboard.press('Escape')

    # 用保存的模板真实跑一次回测
    uncheck_all_data(page)
    check_data(page, DATA1)
    page.click('#runBtn')
    wait_done(page)
    assert page.locator('#errorBox').is_hidden()
    assert page.locator('.card').count() == 12

    # 删除清理（市场 overlay 的我的模板分类）
    page.click('#marketBtn')
    page.locator('.mo-tab[data-cat="mine"]').click()
    page.wait_for_timeout(300)
    page.on('dialog', lambda d: d.accept())
    page.locator('.mytpl-del').first.click()
    page.wait_for_timeout(600)
    assert 'E2E保存模板' not in page.locator('#mktGrid').inner_text()
    page.keyboard.press('Escape')


def test_market_overlay_and_import(page):
    """目标：全屏模板市场——卡片浏览/分类/搜索/在线导入（官方+第三方）"""
    page.click('#marketBtn')
    assert page.locator('#marketOverlay').is_visible()

    # 构建版本徽章可见（用于识别旧进程）
    assert page.locator('#buildBadge').inner_text().startswith('build ')

    # 全部：官方 7 + 第三方 3
    page.wait_for_selector('.mkt-card', timeout=15000)
    all_cards = page.locator('.mkt-card').count()
    assert all_cards >= 14, f'全部应≥14条（实际{all_cards}）'
    # 官方徽章与第三方徽章同时存在
    assert page.locator('.mc-badge.official').count() >= 5
    assert page.locator('.mc-badge.community').count() >= 7

    # 分类过滤：第三方社区 = 2 张卡
    page.locator('.mo-tab[data-cat="community"]').click()
    page.wait_for_timeout(400)
    n_comm = page.locator('.mkt-card').count()
    assert n_comm == 7, f'第三方应有 7 条（实际{n_comm}）'
    grid_text = page.locator('#mktGrid').inner_text()
    for repo in ['jasgin/backtrader-backtests', 'ilahuerta-IA',
                 'jrothschild33/learn_backtrader', 'Adonis2115/Backtesting',
                 '0xRobWatson']:
        assert repo in grid_text, f'缺少来源仓库 {repo}'

    # 搜索
    page.fill('#mktSearch', '布林')
    page.wait_for_timeout(500)
    assert page.locator('.mkt-card').count() == 1
    page.fill('#mktSearch', '')
    page.wait_for_timeout(500)

    # ESC 关闭 / 按钮再开
    page.keyboard.press('Escape')
    assert page.locator('#marketOverlay').is_hidden()
    page.click('#marketBtn')
    assert page.locator('#marketOverlay').is_visible()

    # 真实在线导入（网络不可达则跳过，不误报红）
    import httpx
    try:
        probe = httpx.get('https://cdn.jsdelivr.net/gh/jasgin/backtrader-backtests'
                          '@master/StochasticSR/Stochastic_SR_Backtest.py',
                          timeout=8, trust_env=False)
        net_ok = probe.status_code == 200
    except Exception:
        net_ok = False
    if not net_ok:
        pytest.skip('jsdelivr 不可达，市场在线导入已由离线 mock 测试覆盖')

    page.locator('.mo-tab[data-cat="community"]').click()
    page.wait_for_selector('.mkt-card', timeout=10000)
    imp = page.locator('.mkt-card[data-id="mk-stoch-sr"] .mkt-import')
    imp.click()
    # 等导入流程结束（按钮文案复位，网络下载可能耗时数秒）
    page.wait_for_function(
        '() => { const b = document.querySelector(\'.mkt-card[data-id="mk-stoch-sr"] .mkt-import\');'
        ' return b && !b.disabled; }', timeout=60000)
    page.locator('.mo-tab[data-cat="mine"]').click()
    page.wait_for_selector('#mktGrid .mkt-card', timeout=15000)
    assert '随机指标' in page.locator('#mktGrid').inner_text()

    # 「使用」：关闭 overlay 并选中模板
    page.locator('.mytpl-use').first.click()
    page.wait_for_timeout(300)
    assert page.locator('#marketOverlay').is_hidden()
    assert page.locator('#tplSel').input_value().startswith('custom-')

    # 导入的模板真实回测
    uncheck_all_data(page)
    check_data(page, DATA1)
    page.click('#runBtn')
    wait_done(page)
    assert page.locator('#errorBox').is_hidden()

    # 清理
    page.click('#marketBtn')
    page.locator('.mo-tab[data-cat="mine"]').click()
    page.wait_for_timeout(300)
    page.on('dialog', lambda d: d.accept())
    page.locator('.mytpl-del').first.click()
    page.wait_for_timeout(500)
    page.keyboard.press('Escape')


# ---------------------------------------------------------------- 在线获取：日期控件 + 中文名搜索

def test_fetch_panel_date_pickers_and_symbol_search(page):
    """日期为原生 date 控件且有默认值；代码输入支持中文名搜索下拉"""
    # 展开在线获取面板
    page.click('text=在线获取数据（AkShare / Yahoo）')
    page.wait_for_selector('#fetchSymbol:visible', timeout=5000)

    # 1) 日期控件 + 默认值（近两年）
    assert page.locator('#fetchStart').get_attribute('type') == 'date'
    assert page.locator('#fetchEnd').get_attribute('type') == 'date'
    start_default = page.locator('#fetchStart').input_value()
    end_default = page.locator('#fetchEnd').input_value()
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', start_default)
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}', end_default)
    assert start_default < end_default

    # 2) 中文名搜索：输入"茅台" → 下拉出现 600519 → 点击选中
    page.fill('#fetchSymbol', '茅台')
    page.wait_for_selector('#symDrop .symopt', timeout=10000)
    opt = page.locator('#symDrop .symopt').first
    assert '600519' in opt.inner_text()
    assert '贵州茅台' in opt.inner_text()
    opt.click()
    page.wait_for_timeout(200)
    assert page.locator('#fetchSymbol').input_value() == '600519'
    assert '贵州茅台' in page.locator('#symPicked').inner_text()

    # 3) 代码前缀搜索：结果按代码排序，30075 应精确命中宁德时代
    page.fill('#fetchSymbol', '30075')
    page.wait_for_selector('#symDrop .symopt', timeout=10000)
    assert '300750' in page.locator('#symDrop .symopt').first.inner_text()

    # 4) 键盘：输入名称后回车选中第一个
    page.fill('#fetchSymbol', '宁德')
    page.wait_for_selector('#symDrop .symopt', timeout=10000)
    page.press('#fetchSymbol', 'Enter')
    page.wait_for_timeout(200)
    assert page.locator('#fetchSymbol').input_value() == '300750'

    # 5) 指数源搜索走静态表
    page.select_option('#fetchProvider', 'akshare-index')
    page.fill('#fetchSymbol', '上证')
    page.wait_for_selector('#symDrop .symopt', timeout=10000)
    assert 'sh000001' in page.locator('#symDrop .symopt').first.inner_text()

    # 6) Yahoo 源：中文搜索 → 苹果/AAPL；点选回填代码
    page.select_option('#fetchProvider', 'yfinance')
    page.fill('#fetchSymbol', '苹果')
    page.wait_for_selector('#symDrop .symopt', timeout=10000)
    first = page.locator('#symDrop .symopt').first.inner_text()
    assert 'AAPL' in first and '苹果' in first
    page.locator('#symDrop .symopt').first.click()
    page.wait_for_timeout(200)
    assert page.locator('#fetchSymbol').input_value() == 'AAPL'

    # 英文搜索同样可用
    page.fill('#fetchSymbol', 'tesla')
    page.wait_for_selector('#symDrop .symopt', timeout=10000)
    assert 'TSLA' in page.locator('#symDrop .symopt').first.inner_text()


# ---------------------------------------------------------------- 历史批量删除

def test_history_batch_delete(page):
    """批量删除历史：复选框勾选 → 删除选中；全选联动；清空已完成"""
    # 先跑两个任务制造历史
    for _ in range(2):
        uncheck_all_data(page)
        check_data(page, DATA1)
        page.select_option('#tplSel', 'sma_cross')
        page.click('#runBtn')
        wait_done(page)

    page.click('#tabHist')
    page.wait_for_selector('#histBody table tbody tr', timeout=10000)

    # 工具栏可见：全选/删除选中/清空已完成
    assert page.locator('#histSelAll').is_visible()
    assert page.locator('#histDelSel').is_visible()
    assert page.locator('#histClearDone').is_visible()

    # 勾选第一行 → 计数显示
    page.locator('.histChk').first.check()
    page.wait_for_timeout(200)
    assert '已选 1 项' in page.locator('#histSelCount').inner_text()
    first_id = page.locator('#histBody tbody tr').first.get_attribute('data-id')

    # 删除选中
    page.on('dialog', lambda d: d.accept())
    page.click('#histDelSel')
    page.wait_for_timeout(800)
    assert page.locator(f'#histBody tbody tr[data-id="{first_id}"]').count() == 0

    # 全选 → 计数=行数 → 清空已完成
    page.check('#histSelAll')
    page.wait_for_timeout(200)
    n_rows = page.locator('#histBody tbody tr').count()
    n_chk = page.locator('.histChk:checked').count()
    assert n_chk == n_rows and n_rows >= 1

    page.click('#histClearDone')
    page.wait_for_timeout(800)
    remaining = page.locator('#histBody tbody tr').count()
    if remaining == 0:
        assert '暂无历史任务' in page.locator('#histBody').inner_text()


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
