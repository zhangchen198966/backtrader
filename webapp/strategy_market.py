# -*- coding: utf-8 -*-
"""bt-lab: 策略模板市场（从公开 GitHub 仓库在线获取策略示例）

数据源：GitHub 上 mementum/backtrader 官方仓库的 samples/ 目录，
经 jsdelivr CDN 拉取（国内可达），raw.githubusercontent.com 作备用。
下载后用 AST 抽取目标策略类（含其文件内基类与全部顶层 import），
经 templatestore 校验后入库为本地模板。

目录（MARKET）为人工甄选的可运行样本：策略类自包含、带参数、不依赖实盘环境。
"""
import ast
import json
import os
import re
import time
import urllib.request

import backtrader as bt
import re as _re_html

from webapp.templatestore import (TemplateError, add_custom,
                                  parse_params_metadata,
                                  validate_strategy_code)

CDNS = [
    'https://cdn.jsdelivr.net/gh/{repo}@master/{path}',
    'https://raw.githubusercontent.com/{repo}/master/{path}',
]


class MarketError(Exception):
    """市场操作失败（网络/解析），消息直接展示给用户"""


# 人工甄选目录（repo/path 定位源码，class 指定要导入的策略类）
# 甄别标准：AST 抽取后可独立 exec、真实回测通过（lrsi/multitimeframe 等样本因
# 引用当前库不存在的指标或需要多数据源已被剔除）
# provider: official=官方仓库 samples；community=第三方社区仓库（同样经过回测验证）
MARKET = [
    {'id': 'mk-macd-settings', 'name': 'MACD 可调参数策略',
     'desc': '官方示例：MACD 指标参数（快/慢/信号）全部可调，适合参数优化练习。',
     'tags': 'MACD 动量', 'provider': 'official', 'repo': 'mementum/backtrader',
     'path': 'samples/macd-settings/macd-settings.py', 'class': 'TheStrategy'},
    {'id': 'mk-psar', 'name': '抛物线 SAR 趋势跟踪',
     'desc': '官方示例：使用 ParabolicSAR 指标的趋势策略，含绘图设置。',
     'tags': 'SAR 趋势', 'provider': 'official', 'repo': 'mementum/backtrader',
     'path': 'samples/psar/psar.py', 'class': 'St'},
    {'id': 'mk-stoptrail', 'name': '移动止损（StopTrail）',
     'desc': '官方示例：限价单入场 + StopTrail 移动止损单离场。',
     'tags': '止损 风控', 'provider': 'official', 'repo': 'mementum/backtrader',
     'path': 'samples/stoptrail/trail.py', 'class': 'St'},
    {'id': 'mk-kselrsi', 'name': 'K线信号 + RSI 过滤',
     'desc': '官方示例：K线形态信号与 RSI 结合的入场策略。',
     'tags': 'K线形态 RSI', 'provider': 'official', 'repo': 'mementum/backtrader',
     'path': 'samples/kselrsi/ksignal.py', 'class': 'TheStrategy'},
    {'id': 'mk-order-target', 'name': 'order_target 仓位管理',
     'desc': '官方示例：演示 order_target_size/value 系列按目标仓位下单的用法。',
     'tags': '仓位管理', 'provider': 'official', 'repo': 'mementum/backtrader',
     'path': 'samples/order_target/order_target.py', 'class': 'TheStrategy'},
    {'id': 'mk-bracket', 'name': 'Bracket 组合单（止盈+止损）',
     'desc': '官方示例：buy_bracket 一单三腿（主单+止盈+止损）的标准用法。',
     'tags': '组合单 风控', 'provider': 'official', 'repo': 'mementum/backtrader',
     'path': 'samples/bracket/bracket.py', 'class': 'St'},
    {'id': 'mk-optimization', 'name': '官方参数优化示例',
     'desc': '官方示例：带多参数的均线策略，专为参数优化设计（配合网格模式使用）。',
     'tags': '优化 均线', 'provider': 'official', 'repo': 'mementum/backtrader',
     'path': 'samples/optimization/optimization.py', 'class': 'OptimizeStrategy'},
    {'id': 'mk-bb-adx', 'name': '布林带 + ADX 趋势过滤',
     'desc': '第三方社区策略：布林带突破入场，ADX 过滤弱趋势行情，含完整交易日志输出。',
     'tags': '布林带 ADX 趋势', 'provider': 'community',
     'repo': 'jasgin/backtrader-backtests',
     'path': 'BollBand%20and%20ADX/BB_ADX.py', 'class': 'BBADX'},
    {'id': 'mk-stoch-sr', 'name': '随机指标 + 支撑阻力位',
     'desc': '第三方社区策略：Stochastic 指标结合支撑/阻力位判断的超买超卖反转策略。',
     'tags': 'Stochastic 支撑阻力 反转', 'provider': 'community',
     'repo': 'jasgin/backtrader-backtests',
     'path': 'StochasticSR/Stochastic_SR_Backtest.py', 'class': 'StochasticSR'},
    {'id': 'mk-sunrise-ogle', 'name': '黄金 XAUUSD 拉回窗口策略（专业级）',
     'desc': '第三方专业策略（66★）：多周期波动率扩张 + 拉回窗口入场，31 个可调参数，'
             '含完整风控。原为 XAUUSD 设计，可试用于其他品种。',
     'tags': '拉回 波动率 多周期 风控', 'provider': 'community',
     'repo': 'ilahuerta-IA/backtrader-pullback-window-xauusd',
     'path': 'src/strategy/sunrise_ogle_xauusd.py', 'class': 'SunriseOgle'},
    {'id': 'mk-lesson3', 'name': '官方教程实战策略（中文教程库）',
     'desc': '来自 2247★ 中文教程库《learn_backtrader》（量化投资与机器学习笔记）的'
             '指标组合示例，适合配合教程学习策略构建。',
     'tags': '中文 教程 入门', 'provider': 'community',
     'repo': 'jrothschild33/learn_backtrader',
     'path': 'Lesson3.py', 'class': 'MyStrategy'},
    {'id': 'mk-golden-cross', 'name': '金叉策略（可调参数）',
     'desc': '第三方社区策略：经典均线金叉/死叉，4 个可调参数，适合作为参数优化练习的起点。',
     'tags': '均线 金叉 入门', 'provider': 'community',
     'repo': 'Adonis2115/Backtesting',
     'path': 'strategies/goldenCross.py', 'class': 'GoldenCross'},
    {'id': 'mk-buy-dip', 'name': '逢跌买入策略',
     'desc': '第三方社区策略：价格回调超过阈值时买入博反弹的均值回归思路。',
     'tags': '均值回归 回调', 'provider': 'community',
     'repo': 'Adonis2115/Backtesting',
     'path': 'strategies/dip.py', 'class': 'BuyDip'},
    {'id': 'mk-ma-cross-rw', 'name': '双均线交叉（教学实现）',
     'desc': '第三方社区策略：结构清晰的双均线交叉教学实现，适合阅读源码学习 next() 写法。',
     'tags': '均线 教学', 'provider': 'community',
     'repo': '0xRobWatson/Quant-Trading-Strategy-Backtesting-Framework',
     'path': 'MA%20cross.py', 'class': 'MaCrossStrategy'},
]


