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
from webapp.glossary import get_glossary
from webapp.templates import TEMPLATES

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


@app.get('/api/strategy/templates')
def api_templates():
    return {'templates': TEMPLATES}


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
    out = []
    for task_id in sorted(os.listdir(TASKS_DIR), reverse=True)[:10]:
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
                raw_path = ','.join(os.path.basename(p) for p in raw_path)
            item['data'] = os.path.basename(raw_path)
            tpl = strat.get('template_id') or ('自定义代码' if strat.get('source') == 'custom' else '')
            item['strategy'] = tpl
        except (OSError, ValueError, TypeError):
            pass
        out.append(item)
    return {'tasks': out}
