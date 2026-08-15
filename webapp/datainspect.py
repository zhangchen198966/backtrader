# -*- coding: utf-8 -*-
"""bt-lab: CSV 数据文件探测（分隔符/表头/日期格式/行数）"""
import csv
import os
from datetime import datetime

DTFORMATS = ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y/%m/%d',
             '%Y-%m-%dT%H:%M:%S', '%d/%m/%Y', '%m/%d/%Y', '%Y%m%d']

SEPARATORS = [',', ';', '\t']


def _sniff_sep(line):
    counts = {s: line.count(s) for s in SEPARATORS}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else None


def _is_float(token):
    try:
        float(token)
        return True
    except ValueError:
        return False


def _sniff_header(first_tokens):
    """首行所有字段都不是数值 → 认为是表头"""
    if not first_tokens:
        return False
    return all(not _is_float(t) for t in first_tokens)


def _sniff_dtformat(token):
    for fmt in DTFORMATS:
        try:
            datetime.strptime(token, fmt)
            return fmt
        except ValueError:
            continue
    return None


def inspect_csv(path):
    """探测一个 CSV 文件的结构。返回 dict（见 bt-lab 规格）。

    支持两种时间布局：第 0 列完整日期时间；或第 0 列日期 + 第 1 列时间
    （此时返回 time_col=1 和 tmformat，加载时列号整体后移一位）。
    """
    result = {'ok': False, 'error': None, 'sep': None, 'dtformat': None,
              'time_col': -1, 'tmformat': None, 'header': False,
              'columns': [], 'ncols': 0, 'rows': 0,
              'date_first': None, 'date_last': None, 'preview': []}
    try:
        with open(path, 'r', newline='', encoding='utf-8-sig', errors='replace') as f:
            lines = [ln.rstrip('\r\n') for ln in f if ln.strip()]
    except OSError as e:
        result['error'] = f'无法读取文件: {e}'
        return result

    if len(lines) < 2:
        result['error'] = '有效数据行不足 2 行'
        return result

    result['preview'] = lines[:3]
    sep = _sniff_sep(lines[0])
    if sep is None:
        result['error'] = '无法识别分隔符（支持 , ; Tab）'
        return result
    result['sep'] = sep

    rows = [next(csv.reader([ln], delimiter=sep)) for ln in lines]
    first, data_rows = rows[0], rows[1:]

    # 列数以数据行为准（个别测试文件表头会缺列）
    result['ncols'] = max(len(r) for r in data_rows)

    if _sniff_header(first):
        result['header'] = True
        result['columns'] = [t.strip() for t in first]
        if len(result['columns']) < result['ncols']:
            result['columns'] += ['col%d' % i for i in
                                  range(len(result['columns']), result['ncols'])]
    else:
        data_rows = rows  # 无表头，全部是数据
        result['columns'] = ['col%d' % i for i in range(result['ncols'])]

    result['rows'] = len(data_rows)

    # 时间列探测：数据第 1 列在所有样本上都能按 %H:%M(:%S) 解析
    samples = [data_rows[0], data_rows[len(data_rows) // 2], data_rows[-1]]
    time_col = -1
    tmformat = None
    if result['ncols'] >= 8:
        for fmt in ('%H:%M:%S', '%H:%M'):
            try:
                for row in samples:
                    datetime.strptime(row[1].strip(), fmt)
                time_col, tmformat = 1, fmt
                break
            except (ValueError, IndexError):
                continue

    # 日期格式：日期列上探测（首/中/尾三条样本全部通过才算）
    dtformat = None
    for fmt in DTFORMATS:
        try:
            for row in samples:
                datetime.strptime(row[0].strip(), fmt)
            dtformat = fmt
            break
        except (ValueError, IndexError):
            continue
    if dtformat is None:
        result['error'] = ('第 0 列无法按已知格式解析为日期，'
                           '请手动指定日期格式')
        return result
    result['dtformat'] = dtformat
    if time_col >= 0:
        result['time_col'], result['tmformat'] = time_col, tmformat
        date_first = ' '.join(t.strip() for t in (data_rows[0][0], data_rows[0][time_col]))
        date_last = ' '.join(t.strip() for t in (data_rows[-1][0], data_rows[-1][time_col]))
    else:
        date_first, date_last = data_rows[0][0], data_rows[-1][0]
    result['date_first'] = date_first
    result['date_last'] = date_last

    result['ok'] = True
    return result


if __name__ == '__main__':
    datas_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'datas')
    for name in sorted(os.listdir(datas_dir)):
        if not name.endswith(('.txt', '.csv')):
            continue
        info = inspect_csv(os.path.join(datas_dir, name))
        print(f"{name:40s} ok={info['ok']} dt={info['dtformat']} "
              f"rows={info['rows']} header={info['header']} err={info['error']}")
