"""浏览器桥：把设备浏览器里已经渲染好的课表页面送回本机解析。

思路：
  1. 本机运行 `jwxt browser`，在 127.0.0.1 上开一个只允许本机访问的小服务；
  2. 用户在浏览器里正常登录教务系统并打开课表页；
  3. 在 DevTools Console 粘贴一行加载器，页面会把 DOM/Table 结构 POST 回本机；
  4. 本机复用现有解析器导出 JSON/CSV/iCal。

这样不需要把学校密码交给工具，也不需要提前知道系统接口地址；
重点是拿到“浏览器渲染后的真实 DOM”。
"""

from __future__ import annotations

import html as html_module
import json
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from .config import AppConfig, load_config
from .display import format_schedule
from .models import Schedule
from .tables import ScheduleParseError, parse_html_schedule
from .visual import parse_visual_schedule
from .wizard import suggest_start_date, suggest_term_name
from .workflow import export_schedule

MAX_BODY_BYTES = 50 * 1024 * 1024  # 单次最多 50MB


class BrowserBridgeError(RuntimeError):
    pass


# ---------------- 解析浏览器快照 ----------------
def _iter_html_candidates(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """从浏览器 payload 中找出所有可能包含课表的 HTML 片段。"""
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(name: str, html: Any) -> None:
        if not isinstance(html, str) or len(html) < 20:
            return
        key = html[:200] + str(len(html))
        if key in seen:
            return
        seen.add(key)
        candidates.append((name, html))

    # 浏览器直接提取的每个 table
    for idx, table in enumerate(payload.get("tables") or []):
        if isinstance(table, dict):
            add(f"table[{idx}]", table.get("html"))
        else:
            add(f"table[{idx}]", table)
    for idx, table in enumerate(payload.get("tableHtml") or []):
        add(f"tableHtml[{idx}]", table)

    # 主页面 / iframe
    for idx, frame in enumerate(payload.get("frames") or []):
        if isinstance(frame, dict):
            for tidx, table in enumerate(frame.get("tables") or []):
                if isinstance(table, dict):
                    add(f"frame[{idx}].table[{tidx}]", table.get("html"))
            add(f"frame[{idx}].html", frame.get("html"))
    add("html", payload.get("html"))

    # 优先 table 片段：它们最小、最可能是课表
    return candidates


def parse_browser_payload(payload: dict[str, Any], config: AppConfig) -> Schedule:
    """尝试用现有 HTML 解析器解析浏览器送回的页面/表格。"""
    candidates = _iter_html_candidates(payload)

    best: Schedule | None = None
    errors: list[str] = []
    if not candidates and not payload.get("elements"):
        raise BrowserBridgeError(
            "浏览器没有发送有效的页面 HTML/文本坐标。可能页面完全由 canvas 绘制，或课表在跨域 iframe 中。"
        )
    for name, html in candidates:
        try:
            schedule = parse_html_schedule(
                html,
                term=config.term.name,
                source=f"browser:{name}",
            )
        except ScheduleParseError as exc:
            errors.append(f"{name}: {exc}")
            continue
        if not schedule.courses:
            continue
        if best is None or len(schedule.courses) > len(best.courses):
            best = schedule
            best.source = f"browser:{name}"
    # HTML/表格解析失败时，尝试用文本坐标恢复（适合 div/CSS 网格布局）
    if best is None and payload.get("elements"):
        try:
            visual_schedule = parse_visual_schedule(
                payload["elements"],
                term=config.term.name,
                source="browser:visual",
            )
        except ScheduleParseError as exc:
            errors.append(f"visual: {exc}")
        else:
            if visual_schedule.courses:
                best = visual_schedule

    if best is None:
        detail = "；".join(errors[:4]) if errors else "没有找到可解析的表格"
        raise BrowserBridgeError(f"已收到页面，但暂时无法自动解析：{detail}")

    # 浏览器快照里如果能拿到 URL/标题，补进 source 信息
    url = str(payload.get("url") or "")
    if url:
        host = urlparse(url).hostname or ""
        best.source = f"browser:{host}" if host else best.source
    if not best.term:
        best.term = config.term.name
    return best


def browser_default_config(outdir: Path) -> AppConfig:
    """浏览器桥用的配置：优先读取当前目录 jwxt.toml，否则用建议的学期信息。"""
    cwd_config = Path("jwxt.toml")
    if cwd_config.exists():
        try:
            config = load_config(cwd_config)
        except Exception:
            config = AppConfig()
    else:
        config = AppConfig()
    if not config.term.name:
        config.term.name = suggest_term_name()
    if config.term.start_date is None:
        # 只有完全没有配置时才用建议日期；避免用户明明选择跳过 iCal 又被自动填上
        if not cwd_config.exists():
            config.term.start_date = suggest_start_date()
    if not config.http.session_file:
        config.http.session_file = ""
    return config


def save_snapshot(payload: dict[str, Any], outdir: Path) -> Path:
    snapshot_dir = outdir / "jwxt-snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    host = urlparse(str(payload.get("url") or "")).hostname or "unknown"
    safe_host = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in host)
    path = snapshot_dir / f"{safe_host}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def import_from_browser(
    payload: dict[str, Any],
    *,
    outdir: Path | None = None,
    config: AppConfig | None = None,
    save_raw: bool = True,
) -> dict[str, Any]:
    """处理一次浏览器导入，返回适合 JSON 响应的结果。"""
    outdir = Path(outdir or ".").expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    config = config or browser_default_config(outdir)
    snapshot = save_snapshot(payload, outdir) if save_raw else None

    try:
        schedule = parse_browser_payload(payload, config)
    except BrowserBridgeError as exc:
        return {
            "ok": False,
            "message": str(exc),
            "snapshot": str(snapshot) if snapshot else "",
            "diagnostics": {
                "url": payload.get("url", ""),
                "title": payload.get("title", ""),
                "tables": len(payload.get("tables") or []),
                "frames": len(payload.get("frames") or []),
                "elements": len(payload.get("elements") or []),
            },
        }

    result = export_schedule(
        schedule,
        config,
        json_path=str(outdir / "schedule.json"),
        ics_path=str(outdir / "schedule.ics") if config.term.start_date else "",
        csv_path=str(outdir / "schedule.csv"),
    )
    return {
        "ok": True,
        "summary": schedule.summary(),
        "term": schedule.term,
        "count": len(schedule.courses),
        "files": result.saved,
        "errors": result.errors,
        "snapshot": str(snapshot) if snapshot else "",
        "display": format_schedule(schedule),
    }