def catalog(keyword=None, provider=None):
    """目录（可按关键词过滤 name/desc/tags，按来源过滤 provider）"""
    kw = (keyword or '').strip().lower()
    out = list(MARKET)
    if provider in ('official', 'community'):
        out = [m for m in out if m.get('provider') == provider]
    if kw:
        out = [m for m in out
               if kw in m['name'].lower() or kw in m['desc'].lower()
               or kw in m['tags'].lower()]
    return out


def find_market(mid):
    for m in MARKET:
        if m['id'] == mid:
            return m
    return None


# ---------------------------------------------------------------- 源码下载

def download_source(repo, path):
    """从 CDN 拉取源码（jsdelivr 优先，raw 备用）"""
    last_err = None
    for tpl in CDNS:
        url = tpl.format(repo=repo, path=path)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'bt-lab/1.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            text = data.decode('utf-8', errors='replace')
            if len(text) > 200 and 'Strategy' in text:
                return text
            last_err = f'{url} 返回内容异常'
        except Exception as e:
            last_err = f'{url}: {type(e).__name__}: {e}'
    raise MarketError(f'下载失败（可稍后重试）: {last_err}')


def extract_code_blocks(html_text):
    """从网页 HTML 提取 Python 代码块（文章型数据源用）。

    识别 <pre><code> 形式，剥内联标签、反转义，过滤含 class/def 的疑似
    Python 块，按原顺序拼接为一个伪 .py 文件。
    """
    import html as _html
    blocks = _re_html.findall(r'<pre[^>]*>\s*<code[^>]*>(.*?)</code>\s*</pre>', html_text, _re_html.S)
    if not blocks:  # 宽松模式：裸 <pre> 或 <code>
        blocks = _re_html.findall(r'<(?:pre|code)[^>]*>(.*?)</(?:pre|code)>', html_text, _re_html.S)
    py = []
    for b in blocks:
        text = _html.unescape(_re_html.sub(r'<[^>]+>', '', b))
        if 'class' in text and ('def ' in text or 'import' in text) and '{' not in text[:200]:
            py.append(text.strip('\n'))
    return '\n\n\n'.join(py)


