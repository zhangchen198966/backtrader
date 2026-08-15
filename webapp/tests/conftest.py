# -*- coding: utf-8 -*-
"""bt-lab 测试公共 fixtures"""
import json
import os
import subprocess
import sys
import time
import uuid

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEBAPP_DIR = os.path.join(REPO_ROOT, 'webapp')
PYTHON = sys.executable


def run_runner(request_dict, tmp_path):
    """直接以子进程方式跑一次 runner，返回 (status, result|None, error|None)"""
    task_dir = str(tmp_path / ('task-' + uuid.uuid4().hex[:8]))
    os.makedirs(task_dir)
    with open(os.path.join(task_dir, 'request.json'), 'w') as f:
        json.dump(request_dict, f, ensure_ascii=False)
    subprocess.run([PYTHON, '-m', 'webapp.runner', task_dir],
                   cwd=REPO_ROOT, capture_output=True, timeout=300)
    with open(os.path.join(task_dir, 'status')) as f:
        status = f.read().strip()
    result = error = None
    rp = os.path.join(task_dir, 'result.json')
    ep = os.path.join(task_dir, 'error.txt')
    if os.path.isfile(rp):
        with open(rp) as f:
            result = json.load(f)
    if os.path.isfile(ep):
        with open(ep) as f:
            error = f.read()
    return status, result, error


def simple_backtest_request(paths, template='sma_rsi_atr', params=None, mode='backtest'):
    p = params if params is not None else {'fast': 10, 'slow': 30}
    return {
        'mode': mode,
        'data': {'path': paths, 'dtformat': 'auto', 'columns': None},
        'strategy': {'source': 'template', 'template_id': template,
                     'code': None, 'params': p},
        'broker': {'cash': 100000, 'commission': 0.001, 'slippage': 0.0005},
        'sizer': {'type': 'percent', 'value': 90},
    }


@pytest.fixture(scope='session')
def live_server():
    """在 8601 端口启动真实服务（E2E 用）；预置迷你股票清单缓存避免测试触网"""
    import httpx
    import socket
    cache_dir = os.path.join(WEBAPP_DIR, 'cache')
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, 'stock_list.json')
    if not os.path.isfile(cache_file):
        seed = [
            {'code': '600519', 'name': '贵州茅台'},
            {'code': '000858', 'name': '五粮液'},
            {'code': '300750', 'name': '宁德时代'},
            {'code': '000002', 'name': '万  科Ａ'},
            {'code': '600036', 'name': '招商银行'},
            {'code': '000001', 'name': '平安银行'},
        ]
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(seed, f, ensure_ascii=False)

    port = 8601
    with socket.socket() as s:
        s.bind(('127.0.0.1', port))
    proc = subprocess.Popen(
        [PYTHON, '-m', 'uvicorn', 'webapp.server:app', '--port', str(port),
         '--log-level', 'warning'],
        cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'
    try:
        for _ in range(60):
            try:
                if httpx.get(base + '/api/tasks', timeout=2,
                             trust_env=False).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            proc.terminate()
            raise RuntimeError('测试服务启动失败')
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
