#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcore.py —— 音乐下载核心引擎

职责：搜歌、取元数据、取封面、取歌词、按「官方 VIP cookie → 聚合接口 → gequbao」
三级通道取音频直链、下载（带进度回调）、写入标签。

不含任何界面代码，CLI（music_dl.py）和 GUI（app.py）共用这一层。
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

# ============================================================ 常量

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

GD_API = "https://music-api.gdstudio.xyz/api.php"
NE_DETAIL = "https://music.163.com/api/song/detail"
NE_LYRIC = "https://music.163.com/api/song/lyric"
NE_PLAYER_URL = "https://music.163.com/api/song/enhance/player/url/v1"
NE_SEARCH = "https://music.163.com/api/search/get/web"
NE_ACCOUNT = "https://music.163.com/api/nuser/account/get"
ITUNES_API = "https://itunes.apple.com/search"

# 官方接口的 level 档位
LEVEL_OF = {"flac": ["hires", "lossless"], "mp3": ["exhigh"]}
LEVEL_CN = {"hires": "Hi-Res 无损", "lossless": "无损 FLAC",
            "exhigh": "320K MP3", "higher": "192K MP3",
            "standard": "128K MP3", "jymaster": "母带", "sky": "沉浸声"}

VIP_TYPE_CN = {0: "普通用户", 10: "音乐包", 11: "黑胶 VIP"}

def _base_dir():
    """打包成 exe 后，配置和默认输出目录跟着 exe 走，而不是临时解包目录"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _base_dir()
DEFAULT_OUT = Path(r"D:\音乐下载")


# ============================================================ 异常

class MusicError(Exception):
    """带「原因 + 建议」的异常，界面直接把 message/hint 展示给用户"""

    def __init__(self, message, hint="", code="", detail=""):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code
        self.detail = detail

    def to_dict(self):
        return {"message": self.message, "hint": self.hint,
                "code": self.code, "detail": self.detail}


# ============================================================ 小工具

def norm(s):
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    s = re.sub(r"[（(\[【].*?[)）\]】]", "", s)
    return re.sub(r"[\s\-_·・.,，。!！?？'\"“”‘’~～]+", "", s)


def sim(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def san(s):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(s or ""))
    return re.sub(r"\s+", " ", s).strip(" .") or "unknown"


def http_bytes(url, referer=None, cookie=None, data=None, retries=3,
               timeout=40, extra_headers=None):
    last = None
    for i in range(retries):
        try:
            headers = {"User-Agent": UA, "Accept": "*/*"}
            if referer:
                headers["Referer"] = referer
            if cookie:
                headers["Cookie"] = cookie
            if extra_headers:
                headers.update(extra_headers)
            if data is not None and not isinstance(data, bytes):
                data = urllib.parse.urlencode(data).encode()
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except Exception as e:                        # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        if i < retries - 1:
            time.sleep(0.8 * (i + 1))
    raise MusicError(f"网络请求失败：{last}", hint="检查网络或代理设置后重试",
                     code="NETWORK", detail=str(url))


def http_json(url, **kw):
    raw = http_bytes(url, **kw)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception as e:                            # noqa: BLE001
        raise MusicError("接口返回了无法解析的内容（可能被限流或接口已变更）",
                         hint="稍后重试；若持续出现，说明数据源需要更新",
                         code="BAD_JSON", detail=str(e)[:300])


def normalize_cookie(raw):
    """把用户粘贴的内容统一成 Cookie 头。支持三种写法：
       1) 纯 MUSIC_U 值
       2) MUSIC_U=xxx
       3) 完整 cookie 串（MUSIC_U=xxx; __csrf=yyy）
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if "MUSIC_U=" in raw:
        return raw
    if ";" in raw and "=" in raw:
        return raw
    return f"MUSIC_U={raw}"


# ============================================================ 配置

DEFAULT_CONFIG = {
    "cookies": [],            # [{id,name,value,enabled,note}]
    "activeCookieId": "",
    "sourceOrder": "official_first",   # official_first | aggregator_only
    "outputDir": str(DEFAULT_OUT),
    "format": "both",         # flac | mp3 | both
    "coverMax": 1200,
    "coverSource": "best",    # best | netease | itunes
    "writeLrc": True,
    "embedLyrics": True,
    "embedCover": True,
}