def download_article(url):
    """文章型数据源：抓取网页 → 提取代码块 → 伪 py 文件"""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html_text = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        raise MarketError(f'网页抓取失败: {type(e).__name__}: {e}')
    code = extract_code_blocks(html_text)
    if 'Strategy' not in code or len(code) < 200:
        raise MarketError('页面中未找到有效的策略代码块')
    return code


# ---------------------------------------------------------------- AST 抽取

def _collect_bases(tree, target):
    """收集目标类及它在文件内定义的基类链（按定义顺序：基类在前）"""
    classes = {n.name: n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef)}
    seen, stack = set(), [target]
    chain = []
    while stack:
        name = stack.pop()
        if name in seen or name not in classes:
            continue
        seen.add(name)
        node = classes[name]
        chain.append(node)
        for b in node.bases:
            bn = getattr(b, 'id', None)
            if bn and bn in classes:
                stack.append(bn)
    # stack 收集顺序是子类在前，反转保证基类先定义
    return list(reversed(chain))


def _seg(source_code, lines, node):
    return ast.get_source_segment(source_code, node) or \
        '\n'.join(lines[node.lineno - 1:node.end_lineno])


def _import_matches(node, module):
    """该 import 语句是否引入了指定模块"""
    if isinstance(node, ast.Import):
        return any(a.name == module or a.name.startswith(module + '.')
                   for a in node.names)
    if isinstance(node, ast.ImportFrom):
        return node.module == module or \
            (node.module or '').startswith(module + '.')
    return False


