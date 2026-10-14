#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py —— 音乐下载器 桌面版

两种运行方式：
  python app.py            弹出原生窗口（pywebview + WebView2）
  python app.py --serve    起本地网页服务，用浏览器打开（开发/备用）

界面代码在 ui/ 目录，业务逻辑全在 mcore.py。
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mcore as M
from mcore import Config, Job, MusicError

APP_DIR = Path(__file__).resolve().parent
VERSION = "2.0"


def _res_dir():
    """界面资源目录：打包后从 PyInstaller 解包目录取"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


UI_DIR = _res_dir() / "ui"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                      # noqa: BLE001
    pass


# ============================================================ 事件总线

class Bus:
    def __init__(self):
        self.mode = "webview"
        self.window = None
        self.subs = []
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)

    def push(self, evt):
        if self.mode == "webview" and self.window is not None:
            try:
                payload = json.dumps(evt, ensure_ascii=False)
                self.window.evaluate_js(f"window.__push && window.__push({payload})")
                return
            except Exception:                          # noqa: BLE001
                pass
        with self.lock:
            subs = list(self.subs)
        for q in subs:
            try:
                q.put_nowait(evt)
            except Exception:                          # noqa: BLE001
                pass


BUS = Bus()


# ============================================================ API

class Api:
    def __init__(self):
        self.cfg = Config()
        self.jobs = {}
        self.lock = threading.Lock()

    # ---------- 基础 ----------
    def hello(self):
        return {"ok": True, "version": VERSION,
                "outputDir": self.cfg.data.get("outputDir"),
                "hasCookie": bool(self.cfg.active_cookie()),
                "cookieCount": len([c for c in self.cfg.cookie_list()
                                    if c.get("enabled") and c.get("value")])}

    def get_settings(self):
        d = dict(self.cfg.data)
        # 出于安全，cookie 值只回传掩码，前端编辑时再单独取
        d["cookies"] = [{"id": c["id"], "name": c.get("name", ""),
                         "enabled": c.get("enabled", True),
                         "note": c.get("note", ""),
                         "masked": self._mask(c.get("value", "")),
                         "hasValue": bool(c.get("value"))}
                        for c in self.cfg.cookie_list()]
        return d

    @staticmethod
    def _mask(v):
        v = (v or "")
        if len(v) <= 12:
            return "•" * len(v)
        return v[:6] + "•" * 12 + v[-4:]

    def save_settings(self, patch):
        try:
            for k in ("outputDir", "format", "coverMax", "coverSource",
                      "sourceOrder", "writeLrc", "embedLyrics", "embedCover",
                      "activeCookieId"):
                if k in patch:
                    self.cfg.data[k] = patch[k]
            self.cfg.save()
            return {"ok": True}
        except Exception as e:                         # noqa: BLE001
            return {"ok": False, "error": {"message": str(e)}}

    # ---------- cookie ----------
    def add_cookie(self, name, value, note=""):
        try:
            value = (value or "").strip()
            if not value:
                return {"ok": False, "error": {
                    "message": "Cookie 内容不能为空",
                    "hint": "粘贴 MUSIC_U 的值，或完整 cookie 串"}}
            cid = self.cfg.add_cookie(name, value, note)
            return {"ok": True, "id": cid}
        except Exception as e:                         # noqa: BLE001
            return {"ok": False, "error": {"message": f"保存失败：{e}"}}

    def update_cookie(self, cid, patch):
        try:
            if patch.get("value"):
                patch["value"] = patch["value"].strip()
            self.cfg.update_cookie(cid, **{k: v for k, v in patch.items()
                                           if k in ("name", "value", "enabled",
                                                    "note")})
            return {"ok": True}
        except Exception as e:                         # noqa: BLE001
            return {"ok": False, "error": {"message": str(e)}}

    def remove_cookie(self, cid):
        self.cfg.remove_cookie(cid)
        return {"ok": True}

    def set_active_cookie(self, cid):
        self.cfg.data["activeCookieId"] = cid
        self.cfg.save()
        return {"ok": True}

    def get_cookie_value(self, cid):
        for c in self.cfg.cookie_list():
            if c["id"] == cid:
                return {"ok": True, "value": c.get("value", "")}
        return {"ok": False, "error": {"message": "找不到这个账号"}}

    def test_cookie(self, value=None, cid=None):
        """测试 cookie：查账号 + 真实探针取无损直链"""
        try:
            if value is None and cid:
                for c in self.cfg.cookie_list():
                    if c["id"] == cid:
                        value = c.get("value", "")
                        break
            if not value:
                return {"ok": False, "error": {
                    "message": "没有可测试的 Cookie",
                    "hint": "先填写 MUSIC_U 的值"}}
            r = M.probe_cookie(value)
            return {"ok": True, "result": r}
        except Exception as e:                         # noqa: BLE001
            return {"ok": False, "error": {
                "message": f"测试失败：{e}", "hint": "检查网络后重试"}}

    # ---------- 搜索 ----------
    def search(self, query):
        try:
            query = (query or "").strip()
            if not query:
                return {"ok": False, "error": {"message": "请输入歌名或粘贴链接"}}

            # 直接给链接或纯数字 ID
            m = __import__("re").search(r"[?&#/]id=(\d+)", query)
            if not m:
                m = __import__("re").match(r"^\s*(\d{4,})\s*$", query)
            if m:
                sid = m.group(1)
                try:
                    d = M.song_detail(sid)
                    return {"ok": True, "results": [{
                        "id": d["id"], "title": d["title"],
                        "artist": d["artist"], "album": d["album"],
                        "cover": d["cover"], "duration": d["duration"],
                        "vip": d.get("fee") in (1, 4), "_score": 1.0,
                        "direct": True}]}
                except MusicError as e:
                    return {"ok": False, "error": e.to_dict()}

            rows = M.search_songs(query, limit=20)
            if not rows:
                return {"ok": False, "error": {
                    "message": f"没搜到「{query}」",
                    "hint": "试试只输入歌名；日文歌可以用中文译名或罗马音，"
                            "也可以直接粘贴网易云歌曲链接"}}
            best = M.pick_best(rows, query)
            for i, r in enumerate(rows):
                r["_best"] = (r is best)
            return {"ok": True, "results": rows}
        except MusicError as e:
            return {"ok": False, "error": e.to_dict()}
        except Exception as e:                         # noqa: BLE001
            return {"ok": False, "error": {
                "message": f"搜索出错：{type(e).__name__}: {e}",
                "hint": "稍后重试", "detail": traceback.format_exc()[-600:]}}

    # ---------- 下载 ----------
    def start_download(self, song):
        try:
            song = dict(song or {})
            if not song.get("id"):
                return {"ok": False, "error": {"message": "这条结果缺少歌曲 ID"}}
            jid = f"j{int(time.time()*1000)}"
            job = Job(jid, song, self.cfg, BUS.push)
            with self.lock:
                self.jobs[jid] = job
            threading.Thread(target=job.run, daemon=True).start()
            return {"ok": True, "jobId": jid}
        except Exception as e:                         # noqa: BLE001
            return {"ok": False, "error": {"message": f"启动失败：{e}"}}

    def cancel_job(self, jid):
        with self.lock:
            job = self.jobs.get(jid)
        if job:
            job.cancel.set()
            return {"ok": True}
        return {"ok": False, "error": {"message": "任务不存在或已结束"}}

    def probe_file(self, path):
        try:
            info = M.probe_audio(path)
            if not info:
                return {"ok": False, "error": {"message": "文件不存在或已被移动"}}
            info.update(M.audio_stream_info(path))
            return {"ok": True, "info": info}
        except Exception as e:                         # noqa: BLE001
            return {"ok": False, "error": {"message": str(e)}}

    # ---------- 系统 ----------
    def open_folder(self, path):
        try:
            p = Path(path)
            if p.is_file():
                subprocess.Popen(["explorer", "/select,", str(p)])
            elif p.is_dir():
                os.startfile(str(p))                   # noqa: S606
            else:
                return {"ok": False, "error": {"message": "路径不存在"}}
            return {"ok": True}
        except Exception as e:                         # noqa: BLE001
            return {"ok": False, "error": {"message": str(e)}}

    def open_file(self, path):
        try:
            os.startfile(str(path))                    # noqa: S606
            return {"ok": True}
        except Exception as e:                         # noqa: BLE001
            return {"ok": False, "error": {"message": str(e)}}

    def pick_folder(self, current=""):
        try:
            import webview
            r = webview.windows[0].create_file_dialog(
                webview.FOLDER_DIALOG, directory=current or "")
            if r:
                return {"ok": True, "path": r[0] if isinstance(r, (list, tuple)) else r}
            return {"ok": False, "cancelled": True}
        except Exception as e:                         # noqa: BLE001
            return {"ok": False, "error": {"message": f"无法打开文件夹选择器：{e}"}}


API = Api()


# ============================================================ 网页模式服务

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        ctype = {".html": "text/html; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8",
                 ".svg": "image/svg+xml", ".png": "image/png",
                 ".ico": "image/x-icon"}.get(path.suffix.lower(),
                                             "application/octet-stream")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html_with_marker(self, path: Path):
        """网页模式：注入环境标记，前端据此区分「浏览器 + 本地服务」和
        「pywebview 原生窗口」（两者都是 http 协议，无法用 protocol 区分）"""
        html = path.read_text(encoding="utf-8")
        html = html.replace(
            "</head>", "<script>window.__MODE_SERVE__=1;</script></head>", 1)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = BUS.subscribe()
            try:
                while True:
                    try:
                        evt = q.get(timeout=15)
                        data = json.dumps(evt, ensure_ascii=False)
                        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except Exception:                          # noqa: BLE001
                pass
            finally:
                BUS.unsubscribe(q)
            return
        if u.path in ("/", "/index.html"):
            return self._html_with_marker(UI_DIR / "index.html")
        rel = u.path.lstrip("/")
        target = (UI_DIR / rel).resolve()
        if not str(target).startswith(str(UI_DIR.resolve())):
            self.send_error(403)
            return
        self._file(target)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if not u.path.startswith("/api/"):
            self.send_error(404)
            return
        name = u.path[5:]
        fn = getattr(API, name, None)
        if not callable(fn) or name.startswith("_"):
            return self._json({"ok": False,
                               "error": {"message": f"未知接口 {name}"}}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            args = json.loads(self.rfile.read(n) or b"[]") if n else []
            if not isinstance(args, list):
                args = [args]
            self._json(fn(*args))
        except Exception as e:                         # noqa: BLE001
            self._json({"ok": False, "error": {
                "message": f"{type(e).__name__}: {e}",
                "detail": traceback.format_exc()[-600:]}}, 500)


def run_serve(port, open_browser=True):
    BUS.mode = "server"
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"音乐下载器 网页模式已启动：{url}")
    print("按 Ctrl+C 退出")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:                              # noqa: BLE001
            pass
    srv.serve_forever()


# ============================================================ 原生窗口

def run_window():
    import webview
    BUS.mode = "webview"
    window = webview.create_window(
        "音乐下载器", str(UI_DIR / "index.html"),
        js_api=API, width=1080, height=760, min_size=(880, 600),
        background_color="#FCFCFD", text_select=True)
    BUS.window = window
    webview.start()
    return 0


def run_selftest():
    """无头自检：真开一次原生窗口，验证 JS↔Python 双向桥、事件推送、
    以及真实搜索能否在界面里渲染出结果。用于打包后回归测试。"""
    import webview
    BUS.mode = "webview"
    window = webview.create_window(
        "音乐下载器 · 自检", str(UI_DIR / "index.html"), js_api=API,
        width=1080, height=760, background_color="#FCFCFD")
    BUS.window = window
    out = {}

    def probe():
        time.sleep(2.5)
        try:
            out["load"] = window.evaluate_js(
                "document.querySelector('.brand-name')?.textContent")
            out["href"] = window.evaluate_js("location.href")
            out["isServe"] = window.evaluate_js("!!window.__MODE_SERVE__")
            out["chip"] = window.evaluate_js(
                "document.querySelector('#sourceText')?.textContent")
            # Python → JS：桥接调用能否返回
            out["bridge"] = window.evaluate_js(
                "window.pywebview.api.hello().then(r => { window.__t = r; "
                "return 'ok' })")
            time.sleep(1.5)
            out["hello"] = window.evaluate_js("JSON.stringify(window.__t || null)")
            # JS → Python 事件推送：跑一次真实搜索
            window.evaluate_js(
                "document.querySelector('#q').value='永世のクレイドル 鈴華ゆう子';"
                "doSearch()")
            for _ in range(30):
                time.sleep(1)
                n = window.evaluate_js("document.querySelectorAll('.result').length")
                if n:
                    break
            out["results"] = n
            out["first"] = window.evaluate_js(
                "document.querySelector('.result .result-title .t')?.textContent")
            out["coverLoaded"] = window.evaluate_js(
                "(function(){const i=document.querySelector('.result img.cover');"
                "return i ? (i.complete && i.naturalWidth>0) : false})()")
            out["straySvg"] = window.evaluate_js(
                "document.querySelectorAll('.result > path, .result > circle').length")
            out["jsErrors"] = window.evaluate_js("JSON.stringify(window.__errs||[])")
        except Exception as e:                         # noqa: BLE001
            out["error"] = f"{type(e).__name__}: {e}"
        finally:
            # 核心项：窗口起来了、桥接通了、模式判定对、没有 JS 报错
            core = (out.get("load") == "音乐下载器" and out.get("isServe") is False
                    and bool(out.get("hello")) and not out.get("straySvg")
                    and out.get("jsErrors") in ("[]", None)
                    and not out.get("error"))
            # 联网项：真实搜索能否渲染（网络抖动不算失败）
            online = bool(out.get("results")) and bool(out.get("coverLoaded"))
            ok = bool(core and online)
            report = {"result": "PASS" if ok else ("PARTIAL" if core else "FAIL"),
                      "core": bool(core), "online": bool(online),
                      "detail": out, "version": VERSION,
                      "frozen": bool(getattr(sys, "frozen", False))}
            line = "SELFTEST " + json.dumps(report, ensure_ascii=False)
            try:
                print(line, flush=True)
            except Exception:                          # noqa: BLE001
                pass
            try:                                       # windowed exe 没有 stdout
                (M.APP_DIR / "selftest.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
            except Exception:                          # noqa: BLE001
                pass
            try:
                window.destroy()
            except Exception:                          # noqa: BLE001
                pass

    threading.Thread(target=probe, daemon=True).start()
    webview.start()
    return 0


# ============================================================ 入口

def main():
    ap = argparse.ArgumentParser(description="音乐下载器 桌面版")
    ap.add_argument("--serve", nargs="?", const=8765, type=int,
                    help="以本地网页方式运行，可指定端口")
    ap.add_argument("--no-open", action="store_true",
                    help="网页模式启动时不自动打开浏览器")
    ap.add_argument("--selftest", action="store_true",
                    help="开一次窗口做端到端自检后退出（打包回归用）")
    args = ap.parse_args()
    if args.selftest:
        return run_selftest()
    if args.serve:
        run_serve(args.serve, open_browser=not args.no_open)
    else:
        return run_window()


if __name__ == "__main__":
    sys.exit(main() or 0)