class Config:
    def __init__(self, path=None):
        self.path = Path(path) if path else APP_DIR / "config.json"
        self.data = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        try:
            if self.path.exists():
                disk = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(disk, dict):
                    self.data.update(disk)
        except Exception:                              # noqa: BLE001
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.path)

    # --- cookie 管理 ---
    def cookie_list(self):
        return self.data.get("cookies") or []

    def add_cookie(self, name, value, note=""):
        cid = f"ck{int(time.time()*1000)}"
        self.data.setdefault("cookies", []).append(
            {"id": cid, "name": name or "未命名账号",
             "value": value.strip(), "enabled": True, "note": note})
        if not self.data.get("activeCookieId"):
            self.data["activeCookieId"] = cid
        self.save()
        return cid

    def update_cookie(self, cid, **fields):
        for c in self.cookie_list():
            if c["id"] == cid:
                c.update({k: v for k, v in fields.items() if v is not None})
        self.save()

    def remove_cookie(self, cid):
        self.data["cookies"] = [c for c in self.cookie_list() if c["id"] != cid]
        if self.data.get("activeCookieId") == cid:
            left = [c["id"] for c in self.cookie_list() if c.get("enabled")]
            self.data["activeCookieId"] = left[0] if left else ""
        self.save()

    def active_cookie(self):
        want = self.data.get("activeCookieId")
        for c in self.cookie_list():
            if c["id"] == want and c.get("enabled") and c.get("value"):
                return normalize_cookie(c["value"])
        for c in self.cookie_list():                   # 依次回退到任一可用项
            if c.get("enabled") and c.get("value"):
                return normalize_cookie(c["value"])
        return ""

    def cookie_candidates(self):
        """返回 [(名称, cookie头)]，按启用顺序，active 优先"""
        out, act = [], self.data.get("activeCookieId")
        items = [c for c in self.cookie_list()
                 if c.get("enabled") and c.get("value")]
        items.sort(key=lambda c: 0 if c["id"] == act else 1)
        for c in items:
            out.append((c.get("name") or "未命名", normalize_cookie(c["value"])))
        return out


# ============================================================ 搜索

def search_songs(keyword, limit=20):
    """网易云官方搜索。返回统一结构的列表。"""
    try:
        raw = http_bytes(
            NE_SEARCH, referer="https://music.163.com/",
            data={"s": keyword, "type": 1, "offset": 0, "limit": limit,
                  "total": "true"},
            extra_headers={"Content-Type":
                           "application/x-www-form-urlencoded; charset=UTF-8"})
        data = json.loads(raw.decode("utf-8", "replace"))
        songs = ((data.get("result") or {}).get("songs")) or []
        out = []
        for s in songs:
            alb = s.get("album") or {}
            arts = s.get("artists") or []
            fee = s.get("fee", 0)
            out.append({
                "id": str(s.get("id")),
                "title": s.get("name") or "",
                "artist": "/".join(a.get("name", "") for a in arts),
                "artistIds": [a.get("id") for a in arts],
                "album": alb.get("name") or "",
                "cover": alb.get("picUrl") or "",
                "duration": s.get("duration") or 0,
                "fee": fee,
                "vip": fee in (1, 4),
                "source": "netease",
            })
        if out:
            enrich_covers(out)
            return out
    except MusicError:
        pass
    # 回退：聚合接口搜索
    try:
        q = urllib.parse.urlencode({"types": "search", "source": "netease",
                                    "name": keyword, "count": limit})
        data = http_json(f"{GD_API}?{q}", referer="https://music.gdstudio.xyz/")
        if isinstance(data, list):
            return [{"id": str(c.get("id")), "title": c.get("name") or "",
                     "artist": "/".join(c.get("artist") or []),
                     "artistIds": [], "album": c.get("album") or "",
                     "cover": "", "duration": 0, "fee": 0,
                     "vip": False, "source": "gdstudio"} for c in data]
    except Exception:                                  # noqa: BLE001
        pass
    return []


def enrich_covers(rows):
    """搜索结果里 album.picUrl 是 null，只有 picId。
    用一次批量 song/detail 把封面地址补齐（顺便校正歌名/歌手）。"""
    ids = [r["id"] for r in rows if not r.get("cover")]
    if not ids:
        return
    try:
        d = http_json(f"{NE_DETAIL}?{urllib.parse.urlencode({'ids': json.dumps([int(i) for i in ids])})}",
                      referer="https://music.163.com/", retries=1)
    except Exception:                                  # noqa: BLE001
        return
    by_id = {}
    for s in d.get("songs") or []:
        by_id[str(s.get("id"))] = s
    for r in rows:
        s = by_id.get(r["id"])
        if not s:
            continue
        alb = s.get("album") or {}
        if alb.get("picUrl"):
            r["cover"] = alb["picUrl"]
        if not r.get("duration"):
            r["duration"] = s.get("duration") or 0
        if not r.get("album"):
            r["album"] = alb.get("name") or ""
        if not r.get("artist"):
            r["artist"] = "/".join(a.get("name", "")
                                   for a in (s.get("artists") or []))


def pick_best(cands, keyword):
    """按关键词挑最匹配的一条"""
    if not cands:
        return None
    parts = [p for p in re.split(r"[\s\-—]+", keyword.strip()) if p]
    want_t = parts[0] if parts else keyword
    want_a = " ".join(parts[1:]) if len(parts) > 1 else ""
    best, best_sc = None, -1
    for c in cands:
        sc = sim(want_t, c["title"]) * 0.7 + sim(want_t, c["artist"]) * 0.1
        sc += sim(want_a, c["artist"]) * 0.2 if want_a else 0.2
        if sc > best_sc:
            best, best_sc = c, sc
    if best:
        best["_score"] = round(best_sc, 3)
    return best


# ============================================================ 元数据

