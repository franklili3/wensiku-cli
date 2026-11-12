#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wensiku-cli — 问思库本地文件评审命令行（方案 B：文件不出你的机器）

隐私模型（开源可审计）：
  1. 文件只在本机解析（本文件内的 extract_file_text，无任何上传代码路径）
  2. 通过 --api-url 调问思库 /api/cli/context 拿"检索上下文"——外发的只有
     你的评审指令/问题文本（用于语料检索），不含文件内容
  3. 生成由本机直连智谱（你自己的 API key，环境变量 ZHIPU_API_KEY）——
     文件内容只到达你签约的模型服务商，不经过问思库服务器
  4. 引文核验（逐字比对金句库、每人至多一句）在本机完成

用法：
  ZHIPU_API_KEY=sk-xxx ./wensiku.py 复盘.txt --code WSK-XXXXXX
  ./wensiku.py bp.md --code WSK-XXXXXX --mentors pg,altman --focus '重点看风险'
  ./wensiku.py 合同.pdf --raw                # 只解析不生成（零外发）
依赖：Python 3.10+，纯标准库；PDF 需 pip install pypdf（可选）
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# .env 支持（存在则加载，不覆盖已有环境变量；仓库根或当前目录）
for _p in (os.path.join(os.getcwd(), '.env'), os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')):
    if os.path.isfile(_p):
        for _l in open(_p):
            _l = _l.strip()
            if '=' in _l and not _l.startswith('#') and _l.split('=', 1)[0].strip() not in os.environ:
                os.environ[_l.split('=', 1)[0].strip()] = _l.split('=', 1)[1].strip()
        break

FILE_MAX_BYTES = 1_500_000
FILE_TEXT_LIMIT = 12000
FILE_EXTS = {'.txt', '.md', '.csv', '.json', '.log', '.pdf'}
DEFAULT_API = 'https://aiprep.com.cn/wensiku/api'
DEFAULT_REVIEW_Q = ('请评审我上传的这份文件：概括其核心观点，指出亮点与主要风险/漏洞，'
                    '并结合你自己的方法论给出具体的改进建议')
GLM_URL = 'https://open.bigmodel.cn/api/paas/v4/chat/completions'


def extract_file_text(name, path):
    """本地解析：txt/md/csv/json/log 直读，PDF 走 pypdf（可选依赖）"""
    raw = open(path, 'rb').read()
    if len(raw) > FILE_MAX_BYTES:
        raise ValueError(f'文件超过 1.5MB 上限（当前 {len(raw)/1e6:.1f}MB）')
    ext = os.path.splitext(name)[1].lower()
    if ext == '.pdf':
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError('PDF 支持需要先安装：pip install pypdf')
        import io
        pages = PdfReader(io.BytesIO(raw)).pages[:30]
        text = '\n'.join((p.extract_text() or '') for p in pages)
        if not text.strip():
            raise ValueError('PDF 中没提取到文字（扫描件请先转文字版）')
    elif ext in FILE_EXTS:
        text = raw.decode('utf-8', 'replace')
    else:
        raise ValueError(f'不支持的文件类型 {ext or "（无扩展名）"}，支持：txt/md/csv/json/log/pdf')
    text = text.strip()
    if not text:
        raise ValueError('文件内容为空')
    return text[:FILE_TEXT_LIMIT] + ('…（超长已截断）' if len(text) > FILE_TEXT_LIMIT else '')


def _post(url, payload, headers, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json', **headers})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for wait in (0, 10, 20):
        if wait:
            time.sleep(wait)
        try:
            r = json.loads(op.open(req, timeout=timeout).read())
            return r
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')[:200]
            if e.code in (429, 500, 502, 503):
                continue
            sys.exit(f'✗ HTTP {e.code}：{body}')
    sys.exit('✗ 多次重试后仍失败（限流/网络）')


# ── 引文纪律（与问思库服务端同规则的开源实现）──
def _norm(s):
    s = s.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"')
    s = s.replace('—', '-').replace('–', '-')
    return re.sub(r'\s+', ' ', s).strip().lower()


def quote_source_ok(text, quotes):
    """每处引号引文（≥15 字符）必须逐字来自金句库候选（原文或译文）"""
    if not quotes:
        return True
    allowed = [_norm(q.get('text', '')) for q in quotes if q.get('text')]
    allowed += [_norm(q.get('zh', '')) for q in quotes if q.get('zh')]
    for span in re.findall(r'["“]([^"”]{15,300})["”]', text):
        ns = _norm(span)
        if not any(ns in a for a in allowed):
            return False
    return True


def overquoted(text):
    pat = re.compile(r'[（(]\s*([A-Za-z\u4e00-\u9fff][A-Za-z .\'\-\u4e00-\u9fff]{0,40}?),\s*(?=\d{4}|[A-Za-z\u4e00-\u9fff])')
    counts = {}
    for m in pat.finditer(text):
        counts[m.group(1).strip()] = counts.get(m.group(1).strip(), 0) + 1
    return sum(max(0, c - 1) for c in counts.values())


def generate_verified(system, user_msg, quotes, label):
    """生成 + 引文核验，违规带意见重写一次（与问思库网页版同款纪律）"""
    key = os.environ.get('ZHIPU_API_KEY', '')
    if not key:
        sys.exit('✗ 缺 ZHIPU_API_KEY 环境变量（在智谱开放平台创建，仅在本机使用）')
    msgs = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user_msg}]
    best = None
    answer = _post(GLM_URL, {'model': 'glm-4.6', 'messages': msgs, 'max_tokens': 8192,
                            'thinking': {'type': 'disabled'}},
                   {'Authorization': 'Bearer ' + key})['choices'][0]['message']['content']
    for corrected in (False, True):
        over, src_ok = overquoted(answer), quote_source_ok(answer, quotes)
        if not over and src_ok:
            return answer
        if corrected:
            return answer if best is None else best
        best = answer
        issues = []
        if over:
            issues.append('同一人物引用超出"至多一句"')
        if not src_ok:
            issues.append('存在金句库之外的引文（临场抄句或改写）')
        print(f'[核验] 未过（{"；".join(issues)}），要求重写…', file=sys.stderr)
        msgs += [{'role': 'assistant', 'content': answer},
                 {'role': 'user', 'content': '自查未通过：' + '；'.join(issues)
                  + '。请重写整篇回答：逐字引文只能从【已核验金句库】中逐字选用'
                    f'（含自带译文与出处），{label}至多一句，其余改为第三人称转述。'}]
        answer = _post(GLM_URL, {'model': 'glm-4.6', 'messages': msgs, 'max_tokens': 8192,
                                'thinking': {'type': 'disabled'}},
                       {'Authorization': 'Bearer ' + key})['choices'][0]['message']['content']
    return answer


