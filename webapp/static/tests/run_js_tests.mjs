// bt-lab 前端纯函数单元测试（node 运行）
// 用法: node webapp/static/tests/run_js_tests.mjs
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const B = require(path.join(here, '..', 'bt-lab.js'));

let passed = 0, failed = 0;
function test(name, fn) {
  try { fn(); console.log('  ✓ ' + name); passed++; }
  catch (e) { console.error('  ✗ ' + name + '\n    ' + e.message); failed++; }
}

console.log('== 主题 ==');
test('normalizeTheme 只接受 dark/light', () => {
  assert.equal(B.normalizeTheme('light'), 'light');
  assert.equal(B.normalizeTheme('dark'), 'dark');
  assert.equal(B.normalizeTheme('garbage'), 'dark');
  assert.equal(B.normalizeTheme(undefined), 'dark');
});
test('toggleTheme 双向切换', () => {
  assert.equal(B.toggleTheme('dark'), 'light');
  assert.equal(B.toggleTheme('light'), 'dark');
});
test('getInitialTheme 读取 storage 并持久化', () => {
  const store = { m: {}, getItem(k) { return this.m[k] ?? null; }, setItem(k, v) { this.m[k] = String(v); } };
  assert.equal(B.getInitialTheme(store, 'dark'), 'dark');
  store.m['btlab-theme'] = 'light';
  assert.equal(B.getInitialTheme(store, 'dark'), 'light');
  B.saveTheme(store, 'light');
  assert.equal(store.m['btlab-theme'], 'light');
  // storage 抛异常时不崩
  const bad = { getItem() { throw new Error('x'); }, setItem() { throw new Error('x'); } };
  assert.equal(B.getInitialTheme(bad, 'light'), 'light');
  B.saveTheme(bad, 'dark');
});
test('themeColors 双主题色板', () => {
  assert.notEqual(B.themeColors('dark').bg, B.themeColors('light').bg);
  assert.ok(B.themeColors('dark').axis && B.themeColors('light').axis);
});

console.log('== Python 语法高亮 ==');
test('关键字/字符串/数字/注释分别着色', () => {
  const code = 'class A(bt.Strategy):  # note\n    s = "hi"\n    n = 3.5\n';
  const html = B.highlightPython(code);
  assert.ok(html.includes('tk-kw'), '应有关键字高亮: ' + html);
  assert.ok(html.includes('tk-str'), '应有字符串高亮');
  assert.ok(html.includes('tk-num'), '应有数字高亮');
  assert.ok(html.includes('tk-com'), '应有注释高亮');
});
test('HTML 特殊字符被转义', () => {
  const html = B.highlightPython('s = "<b>"');
  assert.ok(!/<b>/.test(html.replace(/<span[^>]*>|<\/span>/g, '')), '原始 <b> 不应残留');
  assert.ok(html.includes('&lt;b&gt;'));
});
test('tokenize 覆盖三种引号与多行字符串', () => {
  const toks = B.tokenizePython("a='x'\nb=\"y\"\nc='''z\nz2'''\nd=\"\"\"w\"\"\"");
  const strs = toks.filter(t => t.type === 'str').map(t => t.text);
  assert.deepEqual(strs, ["'x'", '"y"', "'''z\nz2'''", '"""w"""']);
});
test('内置名（self/bt）单独分类', () => {
  const toks = B.tokenizePython('self bt foo');
  assert.deepEqual(toks.map(t => t.type), ['bi', 'ws', 'bi', 'ws', 'id']);
});

console.log('== 名词术语包裹 ==');
test('长词优先，避免重复嵌套', () => {
  const g = { '夏普比率': 'a', '夏普': 'b', '比率': 'c' };
  const html = B.wrapTerms('夏普比率越高', g);
  assert.equal((html.match(/class="term"/g) || []).length, 1);
  assert.ok(html.includes('data-term="夏普比率"'));
});
test('文本转义 + 未命中术语原样返回', () => {
  const html = B.wrapTerms('总<收益> & 胜率', { '胜率': 'x' });
  assert.ok(html.includes('&lt;收益&gt;'));
  assert.ok(html.includes('class="term"'));
  assert.equal(B.wrapTerms('abc', {}), 'abc');
});