def song_detail(song_id):
    d = http_json(f"{NE_DETAIL}?{urllib.parse.urlencode({'ids': json.dumps([int(song_id)])})}",
                  referer="https://music.163.com/")
    songs = d.get("songs") or []
    if not songs:
        raise MusicError(f"网易云查不到 ID {song_id} 这首歌",
                         hint="确认歌曲 ID 是否正确，或换用歌名搜索",
                         code="NOT_FOUND")
    s = songs[0]
    alb = s.get("album") or {}
    return {
        "id": str(s.get("id")),
        "title": s.get("name") or "",
        "artist": "/".join(a.get("name", "") for a in (s.get("artists") or [])),
        "album": alb.get("name") or "",
        "cover": alb.get("picUrl") or "",
        "coverId": alb.get("picId"),
        "duration": s.get("duration") or 0,
        "track": s.get("no"),
        "disc": s.get("disc"),
        "albumSize": alb.get("size"),
        "publishTime": s.get("publishTime"),
        "fee": s.get("fee", 0),
        "alias": s.get("alias") or [],
        "raw": s,
    }


def itunes_lookup(title, artist, country="JP"):
    q = urllib.parse.urlencode({"term": f"{title} {artist}", "entity": "song",
                                "limit": 10, "country": country})
    try:
        data = http_json(f"{ITUNES_API}?{q}", referer="https://music.apple.com/")
    except Exception:                                  # noqa: BLE001
        return None
    best, best_sc = None, 0.0
    for r in data.get("results", []):
        sc = sim(title, r.get("trackName", "")) * 0.65 + \
             sim(artist, r.get("artistName", "")) * 0.35
        if sc > best_sc:
            best, best_sc = r, sc
    if best and best_sc >= 0.72:
        best["_score"] = round(best_sc, 3)
        return best
    return None


def img_size(blob):
    try:
        if blob[:2] == b"\xff\xd8":
            i = 2
            while i < len(blob) - 9:
                if blob[i] != 0xFF:
                    i += 1
                    continue
                m = blob[i + 1]
                if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                         0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    return (int.from_bytes(blob[i + 7:i + 9], "big"),
                            int.from_bytes(blob[i + 5:i + 7], "big"))
                i += 2 + int.from_bytes(blob[i + 2:i + 4], "big")
        elif blob[:8] == b"\x89PNG\r\n\x1a\n":
            return (int.from_bytes(blob[16:20], "big"),
                    int.from_bytes(blob[20:24], "big"))
    except Exception:                                  # noqa: BLE001
        pass
    return 0, 0


def fetch_cover(meta, itunes_info, prefer="best", max_px=1200):
    """返回 (bytes, mime, 描述)。自动挑分辨率最高的。"""
    cands = []
    if prefer != "itunes" and meta.get("cover"):
        try:
            blob = http_bytes(meta["cover"], referer="https://music.163.com/",
                              retries=2)
            w, h = img_size(blob)
            if w:
                cands.append((w * h, blob, "image/jpeg", f"网易云 {w}×{h}"))
        except Exception:                              # noqa: BLE001
            pass
    if prefer != "netease" and itunes_info:
        art = itunes_info.get("artworkUrl100") or ""
        if art:
            base = re.sub(r"/\d+x\d+(bb|sr)?\.(jpg|png)$", "/{size}.jpg", art)
            want = int(max_px) if max_px and max_px > 0 else 3000
            for size in (f"{want}x{want}bb", "1200x1200bb", "600x600bb"):
                try:
                    blob = http_bytes(base.replace("{size}", size),
                                      referer="https://music.apple.com/",
                                      retries=1)
                    w, h = img_size(blob)
                    if w:
                        cands.append((w * h, blob, "image/jpeg",
                                      f"iTunes {w}×{h}"))
                        break
                except Exception:                      # noqa: BLE001
                    continue
    if not cands:
        return None, None, "未取到"
    cands.sort(key=lambda x: -x[0])
    _, blob, mime, desc = cands[0]
    return blob, mime, desc


# ============================================================ 歌词

TS_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
META_RE = re.compile(r"^\[(ar|ti|al|by|au|offset|re|ve|length):(.*)\]", re.I)


def _to_ms(m, s, frac):
    ms = int(m) * 60000 + int(s) * 1000
    if frac:
        ms += int(frac.ljust(3, "0")[:3]) if len(frac) <= 3 else int(frac)
    return ms


def parse_lrc(text):
    metas, entries = [], []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if META_RE.match(line):
            metas.append(line)
            continue
        stamps = list(TS_RE.finditer(line))
        if not stamps:
            continue
        content = line[stamps[-1].end():].strip()
        for st in stamps:
            entries.append((_to_ms(*st.groups()), content))
    entries.sort(key=lambda x: x[0])
    return metas, entries


def fmt_ts(ms):
    return f"[{ms // 60000:02d}:{ms % 60000 / 1000:05.2f}]"


