# -*- coding: utf-8 -*-
"""bt-lab Web 服务：FastAPI + 串行任务队列

启动（仓库根目录）:
    .venv/bin/python -m uvicorn webapp.server:app --port 8600
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from webapp.datainspect import inspect_csv
from webapp.datasource import (PROVIDERS, FetchError, fetch_to_csv,
                               search_indexes, search_stocks)
from webapp.glossary import get_glossary
from webapp.strategy_market import MarketError, catalog, import_from_market
from webapp.templatestore import (TemplateError, add_custom, delete_custom,
                                  find_custom, list_custom)
from webapp.templates import TEMPLATES, get_template

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBAPP_DIR = os.path.join(REPO_ROOT, 'webapp')
TASKS_DIR = os.path.join(WEBAPP_DIR, 'tasks')
UPLOADS_DIR = os.path.join(WEBAPP_DIR, 'uploads')
STATIC_DIR = os.path.join(WEBAPP_DIR, 'static')
DATAS_DIR = os.path.join(REPO_ROOT, 'datas')

TASK_TIMEOUT = 30 * 60  # 子进程硬超时（秒）

os.makedirs(TASKS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

app = FastAPI(title='bt-lab')
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


# ---------------------------------------------------------------- 任务管理

class TaskManager:
    """串行任务队列：worker 线程逐个以子进程方式执行任务目录里的请求"""

    def __init__(self):
        self.queue = deque()
        self.lock = threading.Lock()
        self.current = None          # 当前任务目录
        self.proc = None             # 当前子进程
        self.worker = threading.Thread(target=self._loop, daemon=True)
        self.worker.start()

    def submit(self, request):
        task_id = time.strftime('%Y%m%d-%H%M%S') + '-%04x' % (int(time.time() * 16) & 0xffff)
        task_dir = os.path.join(TASKS_DIR, task_id)
        os.makedirs(task_dir)
        with open(os.path.join(task_dir, 'request.json'), 'w') as f:
            json.dump(request, f, ensure_ascii=False)
        self._write_status(task_dir, 'queued')
        with self.lock:
            self.queue.append(task_dir)
        return task_id

    def kill(self, task_id):
        task_dir = os.path.join(TASKS_DIR, task_id)
        with self.lock:
            is_current = self.current == task_dir
        if not is_current:
            if os.path.isdir(task_dir):
                self._write_status(task_dir, 'killed')
            return False
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._write_status(task_dir, 'killed')
        return True

    def _write_status(self, task_dir, status):
        with open(os.path.join(task_dir, 'status'), 'w') as f:
            f.write(status)

    def _loop(self):
        while True:
            with self.lock:
                task_dir = self.queue.popleft() if self.queue else None
                if task_dir:
                    self.current = os.path.basename(task_dir)
            if not task_dir:
                time.sleep(0.2)
                continue
            try:
                self._run_task(task_dir)
            finally:
                with self.lock:
                    self.current = None

    def _run_task(self, task_dir):
        # 已经被 kill 的排队任务不再执行
        try:
            with open(os.path.join(task_dir, 'status')) as f:
                if f.read().strip() == 'killed':
                    return
        except OSError:
            pass

        self._write_status(task_dir, 'running')
        proc = subprocess.Popen(
            [sys.executable, '-m', 'webapp.runner', task_dir],
            cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self.lock:
            self.proc = proc
        try:
            proc.wait(timeout=TASK_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            with open(os.path.join(task_dir, 'error.txt'), 'a') as f:
                f.write('\n任务超时（%d 分钟）被终止' % (TASK_TIMEOUT // 60))
            self._write_status(task_dir, 'error')


manager = TaskManager()


# ---------------------------------------------------------------- 任务读取

def _read_task(task_id):
    if not re.fullmatch(r'[0-9a-zA-Z\-]+', task_id):
        raise HTTPException(404, '任务不存在')
    task_dir = os.path.join(TASKS_DIR, task_id)
    if not os.path.isdir(task_dir):
        raise HTTPException(404, '任务不存在')

    out = {'id': task_id, 'status': 'unknown'}
    try:
        with open(os.path.join(task_dir, 'status')) as f:
            out['status'] = f.read().strip()
    except OSError:
        pass
    try:
        with open(os.path.join(task_dir, 'error.txt')) as f:
            out['error'] = f.read()
    except OSError:
        pass
    try:
        with open(os.path.join(task_dir, 'request.json')) as f:
            out['request'] = json.load(f)
    except OSError:
        pass

    # 优化进度
    prog_path = os.path.join(task_dir, 'progress.jsonl')
    if os.path.isfile(prog_path):
        done = 0
        with open(prog_path) as f:
            for _ in f:
                done += 1
        total = done
        req = out.get('request') or {}
        if req.get('mode') == 'optimize':
            total = 1
            for val in (req.get('strategy', {}).get('params') or {}).values():
                if isinstance(val, dict) and 'start' in val:
                    step = float(val.get('step') or 1)
                    n = int((float(val['end']) - float(val['start'])) / step) + 1
                    total *= max(1, n)
        out['progress'] = {'done': done, 'total': total}

    if out['status'] == 'done':
        try:
            with open(os.path.join(task_dir, 'result.json')) as f:
                out['result'] = json.load(f)
        except OSError:
            out['status'] = 'error'
            out['error'] = 'result.json 缺失'
    return out


def _list_datas(directory):
    items = []
    if not os.path.isdir(directory):
        return items
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(('.txt', '.csv')):
            continue
        path = os.path.join(directory, name)
        info = inspect_csv(path)
        items.append({
            'name': name,
            'path': os.path.relpath(path, REPO_ROOT),
            'ok': info['ok'],
            'rows': info['rows'],
            'dtformat': info['dtformat'],
            'time_col': info['time_col'],
            'date_first': info['date_first'],
            'date_last': info['date_last'],
        })
    return items


# ---------------------------------------------------------------- API

class InspectBody(BaseModel):
    path: str


class FetchBody(BaseModel):
    provider: str
    symbol: str
    start: str
    end: str


class RunBody(BaseModel):
    mode: str = 'backtest'


@app.get('/')
def index():
    return FileResponse(os.path.join(STATIC_DIR, 'index.html'))


@app.get('/api/datas')
def api_datas():
    return {'builtin': _list_datas(DATAS_DIR), 'uploads': _list_datas(UPLOADS_DIR)}


@app.post('/api/datas/inspect')
def api_inspect(body: InspectBody):
    path = body.path
    if not os.path.isabs(path):
        path = os.path.join(REPO_ROOT, path)
    if not os.path.isfile(path):
        raise HTTPException(404, '文件不存在')
    return inspect_csv(path)


@app.post('/api/datas/upload')
async def api_upload(file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > 64 * 1024 * 1024:
        raise HTTPException(413, '文件超过 64MB')
    name = os.path.basename(file.filename or 'upload.csv')
    name = re.sub(r'[^\w.\-]+', '_', name) or 'upload.csv'
    dest = os.path.join(UPLOADS_DIR, name)
    with open(dest, 'wb') as f:
        f.write(raw)
    info = inspect_csv(dest)
    info['path'] = os.path.relpath(dest, REPO_ROOT)
    return info


@app.get('/api/datas/providers')
def api_providers():
    """在线数据源列表（前端下拉）"""
    return {'providers': [
        {'id': pid, 'label': m['label'], 'hint': m['hint']}
        for pid, m in PROVIDERS.items()
    ]}


@app.get('/api/datas/search')
def api_search(q: str = '', provider: str = 'akshare-a'):
    """股票/指数代码搜索（支持中文名）。清单加载失败不影响手输代码。"""
    try:
        if provider == 'akshare-index':
            results = search_indexes(q)
        elif provider == 'yfinance':
            results = []  # 无中文名清单，保持手输
        else:
            results = search_stocks(q)
        return {'results': results}
    except Exception as e:
        return {'results': [], 'error': f'名称清单暂不可用（{e}），可直接输入代码'}


@app.post('/api/datas/fetch')
def api_fetch(body: FetchBody):
    """在线拉取数据 → 清洗落盘 uploads/ → 返回探测结果"""
    try:
        r = fetch_to_csv(body.provider, body.symbol, body.start, body.end,
                         UPLOADS_DIR)
    except FetchError as e:
        raise HTTPException(400, str(e))
    path = os.path.relpath(r['path'], REPO_ROOT)
    info = inspect_csv(r['path'])
    info['path'] = path
    info['fetched_rows'] = r['rows']
    if not info.get('ok'):
        raise HTTPException(500, '数据已下载但解析失败: %s' % info.get('error'))
    return info


@app.delete('/api/datas')
def api_datas_delete(path: str):
    """删除上传的数据文件（仅限 uploads/，框架自带数据不可删）"""
    target = path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)
    real = os.path.realpath(target)
    uploads_real = os.path.realpath(UPLOADS_DIR)
    if not real.startswith(uploads_real + os.sep):
        raise HTTPException(403, '只能删除自己上传/拉取的数据文件')
    if not os.path.isfile(real):
        raise HTTPException(404, '文件不存在')
    os.remove(real)
    return {'deleted': path}


@app.get('/api/strategy/templates')
def api_templates():
    """内置模板 + 本地保存的模板（自定义/市场导入）"""
    custom = list_custom()
    return {'templates': TEMPLATES + custom,
            'custom_ids': [t['id'] for t in custom]}


@app.post('/api/strategy/templates/custom')
def api_save_template(body: dict):
    """自定义代码保存为本地模板（校验必须含 bt.Strategy 子类）"""
    try:
        entry = add_custom(body.get('name') or '', body.get('code') or '')
    except TemplateError as e:
        raise HTTPException(400, str(e))
    return {'template': entry}


@app.delete('/api/strategy/templates/custom/{tid}')
def api_delete_template(tid: str):
    if not delete_custom(tid):
        raise HTTPException(404, '模板不存在')
    return {'deleted': tid}


@app.get('/api/strategy/market')
def api_market(q: str = '', provider: str = ''):
    """在线模板市场目录（关键词 + 来源过滤）"""
    return {'market': catalog(q, provider or None)}


@app.post('/api/strategy/market/import')
def api_market_import(body: dict):
    """从市场导入：下载源码 → 抽取策略类 → 校验 → 入本地模板库"""
    try:
        entry = import_from_market(body.get('id') or '',
                                   name_override=body.get('name'))
    except MarketError as e:
        raise HTTPException(400, str(e))
    except TemplateError as e:
        raise HTTPException(400, str(e))
    return {'template': entry}


@app.get('/api/terms')
def api_terms():
    """术语表：页面名词解释 tips 的数据源"""
    return {'terms': get_glossary()}


@app.post('/api/run')
def api_run(body: dict):
    if body.get('mode') not in ('backtest', 'optimize'):
        raise HTTPException(400, 'mode 必须是 backtest 或 optimize')
    path = (body.get('data') or {}).get('path')
    if isinstance(path, list):
        if not path:
            raise HTTPException(400, '未选择数据文件')
    elif not path:
        raise HTTPException(400, '未选择数据文件')

    # 本地保存的模板在提交时解析成代码放进请求：
    # runner 是子进程，不依赖本进程的存储路径
    strat = body.get('strategy') or {}
    tid = strat.get('template_id')
    if strat.get('source') == 'template' and tid and not get_template(tid):
        cust = find_custom(tid)
        if not cust:
            raise HTTPException(400, f'未知策略模板: {tid}')
        strat['code'] = cust['code']
        strat['resolved_params_meta'] = cust.get('params') or []

    task_id = manager.submit(body)
    return {'task_id': task_id}


@app.get('/api/task/{task_id}')
def api_task(task_id: str):
    return _read_task(task_id)


@app.post('/api/task/{task_id}/kill')
def api_task_kill(task_id: str):
    ok = manager.kill(task_id)
    return {'killed': ok}


@app.get('/api/tasks')
def api_tasks():
    """最近任务列表（含策略/参数/核心结果摘要，供历史面板展示）"""
    out = []
    for task_id in sorted(os.listdir(TASKS_DIR), reverse=True)[:20]:
        task_dir = os.path.join(TASKS_DIR, task_id)
        if not os.path.isdir(task_dir):
            continue
        item = {'id': task_id, 'status': 'unknown'}
        try:
            with open(os.path.join(task_dir, 'status')) as f:
                item['status'] = f.read().strip()
        except OSError:
            continue
        try:
            with open(os.path.join(task_dir, 'request.json')) as f:
                req = json.load(f)
            strat = req.get('strategy', {})
            item['mode'] = req.get('mode')
            raw_path = (req.get('data') or {}).get('path', '')
            if isinstance(raw_path, list):
                item['data'] = '+'.join(os.path.splitext(os.path.basename(p))[0]
                                        for p in raw_path)
            else:
                item['data'] = os.path.splitext(os.path.basename(raw_path))[0]
            item['strategy'] = strat.get('template_id') or \
                ('自定义代码' if strat.get('source') == 'custom' else '')
            params = strat.get('params') or {}
            item['params'] = ' '.join(
                f'{k}={v}' for k, v in params.items()
                if not isinstance(v, dict))[:120]
        except (OSError, ValueError, TypeError):
            pass
        # 结果摘要
        try:
            with open(os.path.join(task_dir, 'result.json')) as f:
                res = json.load(f)
            if res.get('mode') == 'optimize':
                rows = res.get('rows') or []
                best = rows[0]['annual_pct'] if rows else None
                item['summary'] = f"{res.get('total', 0)} 组 · 最优 " \
                    f"{('+' if (best or 0) > 0 else '')}{best}%"
            else:
                runs = res.get('runs') or [{}]
                s = runs[0].get('summary') or {}
                tr = s.get('total_return_pct')
                item['summary'] = \
                    f"收益 {('+' if (tr or 0) > 0 else '')}{tr}%"
                if len(runs) > 1:
                    item['summary'] += f" · {len(runs)} 数据对比"
        except (OSError, ValueError):
            pass
        out.append(item)
    return {'tasks': out}


@app.delete('/api/task/{task_id}')
def api_task_delete(task_id: str):
    """删除一个历史任务（运行中的任务需先终止）"""
    if not re.fullmatch(r'[0-9a-zA-Z\-]+', task_id):
        raise HTTPException(404, '任务不存在')
    task_dir = os.path.join(TASKS_DIR, task_id)
    if not os.path.isdir(task_dir):
        raise HTTPException(404, '任务不存在')
    with manager.lock:
        is_current = manager.current == task_id
    if is_current:
        raise HTTPException(400, '任务正在运行，请先终止')
    if task_id in manager.queue:
        with manager.lock:
            manager.queue = deque(t for t in manager.queue if t != task_id)
    import shutil
    shutil.rmtree(task_dir, ignore_errors=True)
    return {'deleted': task_id}