def fetch_context(api, code, question, mentors):
    """问思库检索层：外发仅问题文本；返回人设 prompt + 语料块 + 金句库"""
    r = _post(api.rstrip('/') + '/cli/context',
              {'code': code, 'question': question, 'mentors': mentors},
              {'Authorization': 'Bearer ' + code}, timeout=120)
    if isinstance(r, dict) and r.get('error'):
        sys.exit(f'✗ {r["error"]}')
    return r


def build_user_msg(question, ctx, file_name, file_text):
    fblock = (f'用户上传了待评审文件《{file_name}》，内容如下（可能截断）：\n'
              f'<<<文件开始>>>\n{file_text}\n<<<文件结束>>>\n\n') if file_text else ''
    if ctx['roundtable']:
        blocks, qb = [], []
        for m in ctx['mentors']:
            for i, h in enumerate(m['corpus']):
                blocks.append(f"【语料{len(blocks)+1}】导师：{m['name']}｜"
                              f"出处：{h['citation']}（{h.get('year') or ''}）\n{h['text']}")
            for q in m['quotes']:
                line = f"- 导师：{q.get('mentor') or m['name']}｜\"{q['text']}\"｜（出处：{q['citation']}）"
                if q.get('zh'):
                    line += f"｜译文：{q['zh']}"
                qb.append(line)
        return (fblock + '用户问题：' + question + '\n\n检索到的语料（按导师分区，编号全局递增）：\n'
                + '\n\n'.join(blocks)
                + '\n\n【已核验金句库】（唯一合法的逐字引文来源：每位导师的逐字引文只允许'
                  '从自己名下的条目中逐字选用；都不贴切就全篇转述）\n'
                + ('\n'.join(qb) or '（无）'))
    m = ctx['mentors'][0]
    corpus = '\n\n'.join(f"【语料{i+1}】出处：{h['citation']}（{h.get('year') or ''}）\n{h['text']}"
                         for i, h in enumerate(m['corpus']))
    qb = '\n'.join((f"- 导师：{q.get('mentor','')}｜\"{q['text']}\"｜译文：{q['zh']}｜（出处：{q['citation']}）" if q.get('zh')
                   else f"- \"{q['text']}\"（出处：{q['citation']}）") for q in m['quotes']) or '（无）'
    return (fblock + '用户问题：' + question + '\n\n检索到的语料：\n' + corpus
            + '\n\n【已核验金句库】（唯一合法的逐字引文来源：引文只允许逐字选自下列原句，'
              '括注与译文取自该条自带内容；都不贴切就全篇转述、不引用）\n' + qb)