def fetch_lyric(song_id):
    """返回 (双语LRC, 纯原文LRC, [(ms,text)])"""
    ja = zh = ""
    try:
        q = urllib.parse.urlencode({"id": song_id, "lv": 1, "kv": 1, "tv": -1})
        d = http_json(f"{NE_LYRIC}?{q}", referer="https://music.163.com/")
        ja = (d.get("lrc") or {}).get("lyric", "") or ""
        zh = (d.get("tlyric") or {}).get("lyric", "") or ""
    except Exception:                                  # noqa: BLE001
        pass
    if not ja:
        try:
            q = urllib.parse.urlencode({"types": "lyric", "source": "netease",
                                        "id": song_id})
            d = http_json(f"{GD_API}?{q}", referer="https://music.gdstudio.xyz/")
            ja = d.get("lyric") or ""
            zh = d.get("tlyric") or ""
        except Exception:                              # noqa: BLE001
            pass

    ja_meta, ja_entries = parse_lrc(ja)
    zh_meta, zh_entries = parse_lrc(zh)
    if not ja_entries:
        return "", "", []

    zh_map = {}
    for ms, t in zh_entries:
        if t:
            zh_map.setdefault(ms, t)
    merged = []
    for ms, t in ja_entries:
        merged.append(f"{fmt_ts(ms)}{t}")
        z = zh_map.get(ms)
        if z is None:
            for k in zh_map:
                if abs(k - ms) <= 60:
                    z = zh_map[k]
                    break
        if z:
            merged.append(f"{fmt_ts(ms)}{z}")
    bilingual = "\n".join(ja_meta + ([zh_meta[0]] if zh_meta else []) + merged)
    original = "\n".join(ja_meta + [f"{fmt_ts(m)}{t}" for m, t in ja_entries])
    return bilingual, original, [(m, t) for m, t in ja_entries if t]


# ============================================================ 音频直链

def official_url(song_id, level, cookie, timeout=25):
    """网易云官方播放地址接口。返回 data[0] 字典。"""
    q = urllib.parse.urlencode({
        "ids": json.dumps([int(song_id)]),
        "level": level,
        "encodeType": "flac" if level in ("lossless", "hires", "jymaster")
                      else "mp3",
    })
    d = http_json(f"{NE_PLAYER_URL}?{q}", referer="https://music.163.com/",
                  cookie=cookie, retries=2, timeout=timeout)
    arr = d.get("data") or []
    return arr[0] if arr else {}


def probe_cookie(cookie, song_id=455502615):
    """测试一组 cookie：返回账号信息 + 真实取无损直链的结果"""
    cookie = normalize_cookie(cookie)
    if not cookie:
        return {"ok": False, "reason": "Cookie 为空",
                "hint": "请粘贴 MUSIC_U 的值"}
    out = {"ok": False, "nickname": "", "vipType": 0, "vipText": "",
           "probe": False, "level": "", "reason": "", "hint": ""}
    try:
        d = http_json(NE_ACCOUNT, referer="https://music.163.com/",
                      cookie=cookie, retries=1, timeout=20)
        prof = d.get("profile") or {}
        acct = d.get("account") or {}
        if not prof and not acct:
            out.update(reason="Cookie 无效或已过期",
                       hint="重新登录 music.163.com 后复制新的 MUSIC_U")
            return out
        out["nickname"] = prof.get("nickname") or acct.get("userName") or "（未知昵称）"
        vt = prof.get("vipType", acct.get("vipType", 0)) or 0
        out["vipType"] = vt
        out["vipText"] = VIP_TYPE_CN.get(vt, f"VIP 类型 {vt}")
    except MusicError as e:
        out.update(reason="账号查询失败", hint=e.hint or "检查网络",
                   detail=e.message)
        return out

    # 真实探针：拿一首已知需要 VIP 的歌，看能不能取到无损直链
    try:
        r = official_url(song_id, "lossless", cookie)
        lvl = r.get("level") or ""
        got = bool(r.get("url"))
        out["probe"] = got
        out["level"] = LEVEL_CN.get(lvl, lvl)
        out["probeBr"] = r.get("br") or 0
        if got:
            out["ok"] = True
            out["reason"] = f"可用，能取到 {LEVEL_CN.get(lvl, lvl)}"
            if lvl and lvl != "lossless":
                out["hint"] = ("该账号的黑胶权益可能只覆盖部分曲目，"
                               "或这首歌没有无损版本")
        else:
            code = r.get("code")
            if code == -110:
                out["reason"] = "Cookie 有效，但该账号没有 VIP 播放权益"
                out["hint"] = ("到网易云开通黑胶 VIP 即可；"
                               "不开也能用聚合接口（默认自动降级）")
            else:
                out["reason"] = f"取直链失败（接口码 {code}）"
                out["hint"] = "稍后重试，或检查账号状态"
    except MusicError as e:
        out.update(reason="探针请求失败", hint=e.hint, detail=e.message)
    return out


def aggregator_url(song_id, br):
    q = urllib.parse.urlencode({"types": "url", "source": "netease",
                                "id": song_id, "br": br})
    d = http_json(f"{GD_API}?{q}", referer="https://music.gdstudio.xyz/",
                  retries=2)
    return d.get("url"), d.get("br"), d.get("size")


