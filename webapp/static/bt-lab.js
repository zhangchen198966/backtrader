/* bt-lab 前端核心逻辑（纯函数 + 可测试）
 * 浏览器: <script src="/static/bt-lab.js"> → window.BTLab
 * Node 测试: const BTLab = require('../static/bt-lab.js')
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.BTLab = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* ================================================== 1. 主题 */

  const THEME_COLORS = {
    dark: {
      bg: '#0e1318', panel: '#161d26', panel2: '#1c2530', line: '#263242',
      txt: '#d7e0ea', sub: '#8494a7',
      up: '#f2555a', down: '#2ecc8f',      // 红涨绿跌
      accent: '#4f9cf9',
      cross: '#4f9cf9', bgPlot: '#0e1318',
      axis: '#8494a7', split: '#1c2530',
      tkKw: '#c678dd', tkStr: '#98c379', tkNum: '#d19a66',
      tkCom: '#7f8c98', tkBi: '#56b6c2',
    },
    light: {
      bg: '#f5f7fa', panel: '#ffffff', panel2: '#eef2f7', line: '#d8e0ea',
      txt: '#1c2530', sub: '#5b6b7e',
      up: '#d93036', down: '#0f9d6c',
      accent: '#1a73e8',
      cross: '#333', bgPlot: '#ffffff',
      axis: '#5b6b7e', split: '#e3e9f1',
      tkKw: '#a626a4', tkStr: '#50a14f', tkNum: '#b76b01',
      tkCom: '#a0a1a7', tkBi: '#0184bc',
    },
  };

  function normalizeTheme(t) {
    return t === 'light' ? 'light' : 'dark';
  }

  function getInitialTheme(storage, fallback) {
    try {
      const v = storage && storage.getItem('btlab-theme');
      if (v) return normalizeTheme(v);
    } catch (e) { /* storage 不可用时忽略 */ }
    return normalizeTheme(fallback);
  }

  function saveTheme(storage, theme) {
    try { storage && storage.setItem('btlab-theme', normalizeTheme(theme)); }
    catch (e) { /* 忽略 */ }
  }

  function toggleTheme(theme) {
    return normalizeTheme(theme) === 'dark' ? 'light' : 'dark';
  }

  function applyTheme(doc, theme) {
    doc.documentElement.setAttribute('data-theme', normalizeTheme(theme));
  }

  function themeColors(theme) {
    return THEME_COLORS[normalizeTheme(theme)];
  }

  /* ================================================== 2. Python 语法高亮 */

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  const PY_KEYWORDS = new Set(('class def return if elif else for while in import from as pass ' +
    'break continue try except finally with lambda None True False and or not is ' +
    'global raise yield del assert nonlocal').split(' '));
  const PY_BUILTINS = new Set(('self bt print len range float int str list dict set abs min max ' +
    'sum enumerate zip sorted round type isinstance super').split(' '));

  function tokenizePython(code) {
    /* 返回 [{type, text}]，type: kw|str|num|com|bi|id|op|ws */
    const re = /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_]\w*)|(\s+)|([^\sA-Za-z_0-9'"]+)/g;
    const out = [];
    let m, last = 0;
    const s = String(code == null ? '' : code);
    while ((m = re.exec(s)) !== null) {
      if (m.index > last) out.push({ type: 'id', text: s.slice(last, m.index) });
      if (m[1]) out.push({ type: 'com', text: m[1] });
      else if (m[2]) out.push({ type: 'str', text: m[2] });
      else if (m[3]) out.push({ type: 'num', text: m[3] });
      else if (m[4]) {
        const w = m[4];
        out.push({ type: PY_KEYWORDS.has(w) ? 'kw' : (PY_BUILTINS.has(w) ? 'bi' : 'id'), text: w });
      }
      else if (m[5]) out.push({ type: 'ws', text: m[5] });
      else if (m[6]) out.push({ type: 'op', text: m[6] });
      last = re.lastIndex;
    }
    if (last < s.length) out.push({ type: 'id', text: s.slice(last) });
    return out;
  }

  function highlightPython(code) {
    const toks = tokenizePython(code);
    let html = '';
    for (const t of toks) {
      const cls = t.type === 'ws' || t.type === 'id' ? '' : ' tk-' + t.type;
      html += cls ? '<span class="' + cls.slice(1) + '">' + escapeHtml(t.text) + '</span>'
        : escapeHtml(t.text);
    }
    return html + '\n';  // 保证末行换行与 textarea 对齐
  }

  /* ================================================== 3. 名词 tips */

  function wrapTerms(text, glossary) {
    let html = escapeHtml(text);
    const keys = Object.keys(glossary || {}).sort(function (a, b) { return b.length - a.length; });
    const phMap = {};
    keys.forEach(function (k, i) {
      if (html.indexOf(k) >= 0) {
        const ph = '\u0000' + i + '\u0000';
        html = html.split(k).join(ph);
        phMap[ph] = k;
      }
    });
    Object.keys(phMap).forEach(function (ph) {
      const k = phMap[ph];
      html = html.split(ph).join('<span class="term" data-term="' + escapeHtml(k) + '" title="点击查看解释">' + escapeHtml(k) + '</span>');
    });
    return html;
  }

  /* ================================================== 4. 数字/工具 */

  function fmtNum(v, nd) {
    nd = nd == null ? 2 : nd;
    if (v === null || v === undefined || isNaN(v)) return '—';
    return Number(v).toLocaleString('zh-CN',
      { minimumFractionDigits: nd, maximumFractionDigits: nd });
  }

  function pnlClass(v) { return v > 0 ? 'up' : v < 0 ? 'down' : ''; }

  /* ================================================== 5. ECharts option 构建 */

  function baseAxisColors(theme) {
    const c = themeColors(theme);
    return { axis: c.axis, split: c.split, txt: c.sub };
  }

  /** 主图：K线 + 指标线 + 买卖点；子图：成交量；子图：权益曲线 */
  function buildCandleOption(chartData, theme) {
    const c = themeColors(theme);
    const dates = chartData.dates || [];
    const candleSeries = {
      name: 'K线', type: 'candlestick', data: chartData.candles || [],
      itemStyle: { color: c.up, color0: c.down, borderColor: c.up, borderColor0: c.down },
      xAxisIndex: 0, yAxisIndex: 0, z: 3,
    };
    const indSeries = (chartData.indicators || []).map(function (ind, i) {
      return {
        name: ind.name, type: 'line', data: ind.data, showSymbol: false,
        connectNulls: true, smooth: false, lineStyle: { width: 1.3 },
        xAxisIndex: 0, yAxisIndex: 0, z: 4,
      };
    });
    const buyMarks = (chartData.trades || []).filter(function (t) { return t.side === 'buy'; });
    const sellMarks = (chartData.trades || []).filter(function (t) { return t.side === 'sell'; });
    function markSeries(name, marks, color, symbol, symbolRotate) {
      return {
        name: name, type: 'scatter',
        data: marks.map(function (t) { return [t.date, t.price]; }),
        symbol: symbol, symbolSize: 11, z: 5,
        itemStyle: { color: color, borderColor: c.bgPlot, borderWidth: 1 },
        xAxisIndex: 0, yAxisIndex: 0,
      };
    }

    const volColors = (chartData.candles || []).map(function (k) {
      return k && k[1] >= k[0] ? c.up : c.down;
    });
    const equity = chartData.equity || [];

    const ax = baseAxisColors(theme);
    return {
      animation: false, backgroundColor: 'transparent',
      axisPointer: { link: [{ xAxisIndex: 'all' }], lineStyle: { color: c.cross } },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross' },
        backgroundColor: c.panel, borderColor: c.line,
        textStyle: { color: c.txt, fontSize: 12 },
      },
      legend: {
        top: 0, textStyle: { color: ax.txt },
        data: indSeries.map(function (s) { return s.name; }).concat(['买入', '卖出']),
      },
      grid: [
        { left: 64, right: 20, top: 30, height: '46%' },
        { left: 64, right: 20, top: '62%', height: '12%' },
        { left: 64, right: 20, top: '78%', height: '16%' },
      ],
      xAxis: [0, 1, 2].map(function (i) {
        return {
          type: 'category', data: dates, gridIndex: i,
          boundaryGap: i === 1 || i === 2 ? true : false,
          axisLine: { lineStyle: { color: ax.axis } },
          axisLabel: { color: ax.txt, show: i === 2 },
          axisTick: { show: false },
        };
      }),
      yAxis: [
        { scale: true, gridIndex: 0, axisLabel: { color: ax.txt },
          splitLine: { lineStyle: { color: ax.split } } },
        { gridIndex: 1, axisLabel: { color: ax.txt, formatter: abbrNum },
          splitLine: { show: false } },
        { scale: true, gridIndex: 2, axisLabel: { color: ax.txt },
          splitLine: { lineStyle: { color: ax.split } } },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1, 2], start: 0, end: 100 },
        { type: 'slider', xAxisIndex: [0, 1, 2], bottom: 0, height: 18,
          borderColor: c.line, textStyle: { color: ax.txt } },
      ],
      series: [candleSeries].concat(indSeries, [
        markSeries('买入', buyMarks, c.up === '#f2555a' ? '#ff7a45' : '#e8710a', 'triangle'),
        markSeries('卖出', sellMarks, '#5b8def', 'path://M0,0 L10,0 L5,8 Z'),
        { name: '成交量', type: 'bar', data: chartData.volume || [],
          itemStyle: { color: volColors }, xAxisIndex: 1, yAxisIndex: 1 },
        { name: '权益曲线', type: 'line', showSymbol: false,
          data: equity.map(function (p) { return p[1]; }),
          lineStyle: { width: 1.6, color: c.accent }, areaStyle: { opacity: 0.08 },
          xAxisIndex: 2, yAxisIndex: 2 },
      ]),
    };
  }

  function abbrNum(v) {
    v = Number(v);
    if (!isFinite(v)) return '';
    if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(1) + '亿';
    if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(1) + '万';
    return String(Math.round(v));
  }

  /* ================================================== 6. Y 轴随缩放自适应 */

  /** 计算可见窗口 [startPct, endPct]（百分比）内的坐标范围。
   *  K线主轴只按 candles 的 low/high 计算（指标如 RSI 量纲不同，不参与），
   *  另返回可见窗口内的成交量峰值与权益曲线范围。 */
  function computeVisibleRange(chartData, startPct, endPct) {
    const dates = chartData.dates || [];
    const n = dates.length;
    if (!n) return null;
    const i0 = Math.max(0, Math.min(n - 1, Math.round(startPct / 100 * (n - 1))));
    const i1 = Math.max(i0, Math.min(n - 1, Math.round(endPct / 100 * (n - 1))));
    let lo = Infinity, hi = -Infinity, vmax = 0;
    for (let i = i0; i <= i1; i++) {
      const k = (chartData.candles || [])[i];
      if (k) {
        if (k[2] != null && k[2] < lo) lo = k[2];
        if (k[3] != null && k[3] > hi) hi = k[3];
      }
      const v = (chartData.volume || [])[i];
      if (v != null && v > vmax) vmax = v;
    }
    if (lo === Infinity || hi === -Infinity) return null;
    const pad = (hi - lo) * 0.05 || (Math.abs(hi) || 1) * 0.01;

    // 权益曲线可见范围（按日期映射到窗口）
    const dmap = {};
    dates.forEach(function (d, i) { dmap[d] = i; });
    let elo = Infinity, ehi = -Infinity;
    (chartData.equity || []).forEach(function (p) {
      const i = dmap[p[0]];
      if (i != null && i >= i0 && i <= i1 && p[1] != null) {
        if (p[1] < elo) elo = p[1];
        if (p[1] > ehi) ehi = p[1];
      }
    });
    let erange = null;
    if (elo !== Infinity) {
      const epad = (ehi - elo) * 0.08 || Math.abs(ehi) * 0.01;
      erange = { min: elo - epad, max: ehi + epad };
    }
    return { min: lo - pad, max: hi + pad, volMax: vmax,
             equity: erange, from: i0, to: i1 };
  }

  /** 给 K 线图装上"Y 轴跟随缩放"：监听 datazoom，按可见窗口重设三根 Y 轴 */
  function attachAutoScale(chart, chartData) {
    if (!chart || !chartData || !(chartData.dates || []).length) return;
    function update() {
      try {
        const opt = chart.getOption();
        const dz = (opt.dataZoom || [])[0];
        if (!dz) return;
        const n = chartData.dates.length;
        let pct0, pct1;
        if (dz.startValue != null && dz.endValue != null) {
          pct0 = dz.startValue / (n - 1) * 100;
          pct1 = dz.endValue / (n - 1) * 100;
        } else {
          pct0 = dz.start; pct1 = dz.end;
        }
        const r = computeVisibleRange(chartData, pct0, pct1);
        if (!r) return;
        const yAxisPatch = [
          { min: r.min, max: r.max },
          { min: 0, max: r.volMax ? r.volMax * 1.1 : null },
        ];
        yAxisPatch.push(r.equity ? { min: r.equity.min, max: r.equity.max } : {});
        chart.setOption({ yAxis: yAxisPatch });
      } catch (e) { /* 缩放极端情况忽略 */ }
    }
    chart.on('datazoom', update);
    update();  // 初始全量窗口也统一走一次
  }

  /** 优化结果：恰好 2 个变化参数 → 热力图；否则 → 柱状图 */
  function buildOptChartOption(rows, theme) {
    const c = themeColors(theme);
    const ax = baseAxisColors(theme);
    if (!rows || !rows.length) return null;
    const keys = Object.keys(rows[0].params || {});
    const dims = keys.filter(function (k) {
      const vals = new Set(rows.map(function (r) { return String(r.params[k]); }));
      return vals.size > 1;
    });

    if (dims.length === 2) {
      const xs = [...new Set(rows.map(function (r) { return r.params[dims[0]]; }))].sort(function (a, b) { return a - b; });
      const ys = [...new Set(rows.map(function (r) { return r.params[dims[1]]; }))].sort(function (a, b) { return a - b; });
      const data = rows.map(function (r, i) {
        return [xs.indexOf(r.params[dims[0]]), ys.indexOf(r.params[dims[1]]),
        r.annual_pct === null ? 0 : r.annual_pct, i];
      });
      return {
        kind: 'heatmap',
        option: {
          animation: false, backgroundColor: 'transparent',
          tooltip: {
            position: 'top',
            backgroundColor: c.panel, borderColor: c.line,
            textStyle: { color: c.txt, fontSize: 12 },
            formatter: function (p) {
              const r = rows[p.data[3]];
              return Object.keys(r.params).map(function (k) {
                return k + '=' + r.params[k];
              }).join('<br/>') + '<br/>年化: ' + fmtNum(r.annual_pct) + '%';
            },
          },
          grid: { left: 90, right: 90, top: 20, bottom: 40 },
          xAxis: { type: 'category', data: xs, name: dims[0],
            axisLabel: { color: ax.txt }, axisLine: { lineStyle: { color: ax.axis } } },
          yAxis: { type: 'category', data: ys, name: dims[1],
            axisLabel: { color: ax.txt }, axisLine: { lineStyle: { color: ax.axis } } },
          visualMap: {
            min: -10, max: 10, calculable: true, orient: 'vertical', right: 0, top: 'center',
            textStyle: { color: ax.txt },
            inRange: { color: ['#2ecc8f', '#ffffff00', '#f2555a'].map(function (x) { return x; }) },
          },
          series: [{ type: 'heatmap', data: data,
            label: { show: true, color: c.txt, fontSize: 10,
              formatter: function (p) { return fmtNum(p.data[2], 1); } },
            itemStyle: { borderColor: c.bgPlot, borderWidth: 1 } }],
        },
      };
    }

    const labels = rows.map(function (r, i) {
      return keys.map(function (k) { return k + '=' + r.params[k]; }).join(' ');
    });
    const vals = rows.map(function (r) { return r.annual_pct === null ? 0 : r.annual_pct; });
    return {
      kind: 'bar',
      option: {
        animation: false, backgroundColor: 'transparent',
        tooltip: { backgroundColor: c.panel, borderColor: c.line,
          textStyle: { color: c.txt, fontSize: 12 } },
        grid: { left: 70, right: 20, top: 20, bottom: 60 },
        xAxis: { type: 'category', data: labels, axisLabel: { color: ax.txt,
          rotate: 30, fontSize: 10 }, axisLine: { lineStyle: { color: ax.axis } } },
        yAxis: { axisLabel: { color: ax.txt, formatter: '{value}%' },
          splitLine: { lineStyle: { color: ax.split } } },
        series: [{ type: 'bar', data: vals.map(function (v) {
          return { value: v, itemStyle: { color: v >= 0 ? c.up : c.down } };
        }) }],
      },
    };
  }

  /** 多数据对比：归一化（起点=100）权益曲线，时间轴对齐 */
  function buildCompareOption(comparison, theme) {
    const c = themeColors(theme);
    const ax = baseAxisColors(theme);
    const series = (comparison || []).map(function (cmp, i) {
      const ds = cmp.equity_dates || [], vs = cmp.equity_norm || [];
      return {
        name: cmp.data_name, type: 'line', showSymbol: false,
        data: ds.map(function (d, j) { return [d, vs[j]]; }),
        lineStyle: { width: 1.8 }, emphasis: { focus: 'series' },
      };
    });
    return {
      animation: false, backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', backgroundColor: c.panel, borderColor: c.line,
        textStyle: { color: c.txt, fontSize: 12 },
        valueFormatter: function (v) { return v == null ? '—' : v.toFixed(2); } },
      legend: { top: 0, textStyle: { color: ax.txt } },
      grid: { left: 60, right: 20, top: 34, bottom: 50 },
      xAxis: { type: 'time', axisLabel: { color: ax.txt },
        axisLine: { lineStyle: { color: ax.axis } } },
      yAxis: { scale: true, axisLabel: { color: ax.txt },
        splitLine: { lineStyle: { color: ax.split } } },
      dataZoom: [{ type: 'inside' }],
      series: series,
    };
  }

  return {
    THEME_COLORS: THEME_COLORS,
    normalizeTheme: normalizeTheme,
    getInitialTheme: getInitialTheme,
    saveTheme: saveTheme,
    toggleTheme: toggleTheme,
    applyTheme: applyTheme,
    themeColors: themeColors,
    escapeHtml: escapeHtml,
    tokenizePython: tokenizePython,
    highlightPython: highlightPython,
    wrapTerms: wrapTerms,
    fmtNum: fmtNum,
    pnlClass: pnlClass,
    abbrNum: abbrNum,
    buildCandleOption: buildCandleOption,
    buildOptChartOption: buildOptChartOption,
    buildCompareOption: buildCompareOption,
    computeVisibleRange: computeVisibleRange,
    attachAutoScale: attachAutoScale,
  };
});