# ---------------- 浏览器端加载器 ----------------
# 这段脚本会由 /snippet.js 返回；用户在教务系统页面的 Console 里执行一行加载器即可。
SNIPPET_JS = r"""
(async function () {
  const endpoint = "__ENDPOINT__";
  const MAX_HTML = 2500000;

  function visible(el) {
    try {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0;
    } catch (e) { return false; }
  }

  function collectTables(doc) {
    const result = [];
    for (const table of doc.querySelectorAll("table")) {
      if (visible(table)) result.push({ html: table.outerHTML });
      if (result.length >= 30) break;
    }
    return result;
  }

  // 文本节点 + 位置：用于后续非 table 布局的课表解析
  function collectElements(doc) {
    const out = [];
    if (!doc.body) return out;
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const text = (node.nodeValue || "").replace(/\s+/g, " ").trim();
        const parent = node.parentElement;
        if (!text || text.length > 120 || !parent || !visible(parent)) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    let node, count = 0;
    while ((node = walker.nextNode()) && count < 3000) {
      try {
        const range = doc.createRange();
        range.selectNodeContents(node);
        const r = range.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        out.push({
          text: (node.nodeValue || "").replace(/\s+/g, " ").trim(),
          x: Math.round(r.left + window.scrollX),
          y: Math.round(r.top + window.scrollY),
          w: Math.round(r.width),
          h: Math.round(r.height)
        });
        count++;
      } catch (e) { /* ignore */ }
    }
    return out;
  }

  function collectFrame(win, depth) {
    if (depth > 3) return [];
    let doc;
    try { doc = win.document; } catch (e) { return []; }
    const frame = {
      url: String(win.location.href || ""),
      title: doc.title || "",
      html: (doc.documentElement ? doc.documentElement.outerHTML : "").slice(0, MAX_HTML),
      tables: collectTables(doc)
    };
    const frames = [frame];
    for (const iframe of doc.querySelectorAll("iframe")) {
      try { frames.push(...collectFrame(iframe.contentWindow, depth + 1)); } catch (e) { /* 跨域 iframe */ }
    }
    return frames;
  }

  function send(payload) {
    const customPost = window.__JWXT_BRIDGE_POST__;
    if (typeof customPost === "function") {
      customPost(payload, showResult);
      return;
    }
    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(r => r.json()).then(showResult).catch(err => {
      alert("jwxt 浏览器桥连接失败：\n" + err + "\n\n请确认本机已运行 `jwxt browser`，" +
            "如果控制台提示跨域/混合内容错误，请改用 Tampermonkey 用户脚本方式。");
    });
  }

  function showResult(data) {
    if (data && data.ok) {
      alert("课表导入成功！\n" + (data.files || []).join("\n") +
            "\n\n共 " + data.count + " 条安排。");
    } else {
      alert("课表导入失败：\n" + ((data && data.message) || "未知错误") +
            ((data && data.snapshot) ? ("\n\n页面快照已保存：\n" + data.snapshot) : ""));
    }
  }

  const frames = collectFrame(window, 0);
  const payload = {
    url: location.href,
    title: document.title,
    userAgent: navigator.userAgent,
    frames: frames,
    tables: framesFlatTables(frames),
    elements: collectElements(document)
  };
  send(payload);

  function framesFlatTables(frameList) {
    const result = [];
    for (const frame of frameList) { for (const table of frame.tables || []) result.push(table); }
    return result;
  }
})();
""".lstrip()