def gequbao_url(keyword):
    """gequbao 兜底。实测只给酷我 128kbps。"""
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "zh-CN,zh;q=0.9")]

    def get(url, data=None, referer=None):
        hdr = {"Referer": referer} if referer else {}
        if data is not None:
            hdr["X-Requested-With"] = "XMLHttpRequest"
            hdr["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        with op.open(urllib.request.Request(url, data=data, headers=hdr),
                     timeout=25) as r:
            return r.read().decode("utf-8", "replace")

    try:
        html_ = get("https://www.gequbao.com/s/" + urllib.parse.quote(keyword))
    except Exception as e:                             # noqa: BLE001
        raise MusicError("gequbao 兜底源搜索失败", hint="稍后重试",
                         code="GEQUBAO", detail=str(e))
    best_id, best_sc = None, 0.0
    for m in re.finditer(r'/music/(\d+)"[^>]*title="([^"]*)"', html_):
        sc = sim(keyword.split()[0], m.group(2).split(" - ")[0])
        if sc > best_sc:
            best_id, best_sc = m.group(1), sc
    if not best_id:
        raise MusicError("gequbao 里也没搜到这首歌", code="GEQUBAO_NOMATCH")
    song_url = f"https://www.gequbao.com/music/{best_id}"
    page = get(song_url)
    mm = re.search(r"window\.appData\s*=\s*JSON\.parse\('(.*?)'\)", page, re.S)
    if not mm:
        raise MusicError("gequbao 页面结构已变更，兜底源暂时不可用",
                         hint="等更新，或改用聚合接口", code="GEQUBAO_STRUCT")
    app = json.loads(json.loads('"' + mm.group(1).replace('"', '\\"') + '"'))
    body = get("https://www.gequbao.com/member/common-play-url",
               data=urllib.parse.urlencode(
                   {"id": app.get("play_id"), "purpose": "play"}).encode(),
               referer=song_url)
    j = json.loads(body)
    if j.get("code") == 1 and (j.get("data") or {}).get("url"):
        u = j["data"]["url"]
        return u, ("酷我 320K" if "bitrate$320" in u else "酷我 128K"), song_url
    raise MusicError(f"gequbao 拒绝提供直链（{j.get('msg') or '需要验证码'}）",
                     hint="gequbao 有验证码和每日额度限制，此路不通是正常的",
                     code="GEQUBAO_DENIED")


def resolve_audio(song_id, want, cfg, cookie="", log=None):
    """三级通道取直链。

    want: 'flac' | 'mp3'
    返回 dict(url, ext, level_cn, br, size, channel)
    """
    def say(msg):
        if log:
            log(msg)

    order = cfg.data.get("sourceOrder", "official_first")
    errors = []

    # ---- 1) 官方 VIP cookie ----
    if order == "official_first" and cookie:
        for lvl in LEVEL_OF.get(want, []):
            try:
                r = official_url(song_id, lvl, cookie)
                if r.get("url"):
                    return {"url": r["url"],
                            "ext": want,
                            "level_cn": LEVEL_CN.get(r.get("level") or lvl, lvl),
                            "br": r.get("br") or 0,
                            "size": r.get("size") or 0,
                            "channel": "official"}
                code = r.get("code")
                if code == -110:
                    errors.append("官方通道：账号无此曲播放权益")
                elif code == -200:
                    errors.append("官方通道：该曲无此音质版本")
                else:
                    errors.append(f"官方通道：接口码 {code}")
            except MusicError as e:
                errors.append(f"官方通道 {lvl}：{e.message}")
            say(f"官方通道 {LEVEL_CN.get(lvl, lvl)} 未取到，继续降级…")
    elif not cookie and order == "official_first":
        errors.append("官方通道：未配置 VIP Cookie，已跳过")

    # ---- 2) 聚合接口 ----
    br = 999 if want == "flac" else 320
    try:
        u, real_br, size = aggregator_url(song_id, br)
        if u:
            return {"url": u, "ext": want,
                    "level_cn": ("无损 FLAC" if want == "flac" else "320K MP3"),
                    "br": (real_br or 0) * 1000 if real_br and real_br < 10000
                          else real_br,
                    "size": size or 0, "channel": "aggregator"}
        errors.append("聚合接口：该曲没有这个音质的直链")
    except MusicError as e:
        errors.append(f"聚合接口：{e.message}")
    say("聚合接口未取到，尝试 gequbao 兜底…")

    # ---- 3) gequbao 兜底（只有 mp3）----
    try:
        u, note, _ = gequbao_url(f"{song_id}")
        return {"url": u, "ext": "mp3", "level_cn": note, "br": 0,
                "size": 0, "channel": "gequbao"}
    except MusicError as e:
        errors.append(f"gequbao：{e.message}")

    raise MusicError(
        "所有音源通道都没能取到直链",
        hint="歌曲可能需要单曲购买，或三个数据源同时故障。可到设置里检查 Cookie。",
        code="NO_SOURCE", detail="\n".join(errors))


# ============================================================ 下载

