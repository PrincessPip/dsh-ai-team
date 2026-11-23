"""Snapshot-only verification. No sandbox or attribution; exclusive tree required."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time

LIMITATIONS = ["Not a hard sandbox", "write then restore is invisible", "outside-root writes are invisible", "external writers are not locked; exclusive tree required; diff is not attribution", "forced parent termination may prevent after/evidence; stale lock requires manual investigation", "snapshots are sequential, not atomic"]
# 链接子树内部的内容变化**检测不到**（见 limitations 的动态声明）：覆盖不到的地方必须写在声明里，
# 而不是让它表现为「整棵树都核过了」。
LINK_LIMITATION = ('reparse point subtrees are NOT hashed: %d path(s) skipped; '
                   'the link entries themselves are recorded and their own add/remove/retarget is diffed; '
                   'see coverage.reparse_points')
EXCLUDED = [".git (root only)"]
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
REPARSE_TAG_NAMES = {0xA0000003: 'junction', 0xA000000C: 'symlink'}

def is_reparse(s):
    return bool(stat.S_ISLNK(s.st_mode)
                or getattr(s, 'st_file_attributes', 0) & FILE_ATTRIBUTE_REPARSE_POINT)

def link_kind(s):
    tag = getattr(s, 'st_reparse_tag', 0) or 0
    if stat.S_ISLNK(s.st_mode):
        return 'symlink'
    if tag in REPARSE_TAG_NAMES:
        return REPARSE_TAG_NAMES[tag]
    return 'reparse-0x%08X' % tag if tag else 'reparse'

def read_link_target(p):
    """readlink 对部分形态（云盘占位符、WCI）会失败；失败记 None，不许让快照作废。"""
    try:
        return os.readlink(p)
    except OSError:
        return None

def no_links(path):
    """硬边界：**目标路径本身**在链接下就拒（root / out-dir / 报告落点用）。

    ⚠️ 这一条不随 ④b 放松 —— 放松的是「树内遍历遇到链接」，不是「从链接底下开始跑」。
    """
    path = Path(os.path.abspath(path))
    for part in [path, *path.parents]:
        if part.exists() or part.is_symlink():
            s = part.lstat()
            if is_reparse(s):
                raise OSError('link/reparse point: ' + str(part))
    return path

def snapshot(root):
    """整树哈希。遇到链接/重解析点：登记进 `excluded_links`、不递归、**不让整次作废**。

    取舍（④b · B1）：原先遇 link 即 raise 会让 `complete=False` → exit 5、验收官一次都不启动
    （实测白等 1049 s / 0 调用），于是每轮送审都得人工把链接搬出树。现在改成「记录 + 声明」：
    被跳过的路径带 rel/kind/target 落盘，`coverage.reparse_points` 与 `limitations` 里写明，
    链接自身的增删改进 diff。**代价写清楚**：链接子树内部被改是看不见的。
    """
    entries, errors, excluded_links = {}, [], {}
    try:
        no_links(root)
        def walk(folder):
            for p in sorted(folder.iterdir()):
                rel = p.relative_to(root).as_posix()
                if rel == '.git':
                    continue
                s = p.lstat()
                if is_reparse(s):
                    excluded_links[rel] = {'kind': link_kind(s), 'target': read_link_target(p)}
                    continue
                if stat.S_ISDIR(s.st_mode):
                    entries[rel] = {'type': 'directory'}
                    walk(p)
                elif stat.S_ISREG(s.st_mode):
                    h = hashlib.sha256()
                    with p.open('rb') as f:
                        for block in iter(lambda: f.read(1024*1024), b''):
                            h.update(block)
                        end = os.fstat(f.fileno())
                    now = p.lstat()
                    if (s.st_ino, s.st_size, s.st_mtime_ns) != (end.st_ino, end.st_size, end.st_mtime_ns) or (end.st_ino, end.st_size, end.st_mtime_ns) != (now.st_ino, now.st_size, now.st_mtime_ns):
                        raise OSError('changed during scan: ' + rel)
                    entries[rel] = {'type': 'file', 'sha256': h.hexdigest()}
                else:
                    raise OSError('unsupported type: ' + rel)
        walk(root)
    except Exception as e:
        errors.append(repr(e))
    return {'entries': entries, 'errors': errors, 'excluded_links': excluded_links,
            'complete': not errors}

def diff(before, after):
    a, b = before['entries'], after['entries']
    la, lb = before.get('excluded_links') or {}, after.get('excluded_links') or {}
    # 只在**真有变化**时给非空 dict：verify() 用真假值判「树被动过」，
    # 常驻一个空骨架会让每次送审都误判 exit 4。
    links = {}
    if la != lb:
        links = {'added': sorted(lb.keys() - la.keys()),
                 'deleted': sorted(la.keys() - lb.keys()),
                 'changed': sorted(k for k in la.keys() & lb.keys() if la[k] != lb[k])}
    return {'added': sorted(b.keys()-a.keys()), 'deleted': sorted(a.keys()-b.keys()),
            'modified': sorted(k for k in a.keys() & b.keys() if a[k] != b[k]),
            'excluded_links': links}

def merge_links(before, after):
    """两次快照里出现过的链接取并集；同 rel 两侧不同则把 before 一起留下（可追溯改了什么）。"""
    la, lb = before.get('excluded_links') or {}, after.get('excluded_links') or {}
    merged = {}
    for k in sorted(la.keys() | lb.keys()):
        v = dict(lb.get(k) or la.get(k) or {})
        if k in la and k in lb and la[k] != lb[k]:
            v['before'] = la[k]
        merged[k] = v
    return merged

class RootLock:
    def __init__(self, root):
        key = hashlib.sha256(os.path.normcase(str(root.resolve())).encode()).hexdigest()
        self.path = Path(tempfile.gettempdir()) / ('review-codex-' + key + '.lock')
        if self.path.is_relative_to(root):
            raise OSError('lock must be outside root')
        self.fd = None
    def acquire(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(self.fd, json.dumps({'pid': os.getpid(), 'time': time.time()}).encode())
    def release(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
            self.path.unlink()

class WindowsJob:
    """Suspend before assignment so descendants cannot escape the launch race."""
    def __init__(self):
        from ctypes import wintypes as w
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        self.k.CreateJobObjectW.restype = w.HANDLE
        self.k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        for name, args in [('AssignProcessToJobObject',[w.HANDLE,w.HANDLE]), ('TerminateJobObject',[w.HANDLE,w.UINT]), ('CloseHandle',[w.HANDLE]), ('QueryInformationJobObject',[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.c_void_p])]:
            f=getattr(self.k,name); f.argtypes=args; f.restype=w.BOOL
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
    def assign_resume(self, p):
        if not self.k.AssignProcessToJobObject(self.handle, int(p._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        n=ctypes.WinDLL('ntdll'); n.NtResumeProcess.argtypes=[ctypes.c_void_p]; n.NtResumeProcess.restype=ctypes.c_long
        if n.NtResumeProcess(int(p._handle)) != 0: raise OSError('NtResumeProcess failed')
    def stop(self):
        if not self.k.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION: 4 LARGE_INTEGER + 4 DWORD.
        class Accounting(ctypes.Structure):
            _fields_=[('times',ctypes.c_longlong*4),('faults',ctypes.c_ulong),('total',ctypes.c_ulong),('active',ctypes.c_ulong),('terminated',ctypes.c_ulong)]
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            a=Accounting()
            if not self.k.QueryInformationJobObject(self.handle,1,ctypes.byref(a),ctypes.sizeof(a),None):
                raise ctypes.WinError(ctypes.get_last_error())
            if a.active == 0: return
            time.sleep(.05)
        raise OSError('job still active after 5s')
    def close(self):
        self.k.CloseHandle(self.handle)

def run_model(argv, prompt, root, timeout):
    result={'status':'spawn_error','returncode':None,'stdout':'','stderr':'','cleanup_verified':True}
    job, p = None, None
    try:
        if os.name != 'nt': raise OSError('Windows Job Object required on this implementation')
        job=WindowsJob()
        p=subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, encoding='utf-8', errors='replace', cwd=str(root), creationflags=0x4)
        job.assign_resume(p)
        try:
            result['stdout'], result['stderr']=p.communicate(prompt, timeout=timeout)
            result['returncode']=p.returncode
            result['status']='completed'
        except subprocess.TimeoutExpired as e:
            result['status']='timeout'
            result['stdout']=(e.stdout or b'').decode('utf-8','replace') if isinstance(e.stdout,bytes) else (e.stdout or '')
            result['stderr']=(e.stderr or b'').decode('utf-8','replace') if isinstance(e.stderr,bytes) else (e.stderr or '')
    except Exception as e:
        result['error']=repr(e)
    finally:
        try:
            if job: job.stop()
            if p:
                # Also kills suspended process when job assignment failed.
                if p.poll() is None: p.kill()
                out, err=p.communicate(timeout=5)
                result['stdout']=out or result['stdout']; result['stderr']=err or result['stderr']
                result['returncode']=p.returncode
        except Exception as e:
            result['cleanup_verified']=False; result['cleanup_error']=repr(e)
        finally:
            if job: job.close()
    return result

def verify(root, argv, prompt, timeout):
    before=snapshot(root)
    model={'status':'not_started','returncode':None,'stdout':'','stderr':'','cleanup_verified':True}
    if before['complete']:
        model=run_model(argv,prompt,root,timeout)
    after=snapshot(root)
    changes=diff(before,after)
    links=merge_links(before,after)
    limitations=list(LIMITATIONS)
    if links:
        limitations.append(LINK_LIMITATION % len(links))
    touched=any(changes[k] for k in ('added','deleted','modified')) or bool(changes['excluded_links'])
    if not before['complete'] or not after['complete'] or not model['cleanup_verified']:
        code=5
    elif touched:
        code=4
    elif model['status'] != 'completed' or model['returncode'] != 0:
        code=3
    else:
        code=0
    return {'readonly_enforcement':'snapshot_only','root':str(root),
            'coverage':{'algorithm':'SHA256','paths_and_types':True,
                        'excluded':list(EXCLUDED)+['reparse points (per-path; recorded, not descended)'],
                        'includes':'all other paths including tasks, runs, out-dir and report targets',
                        'reparse_points':{'count':len(links),'entries':links}},
            'limitations':limitations,'before':before,'after':after,'diff':changes,
            'model':model,'exit_code':code}