def main():
    ap = argparse.ArgumentParser(description='问思库·本地文件评审（文件不出你的机器）')
    ap.add_argument('file', help='待评审文件（txt/md/csv/json/log/pdf，≤1.5MB）')
    ap.add_argument('--code', help='问思库邀请码（或环境变量 WENSIKU_CODE）')
    ap.add_argument('-m', '--mentors', default='munger', help='导师 key 逗号分隔，默认 munger；多个=圆桌')
    ap.add_argument('-f', '--focus', default='', help='评审侧重/附言（默认通用评审指令）')
    ap.add_argument('--api-url', default=os.environ.get('WENSIKU_API', DEFAULT_API))
    ap.add_argument('--raw', action='store_true', help='只解析文件不生成（零外发）')
    a = ap.parse_args()

    if not os.path.isfile(a.file):
        sys.exit(f'✗ 文件不存在：{a.file}')
    try:
        text = extract_file_text(os.path.basename(a.file), a.file)
    except ValueError as e:
        sys.exit(f'✗ {e}')
    print(f'📄 {a.file}（本机解析出 {len(text)} 字）', file=sys.stderr)
    if a.raw:
        print(text)
        return

    code = a.code or os.environ.get('WENSIKU_CODE', '')
    if not code:
        sys.exit('✗ 缺邀请码：--code WSK-XXXXXX 或环境变量 WENSIKU_CODE（wensiku 网页免费领取）')
    q = a.focus.strip() or DEFAULT_REVIEW_Q
    keys = [k.strip() for k in a.mentors.split(',') if k.strip()][:5]

    print(f'🔍 检索 {len(keys)} 位导师语料（外发仅评审指令，不含文件内容）…', file=sys.stderr)
    ctx = fetch_context(a.api_url, code, q, keys)
    names = '、'.join(m['name'] for m in ctx['mentors'])
    print(f'   获得语料 {sum(len(m["corpus"]) for m in ctx["mentors"])} 块、金句 '
          f'{sum(len(m["quotes"]) for m in ctx["mentors"])} 条｜{names}', file=sys.stderr)

    user_msg = build_user_msg(q, ctx, os.path.basename(a.file), text)
    system = ctx['roundtable_prompt'] if ctx['roundtable'] else ctx['mentors'][0]['system_prompt']
    quotes = [q2 for m in ctx['mentors'] for q2 in m['quotes']]
    print('⏳ 本机直连智谱生成 + 引文核验（约 30-120 秒）…', file=sys.stderr)
    ans = generate_verified(system, user_msg, quotes, '每位导师' if ctx['roundtable'] else '导师')

    print('\n' + ans)
    print('\n── 参考来源（可核验）──', file=sys.stderr)
    seen = set()
    for m in ctx['mentors']:
        for h in m['corpus'][:4]:
            tag = (m['name'] if ctx['roundtable'] else '') + h['citation']
            if tag not in seen:
                seen.add(tag)
                print(f"- {('【' + m['name'] + '】' if ctx['roundtable'] else '')}{h['citation']}"
                      f"{' · ' + str(h['year']) if h.get('year') else ''}"
                      f"{' · ' + h['url'] if h.get('url') else ''}", file=sys.stderr)


if __name__ == '__main__':
    main()