console.log('== ECharts option 构建 ==');
const chartData = {
  dates: ['2024-01-01', '2024-01-02'],
  candles: [[10, 11, 9, 12], [11, 10.5, 10, 11.5]],
  volume: [100, 200],
  trades: [{ date: '2024-01-01', price: 10, side: 'buy' },
           { date: '2024-01-02', price: 11, side: 'sell' }],
  equity: [['2024-01-01', 100000], ['2024-01-02', 101000]],
  indicators: [{ name: 'SMA', data: [null, 10.2] }],
};
test('K线 option：三 grid（主图/成交量/权益）+ 指标 + 买卖点', () => {
  const opt = B.buildCandleOption(chartData, 'dark');
  assert.equal(opt.grid.length, 3);
  assert.equal(opt.series[0].type, 'candlestick');
  const names = opt.series.map(s => s.name);
  assert.ok(names.includes('买入') && names.includes('卖出') && names.includes('SMA'));
  assert.ok(names.includes('成交量') && names.includes('权益曲线'));
  assert.equal(opt.xAxis.length, 3);
});
test('K线 option 随主题换色', () => {
  const d = B.buildCandleOption(chartData, 'dark');
  const l = B.buildCandleOption(chartData, 'light');
  assert.notEqual(d.series[0].itemStyle.color, l.series[0].itemStyle.color);
});
test('优化 option：双参数网格 → 热力图', () => {
  const rows = [
    { params: { fast: 5, slow: 20 }, annual_pct: 1.0 },
    { params: { fast: 5, slow: 30 }, annual_pct: 2.0 },
    { params: { fast: 9, slow: 20 }, annual_pct: 3.0 },
    { params: { fast: 9, slow: 30 }, annual_pct: -1.0 },
  ];
  const built = B.buildOptChartOption(rows, 'dark');
  assert.equal(built.kind, 'heatmap');
  assert.equal(built.option.xAxis.data.length, 2);
  assert.equal(built.option.yAxis.data.length, 2);
  assert.equal(built.option.series[0].data.length, 4);
});
test('优化 option：单参数网格 → 柱状图', () => {
  const rows = [
    { params: { fast: 5, slow: 30 }, annual_pct: 1.0 },
    { params: { fast: 9, slow: 30 }, annual_pct: 2.0 },
  ];
  const built = B.buildOptChartOption(rows, 'dark');
  assert.equal(built.kind, 'bar');
  assert.equal(built.option.series[0].data.length, 2);
});
test('对比 option：多数据归一化权益曲线（时间轴）', () => {
  const cmp = [
    { data_name: 'A', equity_dates: ['2024-01-01', '2024-01-02'], equity_norm: [100, 101] },
    { data_name: 'B', equity_dates: ['2024-01-01', '2024-01-03'], equity_norm: [100, 99] },
  ];
  const opt = B.buildCompareOption(cmp, 'dark');
  assert.equal(opt.series.length, 2);
  assert.equal(opt.xAxis.type, 'time');
  assert.deepEqual(opt.series[0].data[0], ['2024-01-01', 100]);
});

console.log('== Y 轴随缩放自适应 ==');
const cd = {
  dates: ['d1', 'd2', 'd3', 'd4', 'd5', 'd6', 'd7', 'd8', 'd9', 'd10'],
  candles: [[10, 11, 9, 12], [11, 12, 10, 13], [12, 11, 11, 14], [13, 14, 12, 15],
            [14, 15, 13, 16], [15, 14, 14, 17], [16, 17, 15, 18], [17, 18, 16, 19],
            [18, 19, 17, 20], [19, 20, 18, 21]],
  volume: [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000],
  equity: [['d1', 100], ['d5', 120], ['d10', 110]],
  indicators: [{ name: 'SMA', data: Array(10).fill(14) }],
};
test('全量窗口：min/max 覆盖全部 K 线并带 5% 边距', () => {
  const r = B.computeVisibleRange(cd, 0, 100);
  const lo = 9, hi = 21, pad = (hi - lo) * 0.05;
  assert.ok(Math.abs(r.min - (lo - pad)) < 1e-9);
  assert.ok(Math.abs(r.max - (hi + pad)) < 1e-9);
  assert.equal(r.volMax, 1000);
});
test('局部窗口（前 30%）：范围只来自可见 K 线', () => {
  const r = B.computeVisibleRange(cd, 0, 30);  // d1..d4 → low=9 high=15
  const pad = (15 - 9) * 0.05;
  assert.ok(Math.abs(r.min - (9 - pad)) < 1e-9);
  assert.ok(Math.abs(r.max - (15 + pad)) < 1e-9);
  assert.equal(r.volMax, 400);  // 只统计 d1..d4
});
test('尾部窗口（后 20%）', () => {
  const r = B.computeVisibleRange(cd, 80, 100);  // d9,d10 → low 17/18, high 20/21
  assert.ok(r.min > 15 && r.max < 22);
  assert.equal(r.volMax, 1000);
});
test('权益范围按可见窗口过滤并带边距', () => {
  const r = B.computeVisibleRange(cd, 0, 40);  // d1..d5 → equity 100,120
  assert.ok(r.equity && r.equity.min < 100 && r.equity.max > 120);
  const r2 = B.computeVisibleRange(cd, 0, 10);  // 只有 d1 → 100
  assert.ok(r2.equity && r2.equity.min < 100 && r2.equity.max > 100);
});
test('指标值不参与主轴范围（RSI 等量纲不同的线不压扁价格轴）', () => {
  const cd2 = JSON.parse(JSON.stringify(cd));
  cd2.indicators = [{ name: 'RSI', data: Array(10).fill(0) }];  // 极小值
  const r = B.computeVisibleRange(cd2, 0, 100);
  assert.ok(r.min > 5, '主轴下限不应被 RSI=0 拉低');
});
test('空数据安全返回 null', () => {
  assert.equal(B.computeVisibleRange({ dates: [] }, 0, 100), null);
  assert.equal(B.computeVisibleRange({}, 0, 100), null);
});

console.log('== 数字工具 ==');
test('fmtNum / pnlClass / abbrNum', () => {
  assert.equal(B.fmtNum(null), '—');
  assert.equal(B.fmtNum(1234.567), '1,234.57');
  assert.equal(B.pnlClass(1), 'up');
  assert.equal(B.pnlClass(-1), 'down');
  assert.equal(B.abbrNum(15000), '1.5万');
});

console.log(`\n${failed === 0 ? 'ALL PASS' : 'FAIL'}: ${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
