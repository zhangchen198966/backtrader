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
MARKET = [
    {'id': 'mk-macd-settings', 'name': 'MACD 可调参数策略',
     'desc': '官方示例：MACD 指标参数（快/慢/信号）全部可调，适合参数优化练习。',
     'tags': 'MACD 动量 官方samples', 'repo': 'mementum/backtrader',
     'path': 'samples/macd-settings/macd-settings.py', 'class': 'TheStrategy'},
    {'id': 'mk-psar', 'name': '抛物线 SAR 趋势跟踪',
     'desc': '官方示例：使用 ParabolicSAR 指标的趋势策略，含绘图设置。',
     'tags': 'SAR 趋势 官方samples', 'repo': 'mementum/backtrader',
     'path': 'samples/psar/psar.py', 'class': 'St'},
    {'id': 'mk-stoptrail', 'name': '移动止损（StopTrail）',
     'desc': '官方示例：限价单入场 + StopTrail 移动止损单离场。',
     'tags': '止损 风控 官方samples', 'repo': 'mementum/backtrader',
     'path': 'samples/stoptrail/trail.py', 'class': 'St'},
    {'id': 'mk-kselrsi', 'name': 'K线信号 + RSI 过滤',
     'desc': '官方示例：K线形态信号与 RSI 结合的入场策略。',
     'tags': 'K线形态 RSI 官方samples', 'repo': 'mementum/backtrader',
     'path': 'samples/kselrsi/ksignal.py', 'class': 'TheStrategy'},
    {'id': 'mk-order-target', 'name': 'order_target 仓位管理',
     'desc': '官方示例：演示 order_target_size/value 系列按目标仓位下单的用法。',
     'tags': '仓位管理 官方samples', 'repo': 'mementum/backtrader',
     'path': 'samples/order_target/order_target.py', 'class': 'TheStrategy'},
    {'id': 'mk-bracket', 'name': 'Bracket 组合单（止盈+止损）',
     'desc': '官方示例：buy_bracket 一单三腿（主单+止盈+止损）的标准用法。',
     'tags': '组合单 风控 官方samples', 'repo': 'mementum/backtrader',
     'path': 'samples/bracket/bracket.py', 'class': 'St'},
    {'id': 'mk-optimization', 'name': '官方参数优化示例',
     'desc': '官方示例：带多参数的均线策略，专为参数优化设计（配合网格模式使用）。',
     'tags': '优化 均线 官方samples', 'repo': 'mementum/backtrader',
     'path': 'samples/optimization/optimization.py', 'class': 'OptimizeStrategy'},
]


def catalog(keyword=None):
    """目录（可按关键词过滤 name/desc/tags）"""
    kw = (keyword or '').strip().lower()
    if not kw:
        return list(MARKET)
    return [m for m in MARKET
            if kw in m['name'].lower() or kw in m['desc'].lower()
            or kw in m['tags'].lower()]


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


def extract_strategy(source_code, class_name):
    """抽取 imports + 目标类（含文件内基类）的源码段。

    返回拼接后的可执行策略代码；找不到类抛 MarketError。
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        raise MarketError(f'源码解析失败: {e}')

    chain = _collect_bases(tree, class_name)
    if not chain:
        raise MarketError(f'源码中未找到策略类 {class_name}')

    lines = source_code.split('\n')

    imports = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.get_source_segment(source_code, node)
                           or '\n'.join(lines[node.lineno - 1:node.end_lineno]))

    parts = ['"""从模板市场导入"""'] + imports
    for node in chain:
        seg = ast.get_source_segment(source_code, node)
        if seg:
            parts.append(seg)

    code = '\n\n'.join(parts) + '\n'
    # 二次校验：抽取结果必须可执行且包含 Strategy 子类
    try:
        validate_strategy_code(code)
    except TemplateError as e:
        raise MarketError(f'抽取的策略类不完整（可能依赖文件内其他函数）: {e}')
    return code


# ---------------------------------------------------------------- 导入流程

def import_from_market(mid, name_override=None):
    """下载 → 抽取 → 校验 → 入本地模板库。返回模板条目。"""
    entry = find_market(mid)
    if not entry:
        raise MarketError(f'未知市场模板: {mid}')
    source = download_source(entry['repo'], entry['path'])
    code = extract_strategy(source, entry['class'])
    name = (name_override or entry['name']).strip()
    return add_custom(name, code, desc=entry['desc'],
                      source=f'市场导入 · {entry["repo"]}/{entry["path"]}')
