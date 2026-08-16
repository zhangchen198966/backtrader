# -*- coding: utf-8 -*-
"""bt-lab: 自定义/市场导入模板的本地存储

存储文件 webapp/templates_custom.json，结构与内置模板一致：
  {id, name, desc, code, params: [...], source}
params 元数据由 AST 从 `params = dict(...)` 自动解析（名字/默认值/整数性）。
"""
import ast
import json
import os
import re
import sys
import time
import types

import backtrader as bt

STORE_PATH = os.environ.get(
    'BT_LAB_TPL_STORE',
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 'templates_custom.json'))


class TemplateError(Exception):
    """模板校验失败，消息直接展示给用户"""


# ---------------------------------------------------------------- 校验（复用 runner 的模块注册手法）

def validate_strategy_code(code):
    """校验代码可 exec 且定义了 bt.Strategy 子类，返回类名列表"""
    if not (code or '').strip():
        raise TemplateError('策略代码为空')
    modname = 'btlab_tplcheck_%d' % int(time.time() * 1000000)
    mod = types.ModuleType(modname)
    mod.bt = bt
    sys.modules[modname] = mod
    try:
        exec(compile(code, '<template-code>', 'exec'), mod.__dict__)
    except SyntaxError:
        del sys.modules[modname]
        raise TemplateError('策略代码语法错误:\n' + _short_tb())
    except Exception:
        del sys.modules[modname]
        raise TemplateError('策略代码执行错误:\n' + _short_tb())
    ns = mod.__dict__
    names = [k for k, v in ns.items()
             if isinstance(v, type) and issubclass(v, bt.Strategy)
             and v.__module__ == modname
             and v not in (bt.Strategy, bt.SignalStrategy, bt.StrategyBase)]
    del sys.modules[modname]
    if not names:
        raise TemplateError('代码中未找到 bt.Strategy 的子类')
    return names


def _short_tb():
    import traceback
    return traceback.format_exc()[-1500:]


# ---------------------------------------------------------------- 参数元数据自动解析

def parse_params_metadata(code):
    """AST 解析代码里第一个 bt.Strategy 子类的 params 定义。

    支持两种官方写法：
      params = dict(fast=10, slow=30)
      params = (('period', 10), ('devfactor', 2.0),)   # backtrader 样本常用
    返回 [{name, label, default, step, int}]；无 params 返回 []。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # 兼容 class X(bt.Strategy) 与 class X(Strategy) 两种基类写法
        try:
            bases = ' '.join(ast.unparse(b) for b in node.bases)
        except Exception:
            bases = ''
        if 'Strategy' not in bases:
            continue
        for item in node.body:
            targets = getattr(item, 'targets', None) or []
            if not targets or getattr(targets[0], 'id', '') != 'params':
                continue
            metas = []
            # 形式一：params = dict(k=v, ...)
            if isinstance(item.value, ast.Call) and \
                    getattr(item.value.func, 'id', '') == 'dict':
                for kw in item.value.keywords:
                    v = _literal_or_none(kw.value)
                    if v is not None:
                        metas.append(_meta_of(kw.arg, v))
            # 形式二：params = ((k, v), ...)
            elif isinstance(item.value, (ast.Tuple, ast.List)):
                for elt in item.value.elts:
                    if isinstance(elt, (ast.Tuple, ast.List)) and len(elt.elts) == 2:
                        k = _literal_or_none(elt.elts[0])
                        v = _literal_or_none(elt.elts[1])
                        if isinstance(k, str) and v is not None:
                            metas.append(_meta_of(k, v))
            if metas:
                return metas
    return []


def _literal_or_none(node):
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _meta_of(name, val):
    is_int = isinstance(val, int) and not isinstance(val, bool)
    return {
        'name': name,
        'label': name,
        'default': val,
        'min': 0,
        'max': 10000,
        'step': 1 if is_int else 0.5,
        'int': is_int,
        'unit': '',
    }


# ---------------------------------------------------------------- 存储

def _load_all():
    if not os.path.isfile(STORE_PATH):
        return []
    try:
        with open(STORE_PATH, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_all(entries):
    with open(STORE_PATH, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)


def list_custom():
    return _load_all()


def find_custom(tid):
    for t in _load_all():
        if t['id'] == tid:
            return t
    return None


def _new_id(name):
    base = re.sub(r'[^\w]+', '-', name).strip('-').lower() or 'tpl'
    tid = f'custom-{base}'
    ids = {t['id'] for t in _load_all()}
    if tid not in ids:
        return tid
    i = 2
    while f'{tid}{i}' in ids:
        i += 1
    return f'{tid}{i}'


def add_custom(name, code, desc='', source='用户自定义'):
    """校验并保存一个自定义模板；同名覆盖更新。返回完整条目。"""
    name = (name or '').strip()
    if not name:
        raise TemplateError('请填写模板名称')
    if len(name) > 60:
        raise TemplateError('模板名称过长（≤60 字符）')
    names = validate_strategy_code(code)   # 校验（错误直接抛出）
    params = parse_params_metadata(code)
    entries = _load_all()
    tid = _new_id(name)
    # 同名覆盖
    entries = [e for e in entries if e['name'] != name]
    entry = {
        'id': tid,
        'name': name,
        'desc': desc or f'来自{source} · 策略类 {names[0]}' +
                (f' · {len(params)} 个参数' if params else ''),
        'code': code,
        'params': params,
        'source': source,
        'strat_class': names[0],
        'created': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    entries.append(entry)
    _save_all(entries)
    return entry


def delete_custom(tid):
    entries = _load_all()
    remain = [e for e in entries if e['id'] != tid]
    if len(remain) == len(entries):
        return False
    _save_all(remain)
    return True