def _top_level_names(node):
    """顶层语句定义的名字（Assign/函数/类）"""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {t.id for t in node.targets if isinstance(t, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def extract_strategy(source_code, class_name):
    """抽取可独立运行的策略代码（迭代式依赖解析）。

    从 imports + 目标类（含文件内基类）出发反复校验：
      - ModuleNotFoundError → 剪掉引入该第三方模块的 import（教程文件顶部
        常有 tushare/empyrical 等数据拉取依赖，策略类本身并不需要）
      - NameError → 从文件顶层补全该名字的定义语句（常量/函数/类）
    直到可执行；不可满足则抛 MarketError。
    """
    import re as _re
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        raise MarketError(f'源码解析失败: {e}')

    chain = _collect_bases(tree, class_name)
    if not chain:
        raise MarketError(f'源码中未找到策略类 {class_name}')

    lines = source_code.split('\n')
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    others = [n for n in tree.body
              if not isinstance(n, (ast.Import, ast.ImportFrom))]

    # 预置安全语句：函数/类定义（不执行）与字面量常量赋值（无副作用）
    def _literal_like(v):
        if isinstance(v, (ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set,
                          ast.Name, ast.Attribute)):
            return True
        if isinstance(v, ast.UnaryOp):
            return _literal_like(v.operand)
        if isinstance(v, ast.BinOp):
            return _literal_like(v.left) and _literal_like(v.right)
        return False

    safe = [n for n in others
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            or (isinstance(n, ast.Assign)
                and all(isinstance(t, ast.Name) for t in n.targets)
                and _literal_like(n.value))]

    def _initial(segs):
        parts = ['"""从模板市场导入"""']
        parts += [_seg(source_code, lines, n) for n in imports]
        parts += [_seg(source_code, lines, n) for n in segs]
        return parts, set(id(n) for n in imports) | set(id(n) for n in segs)

    # 两阶段：先带安全预置（函数/类/字面量常量），失败则回退极简模式
    # （预置可能引入教程占位类等坏语句，如 lines = (xxx,xxx)）
    phases = [safe, chain]
    last_err = None
    for phase in phases:
      try:
        parts, used = _initial(phase)
        for _round in range(60):
            code = '\n\n'.join(p for p in parts if p) + '\n'
            try:
                validate_strategy_code(code)
                return code
            except TemplateError as e:
                err = str(e)
                m = _re.search(r"No module named '([^']+)'", err)
                if m:
                    mod = m.group(1).split('.')[0]
                    dropped = False
                    for n in list(imports):
                        if id(n) in used and _import_matches(n, mod):
                            seg = _seg(source_code, lines, n)
                            parts = [p for p in parts if p != seg]
                            used.discard(id(n))
                            dropped = True
                    if dropped:
                        continue
                    raise MarketError(f'策略依赖不可用模块 {mod}')
                m2 = _re.search(r"name '([^']+)' is not defined", err)
                if m2:
                    name = m2.group(1)
                    found = next((n for n in others
                                  if id(n) not in used and name in _top_level_names(n)),
                                 None)
                    if found is not None:
                        used.add(id(found))
                        seg = _seg(source_code, lines, found)
                        n_imports_used = sum(1 for n in imports if id(n) in used)
                        parts.insert(min(1 + n_imports_used, len(parts)), seg)
                        continue
                    raise MarketError(f'策略依赖文件内未知的名字 {name}')
                raise MarketError(f'抽取的策略类不完整: {err[-300:]}')
        last_err = MarketError('依赖解析轮次耗尽')
      except MarketError as e:
        last_err = e
        continue
    raise last_err or MarketError('抽取失败')


# ---------------------------------------------------------------- 导入流程

def import_from_market(mid, name_override=None):
    """下载 → 抽取 → 校验 → 入本地模板库。返回模板条目。"""
    entry = find_market(mid)
    if not entry:
        raise MarketError(f'未知市场模板: {mid}')
    if entry.get('site') == 'article':
        source = download_article(entry['url'])
        # 文章源无指定类名：抽取代码里的第一个 Strategy 类名
        classes = _re_html.findall(r'class\s+(\w+)\s*\(?[^)\n]*Strategy', source)
        cls = entry.get('class') or (classes[0] if classes else None)
        if not cls:
            raise MarketError('未能从页面代码中识别策略类')
    else:
        source = download_source(entry['repo'], entry['path'])
        cls = entry['class']
    code = extract_strategy(source, cls)
    name = (name_override or entry['name']).strip()
    src_label = f'市场导入 · {entry.get("url")}' if entry.get('site') == 'article' \
        else f'市场导入 · {entry["repo"]}/{entry["path"]}'
    return add_custom(name, code, desc=entry['desc'], source=src_label)