def download_file(url, dest: Path, referer=None, cookie=None,
                  on_progress=None, cancel=None):
    """下载并回报进度。on_progress(done, total, speed_bps)"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    headers = {"User-Agent": UA, "Accept": "*/*",
               "Referer": referer or "https://music.163.com/"}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=90)
    except urllib.error.HTTPError as e:
        raise MusicError(
            f"下载被拒绝（HTTP {e.code}）",
            hint="直链通常是限时的，可能已过期；重试会重新获取",
            code="HTTP_" + str(e.code))
    except Exception as e:                             # noqa: BLE001
        raise MusicError(f"连接下载服务器失败：{e}",
                         hint="检查网络后重试", code="CONNECT")

    total = 0
    try:
        total = int(resp.headers.get("Content-Length") or 0)
    except Exception:                                  # noqa: BLE001
        total = 0

    done, t0, last_t, last_b = 0, time.time(), time.time(), 0
    with open(tmp, "wb") as f:
        while True:
            if cancel and cancel.is_set():
                resp.close()
                tmp.unlink(missing_ok=True)
                raise MusicError("已取消", code="CANCELLED")
            chunk = resp.read(131072)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            now = time.time()
            if on_progress and (now - last_t > 0.25 or (total and done >= total)):
                speed = (done - last_b) / max(now - last_t, 1e-6)
                on_progress(done, total, speed)
                last_t, last_b = now, done
    if total and done < total:
        tmp.unlink(missing_ok=True)
        raise MusicError(
            f"下载不完整（收到 {done/1048576:.1f} MB，应为 {total/1048576:.1f} MB）",
            hint="网络中断，重新下载即可（支持重试）", code="TRUNCATED")
    if on_progress:
        on_progress(done, total or done, 0)
    tmp.replace(dest)
    return done


# ============================================================ 打标签

def tag_mp3(path, meta, cover, cover_mime, lrc_text, sylt):
    from mutagen.id3 import (ID3, TIT2, TPE1, TALB, TPE2, TRCK, TPOS, TDRC,
                             TCON, COMM, APIC, USLT, SYLT, TXXX,
                             ID3NoHeaderError)
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()
    for f in ("TIT2", "TPE1", "TALB", "TPE2", "TRCK", "TPOS", "TYER", "TDAT",
              "TDRC", "TCON", "COMM", "APIC", "USLT", "SYLT"):
        tags.delall(f)
    tags.add(TIT2(encoding=3, text=meta["title"]))
    tags.add(TPE1(encoding=3, text=meta["artist"]))
    if meta["album"]:
        tags.add(TALB(encoding=3, text=meta["album"]))
    if meta["artist"]:
        tags.add(TPE2(encoding=3, text=meta["artist"]))
    if meta["track"]:
        tags.add(TRCK(encoding=3, text=meta["track"]))
    if meta["disc"]:
        tags.add(TPOS(encoding=3, text=meta["disc"]))
    if meta["date"]:
        tags.add(TDRC(encoding=3, text=meta["date"]))
    if meta["genre"]:
        tags.add(TCON(encoding=3, text=meta["genre"]))
    if meta["comment"]:
        tags.add(COMM(encoding=3, lang="XXX", desc="", text=meta["comment"]))
    tags.add(TXXX(encoding=3, desc="SOURCE", text=meta.get("source", "")))
    if cover:
        tags.add(APIC(encoding=3, mime=cover_mime or "image/jpeg", type=3,
                      desc="Cover", data=cover))
    if lrc_text:
        tags.add(USLT(encoding=3, lang="jpn", desc="", text=lrc_text))
    if sylt:
        tags.add(SYLT(encoding=3, lang="jpn", format=2, type=1, desc="",
                      text=[(t, ms) for ms, t in sylt]))
    tags.save(str(path), v2_version=3)


def tag_flac(path, meta, cover, cover_mime, lrc_text, lrc_orig):
    from mutagen.flac import FLAC, Picture
    f = FLAC(str(path))
    f.delete()
    f["TITLE"] = meta["title"]
    f["ARTIST"] = meta["artist"]
    if meta["album"]:
        f["ALBUM"] = meta["album"]
    if meta["artist"]:
        f["ALBUMARTIST"] = meta["artist"]
    if meta["track"]:
        n, _, tot = meta["track"].partition("/")
        f["TRACKNUMBER"] = n
        if tot:
            f["TRACKTOTAL"] = tot
    if meta["disc"]:
        n, _, tot = meta["disc"].partition("/")
        f["DISCNUMBER"] = n
        if tot:
            f["DISCTOTAL"] = tot
    if meta["date"]:
        f["DATE"] = meta["date"]
    if meta["genre"]:
        f["GENRE"] = meta["genre"]
    if meta["comment"]:
        f["COMMENT"] = meta["comment"]
    if lrc_text:
        f["LYRICS"] = lrc_text
        f["UNSYNCEDLYRICS"] = lrc_text
    if lrc_orig:
        f["LYRICS_ORIGINAL"] = lrc_orig
    if cover:
        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.data = 3, cover_mime or "image/jpeg", "Cover", cover
        pic.width, pic.height, pic.depth = *img_size(cover), 24
        f.add_picture(pic)
    f.save()


def build_meta(detail, itunes_info, source_note):
    meta = {"title": detail.get("title", ""),
            "artist": detail.get("artist", ""),
            "album": detail.get("album", ""),
            "date": "", "genre": "", "track": "", "disc": "",
            "source": source_note}
    pt = detail.get("publishTime")
    if pt:
        meta["date"] = time.strftime("%Y-%m-%d", time.localtime(pt / 1000))
    tr, sz = detail.get("track"), detail.get("albumSize")
    if tr:
        meta["track"] = f"{tr}/{sz}" if sz else str(tr)
    dc = detail.get("disc")
    if dc:
        meta["disc"] = f"{dc}/1"
    if itunes_info:
        if itunes_info.get("trackNumber"):
            tn, tc = itunes_info["trackNumber"], itunes_info.get("trackCount")
            meta["track"] = f"{tn}/{tc}" if tc else str(tn)
        if itunes_info.get("discNumber"):
            dn, dc2 = itunes_info["discNumber"], itunes_info.get("discCount")
            meta["disc"] = f"{dn}/{dc2}" if dc2 else str(dn)
        if itunes_info.get("primaryGenreName"):
            meta["genre"] = itunes_info["primaryGenreName"]
        if itunes_info.get("releaseDate"):
            meta["date"] = itunes_info["releaseDate"][:10]
        if itunes_info.get("collectionName"):
            meta["album"] = itunes_info["collectionName"]
    return meta


# ============================================================ 总编排

class Job:
    """一次下载任务，通过 emit 回调把进度抛给界面"""

    def __init__(self, job_id, song, cfg, emit):
        self.id = job_id
        self.song = song               # {id,title,artist,...} 可能是搜索结果
        self.cfg = cfg
        self.emit = emit
        self.cancel = threading.Event()
        self.files = []
        self.status = "pending"

    def _ev(self, **kw):
        kw["jobId"] = self.id
        self.emit(kw)

    def stage(self, text, step=None, total_steps=None):
        self._ev(type="stage", text=text, step=step, totalSteps=total_steps)

    def warn(self, text, hint=""):
        self._ev(type="warn", text=text, hint=hint)

    def run(self):
        cfg = self.cfg
        self.status = "running"
        self._ev(type="status", status="running")
        try:
            sid = str(self.song["id"])

            # 1) 元数据
            self.stage("获取歌曲信息…", 1, 6)
            detail = song_detail(sid)
            self._ev(type="meta", meta={
                "title": detail["title"], "artist": detail["artist"],
                "album": detail["album"], "cover": detail["cover"],
                "duration": detail["duration"], "id": sid,
                "vip": detail.get("fee") in (1, 4),
            })

            # 2) iTunes 补全
            self.stage("匹配 iTunes 高清封面与流派…", 2, 6)
            it = itunes_lookup(detail["title"], detail["artist"])
            if it:
                self._ev(type="itunes", info={
                    "genre": it.get("primaryGenreName", ""),
                    "track": f"{it.get('trackNumber')}/{it.get('trackCount')}",
                    "score": it.get("_score"),
                })
            else:
                self.warn("iTunes 没有匹配到这张专辑，封面和流派将只用网易云的数据")

            # 3) 歌词
            lrc = lrc_orig = ""
            sylt = []
            if cfg.data.get("embedLyrics", True) or cfg.data.get("writeLrc", True):
                self.stage("抓取歌词时间轴…", 3, 6)
                try:
                    lrc, lrc_orig, sylt = fetch_lyric(sid)
                    if sylt:
                        tr = lrc.count("\n") - lrc_orig.count("\n")
                        self._ev(type="lyric", info={
                            "lines": len(sylt),
                            "translated": max(tr, 0)})
                    else:
                        self.warn("这首歌没有时间轴歌词（纯音乐或冷门曲目）")
                except MusicError as e:
                    self.warn(f"歌词获取失败：{e.message}")

            # 4) 封面
            cover = cover_mime = None
            cover_desc = "已跳过"
            if cfg.data.get("embedCover", True):
                self.stage("挑选最清晰的封面…", 4, 6)
                try:
                    cover, cover_mime, cover_desc = fetch_cover(
                        detail, it, cfg.data.get("coverSource", "best"),
                        cfg.data.get("coverMax", 1200))
                    self._ev(type="cover", info={"desc": cover_desc,
                                                 "kb": round(len(cover) / 1024) if cover else 0})
                except Exception as e:                 # noqa: BLE001
                    self.warn(f"封面获取失败：{e}")

            meta = build_meta(detail, it, "music.163.com")
            meta["comment"] = (f"网易云音乐 ID {sid} | "
                               f"https://music.163.com/#/song?id={sid}")

            # 5) 音频
            fmt = cfg.data.get("format", "both")
            wants = ["flac", "mp3"] if fmt == "both" else [fmt]
            stem = san(f"{detail['artist']} - {detail['title']}")
            outdir = Path(cfg.data.get("outputDir") or DEFAULT_OUT).expanduser()
            outdir.mkdir(parents=True, exist_ok=True)
            ctx = 0
            for want in wants:
                ctx += 1
                self.stage(f"解析 {want.upper()} 音源…", 5, 6 + len(wants))
                res = resolve_audio(sid, want, cfg,
                                    cookie=cfg.active_cookie(),
                                    log=lambda m: self.warn(m))
                ext = res["ext"]
                dest = outdir / f"{stem}.{ext}"
                self._ev(type="channel", info={
                    "ext": ext, "channel": res["channel"],
                    "level": res["level_cn"]})
                self.stage(f"下载 {res['level_cn']}…", 5, 6 + len(wants))

                last = {"t": 0}

                def prog(done, total, speed):
                    now = time.time()
                    if now - last["t"] < 0.25 and (not total or done < total):
                        return
                    last["t"] = now
                    eta = ((total - done) / speed) if (speed > 0 and total) else 0
                    self._ev(type="progress", ext=ext, done=done, total=total,
                             speed=speed, eta=eta)

                n = download_file(res["url"], dest,
                                  referer="https://music.163.com/",
                                  cookie=cfg.active_cookie() if res["channel"] == "official" else None,
                                  on_progress=prog, cancel=self.cancel)

                # 6) 打标签
                self.stage(f"写入标签与封面（{ext.upper()}）…", 6, 6 + len(wants))
                try:
                    if ext == "mp3":
                        tag_mp3(dest, meta, cover, cover_mime, lrc, sylt)
                    else:
                        tag_flac(dest, meta, cover, cover_mime, lrc, lrc_orig)
                except Exception as e:                 # noqa: BLE001
                    self.warn(f"标签写入失败：{e}（音频文件本身没问题）")

                if lrc and cfg.data.get("writeLrc", True):
                    dest.with_suffix(".lrc").write_text(lrc, encoding="utf-8")

                self.files.append(str(dest))
                self._ev(type="file", ext=ext, path=str(dest), size=n,
                         level=res["level_cn"], channel=res["channel"],
                         cover=cover_desc, lyricLines=len(sylt),
                         tagged=bool(cover or lrc))

            self.status = "done"
            self._ev(type="status", status="done", files=self.files)
        except MusicError as e:
            self.status = "error"
            self._ev(type="status", status="error", error=e.to_dict())
        except Exception as e:                         # noqa: BLE001
            self.status = "error"
            self._ev(type="status", status="error", error={
                "message": f"意外错误：{type(e).__name__}: {e}",
                "hint": "把这条信息反馈给开发者", "code": "UNEXPECTED",
                "detail": ""})
        return self.status


# ============================================================ 媒体信息探测

def probe_audio(path):
    """读回文件标签，用于界面展示「实际写进去了什么」"""
    p = Path(path)
    if not p.exists():
        return None
    out = {"path": str(p), "size": p.stat().st_size, "ext": p.suffix[1:]}
    try:
        if p.suffix.lower() == ".mp3":
            from mutagen.id3 import ID3
            t = ID3(str(p))
            out["title"] = str(t.get("TIT2", ""))
            out["artist"] = str(t.get("TPE1", ""))
            out["album"] = str(t.get("TALB", ""))
            out["date"] = str(t.get("TDRC", ""))
            out["genre"] = str(t.get("TCON", ""))
            out["track"] = str(t.get("TRCK", ""))
            pics = t.getall("APIC")
            out["cover"] = f"{len(pics[0].data)//1024} KB" if pics else ""
            out["coverSize"] = ""
            if pics:
                w, h = img_size(pics[0].data)
                out["coverSize"] = f"{w}×{h}"
            uslt = t.getall("USLT")
            out["lyricLines"] = len(uslt[0].text.splitlines()) if uslt else 0
            sylt = t.getall("SYLT")
            out["syncLines"] = len(sylt[0].text) if sylt else 0
        else:
            from mutagen.flac import FLAC
            f = FLAC(str(p))
            out["title"] = (f.get("TITLE") or [""])[0]
            out["artist"] = (f.get("ARTIST") or [""])[0]
            out["album"] = (f.get("ALBUM") or [""])[0]
            out["date"] = (f.get("DATE") or [""])[0]
            out["genre"] = (f.get("GENRE") or [""])[0]
            out["track"] = (f.get("TRACKNUMBER") or [""])[0]
            out["cover"] = f"{len(f.pictures[0].data)//1024} KB" if f.pictures else ""
            out["coverSize"] = (f"{f.pictures[0].width}×{f.pictures[0].height}"
                                if f.pictures else "")
            lyr = (f.get("LYRICS") or [""])[0]
            out["lyricLines"] = len(lyr.splitlines()) if lyr else 0
            out["syncLines"] = 0
    except Exception as e:                             # noqa: BLE001
        out["error"] = str(e)
    return out


def audio_stream_info(path):
    """用 ffprobe 读真实码率/采样率（没有 ffprobe 就跳过）"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate",
             "-show_entries", "format=bit_rate,duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace")
        d = json.loads(r.stdout or "{}")
        st = (d.get("streams") or [{}])[0]
        fm = d.get("format") or {}
        return {"codec": st.get("codec_name", ""),
                "sampleRate": st.get("sample_rate", ""),
                "channels": st.get("channels", ""),
                "bitrate": int(fm.get("bit_rate") or st.get("bit_rate") or 0),
                "duration": float(fm.get("duration") or 0)}
    except Exception:                                  # noqa: BLE001
        return {}
