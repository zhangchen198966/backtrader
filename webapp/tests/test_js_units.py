# -*- coding: utf-8 -*-
"""JS 纯函数单元测试（node 运行 bt-lab.js 测试脚本）"""
import os
import subprocess

from webapp.tests.conftest import REPO_ROOT, PYTHON

JS_TESTS = os.path.join(REPO_ROOT, 'webapp', 'static', 'tests', 'run_js_tests.mjs')


def test_js_units():
    """前端核心逻辑（主题/高亮/术语/图表 option）单元测试全绿"""
    r = subprocess.run(['node', JS_TESTS], capture_output=True, text=True,
                       timeout=120)
    out = r.stdout + r.stderr
    assert r.returncode == 0, f'JS 测试失败:\n{out}'
    assert 'ALL PASS' in out
    # 覆盖度 sanity：四大功能域的测试组都执行了
    for section in ['== 主题 ==', '== Python 语法高亮 ==', '== 名词术语包裹 ==',
                    '== ECharts option 构建 ==']:
        assert section in out, f'缺少测试组: {section}'


def test_node_available():
    r = subprocess.run(['node', '-v'], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip().startswith('v')
