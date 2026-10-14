#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
music_dl.py —— 命令行版（复用 mcore.py 同一套引擎，逻辑与 GUI 完全一致）

用法：
  python music_dl.py "永世のクレイドル 鈴華ゆう子"
  python music_dl.py --id 455502615 --format both
  python music_dl.py --batch 歌单.txt
  python music_dl.py --test-cookie "MUSIC_U=xxxxx"
"""

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mcore as M
from mcore import Config, Job, MusicError

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                      # noqa: BLE001
    pass


def log(msg, level="info"):
    print({"info": "  ", "ok": "[OK] ", "warn": "[!] ", "err": "[x] ",
           "step": "==> "}.get(level, "  ") + str(msg), flush=True)


def fmt(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def make_emitter():
    """把 Job 抛出的事件渲染成命令行输出"""
    state = {"files": [], "err": None, "done": False, "last": 0}

    def emit(evt):
        t = evt.get("type")
        if t == "stage":
            steps = evt.get("totalSteps") or 6
            log(f"[{evt.get('step', '?')}/{steps}] {evt['text']}", "step")
        elif t == "warn":
            log(evt["text"] + (f"　（{evt['hint']}）" if evt.get("hint") else ""),
                "warn")
        elif t == "meta":
            m = evt["meta"]
            log(f"{m['title']} - {m['artist']}｜{m['album']}｜"
                f"{m['duration']/1000:.0f}s" + ("｜VIP" if m.get("vip") else ""))
        elif t == "lyric":
            log(f"歌词：{evt['info']['lines']} 行时间轴，"
                f"含 {evt['info'].get('translated', 0)} 行翻译", "ok")
        elif t == "cover":
            log(f"封面：{evt['info']['desc']}（{evt['info']['kb']} KB）", "ok")
        elif t == "channel":
            ch = {"official": "官方 VIP 通道", "aggregator": "聚合接口",
                  "gequbao": "gequbao 兜底"}.get(evt["info"]["channel"], "?")
            log(f"音源通道：{ch}｜{evt['info']['level']}", "ok")
        elif t == "progress":
            now = time.time()
            if now - state["last"] < 1.0 and evt["done"] < (evt.get("total") or 0):
                return
            state["last"] = now
            pct = (evt["done"] * 100 // evt["total"]) if evt.get("total") else 0
            sp = f"{fmt(evt['speed'])}/s" if evt.get("speed") else "-"
            log(f"下载 {evt.get('ext', '').upper()} {pct:3d}%  "
                f"{fmt(evt['done'])}/{fmt(evt.get('total') or 0)}  {sp}")
        elif t == "file":
            state["files"].append(evt)
            log(f"完成 {Path(evt['path']).name}（{fmt(evt['size'])}，"
                f"{evt['level']}，封面 {evt['cover']}，"
                f"歌词 {evt['lyricLines']} 行）", "ok")
        elif t == "status":
            if evt["status"] == "done":
                state["done"] = True
            elif evt["status"] == "error":
                state["err"] = evt["error"]

    return emit, state


def run_one(song, cfg):
    emit, state = make_emitter()
    job = Job("cli", song, cfg, emit)
    th = threading.Thread(target=job.run, daemon=True)
    th.start()
    th.join()
    if state["err"]:
        e = state["err"]
        log(e.get("message", "失败"), "err")
        if e.get("hint"):
            log(f"建议：{e['hint']}", "warn")
        if e.get("detail"):
            log("细节：" + str(e["detail"])[:500], "warn")
        return False
    return bool(state["files"])


def resolve(query=None, song_id=None):
    if song_id:
        d = M.song_detail(song_id)
        return {"id": d["id"], "title": d["title"], "artist": d["artist"],
                "album": d["album"], "cover": d["cover"],
                "duration": d["duration"]}

    import re
    m = re.search(r"[?&#/]id=(\d+)", query or "")
    if m:
        return resolve(song_id=m.group(1))
    if query and query.strip().isdigit():
        return resolve(song_id=query.strip())

    rows = M.search_songs(query, limit=15)
    if not rows:
        raise MusicError(f"没搜到「{query}」",
                         hint="换个关键词；日文歌可以用中文译名或直接贴链接",
                         code="NOMATCH")
    best = M.pick_best(rows, query)
    if best.get("_score", 1) < 0.55:
        log(f"匹配度只有 {best['_score']*100:.0f}%，"
            f"选中的是：{best['title']} - {best['artist']}", "warn")
    return best


def main():
    ap = argparse.ArgumentParser(description="音乐下载器 命令行版")
    ap.add_argument("query", nargs="?", help="歌名 [歌手]，或网易云链接")
    ap.add_argument("--id", help="网易云歌曲 ID")
    ap.add_argument("--out", help="输出目录（默认读配置，配置没有则 D:\\音乐下载）")
    ap.add_argument("--format", choices=["flac", "mp3", "both"], help="输出格式")
    ap.add_argument("--batch", help="批量：每行一条的文本文件")
    ap.add_argument("--cookie", help="临时指定 MUSIC_U（不改配置）")
    ap.add_argument("--only-aggregator", action="store_true",
                    help="本次只用聚合接口")
    ap.add_argument("--test-cookie", metavar="COOKIE",
                    help="测试一组 Cookie 是否可用后退出")
    args = ap.parse_args()

    if args.test_cookie:
        r = M.probe_cookie(args.test_cookie)
        log(f"账号：{r.get('nickname') or '—'}　类型：{r.get('vipText') or '—'}")
        log(f"结论：{r.get('reason')}", "ok" if r.get("ok") else "err")
        if r.get("hint"):
            log(f"建议：{r['hint']}", "warn")
        return 0 if r.get("ok") else 1

    cfg = Config()
    if args.out:
        cfg.data["outputDir"] = args.out
    if args.format:
        cfg.data["format"] = args.format
    if args.only_aggregator:
        cfg.data["sourceOrder"] = "aggregator_only"
    if args.cookie:
        cfg.data["cookies"] = [{"id": "cli", "name": "命令行临时",
                                "value": args.cookie, "enabled": True, "note": ""}]
        cfg.data["activeCookieId"] = "cli"

    outdir = Path(cfg.data.get("outputDir") or M.DEFAULT_OUT)
    outdir.mkdir(parents=True, exist_ok=True)
    log(f"输出目录：{outdir}")
    log(f"格式：{cfg.data.get('format')}　"
        f"音源策略：{cfg.data.get('sourceOrder')}　"
        f"VIP Cookie：{'已配置' if cfg.active_cookie() else '未配置'}")
    print()

    ok = 0
    if args.batch:
        lines = [l.strip() for l in Path(args.batch).read_text(
            encoding="utf-8").splitlines()]
        lines = [l for l in lines if l and not l.startswith("#")]
        log(f"批量任务：{len(lines)} 首")
        for i, line in enumerate(lines, 1):
            print(f"\n[{i}/{len(lines)}] {line}")
            try:
                ok += bool(run_one(resolve(line), cfg))
            except MusicError as e:
                log(e.message, "err")
        log(f"批量完成 {ok}/{len(lines)}", "ok")
        return 0 if ok else 1

    if not args.query and not args.id:
        ap.print_help()
        return 2
    try:
        song = resolve(args.query, args.id)
    except MusicError as e:
        log(e.message, "err")
        if e.hint:
            log(f"建议：{e.hint}", "warn")
        return 1
    return 0 if run_one(song, cfg) else 1


if __name__ == "__main__":
    sys.exit(main())