def _snippet_js(port: int) -> str:
    return SNIPPET_JS.replace("__ENDPOINT__", f"http://127.0.0.1:{port}/api/import")


def _bookmarklet(port: int) -> str:
    """生成一个可拖到书签栏的 javascript: 链接。"""
    js = (
        "(function(){"
        f"fetch('http://127.0.0.1:{port}/snippet.js')"
        ".then(function(r){return r.text();})"
        ".then(function(t){eval(t);})"
        ".catch(function(e){alert('无法连接 jwxt browser，请先运行 jwxt browser\\n'+e);});"
        "})()"
    )
    return "javascript:" + quote(js, safe="()*!~'")


BRIDGE_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>jwxt 浏览器桥</title>
<style>
 body { font-family: -apple-system, "Microsoft YaHei", sans-serif; max-width: 760px; margin: 40px auto; padding: 0 20px; color: #222; }
 code, pre { background: #f5f5f5; border-radius: 6px; }
 pre { padding: 12px; overflow-x: auto; }
 button { padding: 8px 14px; border-radius: 6px; border: 1px solid #ccc; background: #fff; cursor: pointer; }
 .ok { color: #0a7a2f; }
 .tip { background: #fff8e5; border: 1px solid #f0d58a; padding: 10px 14px; border-radius: 6px; }
 li { margin: 8px 0; }
</style>
</head>
<body>
<h1>jwxt 浏览器桥</h1>
<p>本服务只监听 <code>127.0.0.1</code>，用于接收你浏览器里已经登录并渲染好的课表页面。</p>
<div class="tip">流程：<b>先正常在浏览器登录教务系统并打开课表页面</b>，再按下面步骤操作。工具不会接触你的账号密码。</div>
<h2>步骤</h2>
<ol>
  <li>保持这个标签页打开，服务状态：<span class="ok">运行中</span></li>
  <li>在浏览器另一个标签页里登录学校教务系统，打开能看到完整课表的页面</li>
  <li>按 <code>F12</code> 打开开发者工具，切到 <code>Console</code>，粘贴下面这一行并回车：
    <pre id="loader">eval(await (await fetch('http://127.0.0.1:__PORT__/snippet.js')).text())</pre>
    <button onclick="copyLoader()">复制这一行</button>
  </li>
  <li>或者：把 <a href="__BOOKMARKLET__" draggable="true" title="拖到书签栏">导入课表</a>
      拖到书签栏，以后在课表页点一下这个书签即可（若跨域受限请用用户脚本）。</li>
  <li>看到“课表导入成功”提示即可，文件会输出到运行命令的目录</li>
</ol>
<h2>如果控制台提示跨域/混合内容错误</h2>
<p>使用 Tampermonkey 用户脚本：在 Tampermonkey 中新建脚本，粘贴
<a href="/userscript.js" target="_blank">这个用户脚本</a>的内容。之后打开课表页，点击右下角
“导入课表”按钮即可。用户脚本通过本机桥转发数据，不受页面跨域限制。</p>
<h2>解析失败怎么办？</h2>
<p>失败时页面快照会保存到运行目录的 <code>jwxt-snapshots/</code> 下。
把该快照文件提供给开发者，或直接告诉我们学校和系统名称，即可继续适配。</p>
<script>
function copyLoader() {
  const text = document.getElementById('loader').textContent;
  navigator.clipboard.writeText(text).then(
    () => alert('已复制，去教务系统课表页面粘贴到 Console 即可'),
    () => alert('复制失败，请手动选中复制')
  );
}
</script>
</body>
</html>"""


USERSCRIPT_JS = """// ==UserScript==
// @name         jwxt 课表导入桥
// @namespace    jwxt-schedule
// @version      0.1.0
// @description  把当前页面的课表发送到本机 jwxt browser 服务
// @match        http://*/*
// @match        https://*/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// ==/UserScript==

(function () {
  "use strict";
  const endpoint = "http://127.0.0.1:__PORT__";
  const button = document.createElement("button");
  button.textContent = "导入课表";
  Object.assign(button.style, {
    position: "fixed", right: "18px", bottom: "18px", zIndex: 999999,
    padding: "8px 12px", borderRadius: "8px", border: "1px solid #bbb",
    background: "#fff", color: "#222", boxShadow: "0 2px 8px rgba(0,0,0,.15)",
    cursor: "pointer", opacity: "0.85"
  });

  function post(payload, callback) {
    GM_xmlhttpRequest({
      method: "POST",
      url: endpoint + "/api/import",
      headers: { "Content-Type": "application/json" },
      data: JSON.stringify(payload),
      onload: function (res) {
        let data = {};
        try { data = JSON.parse(res.responseText); } catch (e) {}
        callback(data);
      },
      onerror: function () { callback({ ok: false, message: "无法连接本机 jwxt browser" }); }
    });
  }

  window.__JWXT_BRIDGE_POST__ = post;
  button.addEventListener("click", function () {
    button.textContent = "正在导入…";
    GM_xmlhttpRequest({
      method: "GET",
      url: endpoint + "/snippet.js",
      onload: function (res) {
        try {
          eval(res.responseText);
          button.textContent = "导入课表";
        } catch (e) {
          alert("导入失败：" + e);
          button.textContent = "导入课表";
        }
      },
      onerror: function () {
        alert("无法连接本机 jwxt browser，请先运行：jwxt browser");
        button.textContent = "导入课表";
      }
    });
  });
  document.documentElement.appendChild(button);
})();
"""


# ---------------- HTTP 服务 ----------------
class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "jwxt-browser-bridge"
    bridge: "BrowserBridge"

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.bridge.verbose:
            super().log_message(fmt, *args)

    # ---- 响应工具 ----
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    # ---- HTTP 方法 ----
    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain")

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            html = (
                BRIDGE_PAGE
                .replace("__PORT__", str(self.bridge.port))
                .replace("__BOOKMARKLET__", html_module.escape(_bookmarklet(self.bridge.port), quote=True))
            ).encode("utf-8")
            self._send(200, html, "text/html; charset=utf-8")
            return
        if self.path.startswith("/snippet.js"):
            body = _snippet_js(self.bridge.port).encode("utf-8")
            self._send(200, body, "application/javascript; charset=utf-8")
            return
        if self.path.startswith("/userscript.js"):
            body = USERSCRIPT_JS.replace("__PORT__", str(self.bridge.port)).encode("utf-8")
            self._send(200, body, "application/javascript; charset=utf-8")
            return
        if self.path.startswith("/api/health"):
            self._json(200, {"ok": True, "outdir": str(self.bridge.outdir), "port": self.bridge.port})
            return
        self._json(404, {"ok": False, "message": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.startswith("/api/import"):
            self._json(404, {"ok": False, "message": "not found"})
            return
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError:
            length = 0
        if length <= 0:
            self._json(400, {"ok": False, "message": "empty body"})
            return
        if length > MAX_BODY_BYTES:
            self._json(413, {"ok": False, "message": "页面太大，超过 50MB 限制"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json(400, {"ok": False, "message": f"invalid json: {exc}"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"ok": False, "message": "payload must be an object"})
            return

        result = import_from_browser(
            payload,
            outdir=self.bridge.outdir,
            config=self.bridge.config,
            save_raw=self.bridge.save_snapshots,
        )
        self._json(200, result)
        if result.get("ok"):
            print(f"[browser] 导入成功：{result.get('summary')}")
            for item in result.get("files", []):
                print(f"[browser]   {item}")
        else:
            print(f"[browser] 导入失败：{result.get('message')}")
            if result.get("snapshot"):
                print(f"[browser]   页面快照：{result.get('snapshot')}")


class BrowserBridge:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        outdir: Path | None = None,
        config: AppConfig | None = None,
        save_snapshots: bool = True,
        verbose: bool = False,
    ):
        self.host = host
        self.port = port
        self.outdir = Path(outdir or ".").expanduser().resolve()
        self.config = config or browser_default_config(self.outdir)
        self.save_snapshots = save_snapshots
        self.verbose = verbose
        handler = type("Handler", (BridgeHandler,), {"bridge": self})
        self.httpd = ThreadingHTTPServer((host, port), handler)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def open_browser(self) -> None:
        webbrowser.open(self.url)


def run_browser_bridge(
    *,
    port: int = 8765,
    outdir: Path | None = None,
    open_browser: bool = True,
    save_snapshots: bool = True,
    verbose: bool = False,
) -> int:
    outdir = Path(outdir or ".").expanduser().resolve()
    try:
        bridge = BrowserBridge(
            port=port,
            outdir=outdir,
            save_snapshots=save_snapshots,
            verbose=verbose,
        )
    except OSError as exc:
        print(f"无法启动桥接服务（端口 {port} 可能被占用）：{exc}")
        return 1

    print("=" * 60)
    print("  jwxt 浏览器桥已启动")
    print(f"  本机地址：{bridge.url}")
    print(f"  输出目录：{outdir}")
    print(f"  学期：{bridge.config.term.name or '未设置'}"
          + (f"（第一周 {bridge.config.term.start_date.isoformat()}）" if bridge.config.term.start_date else ""))
    print("=" * 60)
    print("请保持本窗口运行，然后在浏览器中：")
    print("  1. 打开本地桥页面（通常已自动打开）")
    print("  2. 正常登录学校教务系统，打开课表页")
    print("  3. 按 F12 → Console，粘贴页面上的加载器并回车")
    print("按 Ctrl+C 停止。")
    if open_browser:
        threading.Timer(0.4, bridge.open_browser).start()
    try:
        bridge.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止……")
    finally:
        bridge.shutdown()
    return 0


__all__ = [
    "BrowserBridge",
    "BrowserBridgeError",
    "import_from_browser",
    "parse_browser_payload",
    "run_browser_bridge",
]
