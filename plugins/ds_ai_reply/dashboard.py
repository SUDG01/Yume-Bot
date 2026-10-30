"""本地观测台：查看 bot 收到的消息、判断、上下文、模型请求/回复和记忆抽取。

只监听 127.0.0.1（可配），通过 SSH 隧道访问服务器上的面板。
"""

from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from nonebot import logger

from .config import DashboardConfig
from .memory import store

_server: uvicorn.Server | None = None
_task: asyncio.Task | None = None


DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YumeBot · AI 观测台</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #0d0d0d;
    --surface: #1a1a19;
    --surface-2: #232322;
    --text: #f0efec;
    --text-2: #c3c2b7;
    --muted: #898781;
    --border: rgba(255,255,255,.10);
    --grid: #2c2c2a;
    --blue: #3987e5;
    --orange: #d95926;
    --aqua: #199e70;
    --yellow: #c98500;
    --magenta: #d55181;
    --green: #0ca30c;
    --violet: #9085e9;
    --red: #e66767;
  }
  html[data-theme="light"] {
    color-scheme: light;
    --bg: #f9f9f7;
    --surface: #fcfcfb;
    --surface-2: #f0efec;
    --text: #0b0b0b;
    --text-2: #52514e;
    --muted: #898781;
    --border: rgba(11,11,11,.10);
    --grid: #e1e0d9;
    --blue: #2a78d6;
    --orange: #eb6834;
    --aqua: #1baf7a;
    --yellow: #eda100;
    --magenta: #e87ba4;
    --green: #008300;
    --violet: #6250d6;
    --red: #e34948;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  header {
    position: sticky; top: 0; z-index: 5;
    display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
    padding: 14px 20px;
    background: var(--bg);
    background: color-mix(in srgb, var(--bg) 88%, transparent);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--border);
  }
  .title { font-size: 18px; font-weight: 650; letter-spacing: .2px; }
  .title span { color: var(--muted); font-weight: 500; margin-left: 8px; font-size: 13px; }
  .stats { display: flex; gap: 14px; color: var(--text-2); font-size: 13px; }
  .stats b { color: var(--text); font-variant-numeric: tabular-nums; }
  .actions { margin-left: auto; display: flex; gap: 8px; }
  button, select, input[type="search"] {
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 7px 10px;
    font: inherit;
    outline: none;
  }
  button { cursor: pointer; }
  button:hover { background: var(--surface-2); }
  .toolbar {
    position: sticky; top: 63px; z-index: 4;
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    padding: 10px 20px;
    background: var(--bg);
    background: color-mix(in srgb, var(--bg) 92%, transparent);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--border);
  }
  .toolbar input[type="search"] { min-width: 220px; flex: 1; max-width: 380px; }
  .toolbar label { color: var(--text-2); display: flex; gap: 6px; align-items: center; font-size: 13px; }
  .conn { margin-left: auto; color: var(--muted); font-size: 12px; }
  .conn.bad { color: var(--red); }
  main { padding: 14px 20px 60px; display: flex; flex-direction: column; gap: 6px; }
  .empty { color: var(--muted); padding: 60px 0; text-align: center; }
  .event {
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--surface);
    overflow: hidden;
  }
  .event .line {
    display: flex; align-items: baseline; gap: 10px;
    padding: 9px 12px;
    cursor: pointer;
  }
  .event .line:hover { background: var(--surface-2); }
  .event time { color: var(--muted); font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: nowrap; }
  .badge {
    flex: 0 0 auto;
    font-size: 11px; font-weight: 650; letter-spacing: .4px;
    padding: 2px 7px; border-radius: 999px;
    color: #fff; background: var(--muted);
  }
  .trace { color: var(--muted); font: 11px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; }
  .summary { color: var(--text); overflow-wrap: anywhere; }
  .detail {
    border-top: 1px solid var(--border);
    padding: 10px 12px 12px;
    background: color-mix(in srgb, var(--surface) 70%, var(--bg));
  }
  .detail.hidden { display: none; }
  .detail pre {
    margin: 0; white-space: pre-wrap; overflow-wrap: anywhere;
    font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace;
    color: var(--text-2);
  }
  .msg { margin: 0 0 8px; }
  .msg:last-child { margin-bottom: 0; }
  .role {
    display: inline-block; font-size: 11px; font-weight: 650;
    padding: 1px 6px; border-radius: 4px; margin-bottom: 3px;
    background: var(--surface-2); color: var(--text-2);
    border: 1px solid var(--border);
  }
  .role.system { color: var(--violet); }
  .role.user { color: var(--blue); }
  .role.assistant { color: var(--aqua); }
  .event[data-kind="message"] .badge { background: var(--blue); }
  .event[data-kind="decision"] .badge { background: var(--yellow); color: #0b0b0b; }
  .event[data-kind="context"] .badge { background: var(--violet); }
  .event[data-kind="llm_request"] .badge { background: var(--orange); }
  .event[data-kind="llm_response"] .badge { background: var(--aqua); }
  .event[data-kind="reply"] .badge { background: var(--green); }
  .event[data-kind="willingness"] .badge { background: var(--violet); }
  .event[data-kind="reply_split"] .badge { background: var(--aqua); }
  .event[data-kind="memory_extract"] .badge { background: var(--magenta); }
  .event[data-kind="command"] .badge { background: var(--violet); }
  .event[data-kind="error"] .badge { background: var(--red); }
  .event[data-kind="system"] .badge { background: var(--muted); }
  @media (max-width: 720px) {
    .stats { display: none; }
    .trace { display: none; }
    .toolbar { top: 57px; }
  }
</style>
</head>
<body>
<header>
  <div class="title">YumeBot <span>AI 观测台</span></div>
  <div class="stats" id="stats"></div>
  <div class="actions">
    <button id="refresh" type="button">刷新</button>
    <button id="theme" type="button">切换主题</button>
  </div>
</header>
<div class="toolbar">
  <select id="session"><option value="">全部会话</option></select>
  <select id="kind">
    <option value="">全部类型</option>
    <option value="message">消息</option>
    <option value="decision">判断</option>
    <option value="context">上下文</option>
    <option value="llm_request">模型请求</option>
    <option value="llm_response">模型返回</option>
    <option value="reply">回复</option>
    <option value="willingness">意愿判断</option>
    <option value="reply_split">分条发送</option>
    <option value="memory_extract">记忆抽取</option>
    <option value="command">命令</option>
    <option value="error">错误</option>
    <option value="system">系统</option>
  </select>
  <input id="search" type="search" placeholder="搜索摘要…">
  <label><input id="pause" type="checkbox"> 暂停刷新</label>
  <span class="conn" id="conn">连接中…</span>
</div>
<main id="feed"><div class="empty">正在加载…</div></main>
<script>
const KINDS = {
  message: "消息", decision: "判断", context: "上下文",
  llm_request: "模型请求", llm_response: "模型返回", reply: "回复",
  willingness: "意愿判断", reply_split: "分条发送",
  memory_extract: "记忆抽取", command: "命令", error: "错误", system: "系统"
};
const token = new URLSearchParams(location.search).get("token") || "";
const feed = document.getElementById("feed");
const conn = document.getElementById("conn");
const statsEl = document.getElementById("stats");
const sessionSel = document.getElementById("session");
const kindSel = document.getElementById("kind");
const searchEl = document.getElementById("search");
const pauseEl = document.getElementById("pause");
let expanded = new Set();
let lastRendered = [];
let timer = null;

function esc(value) { return String(value ?? ""); }
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = esc(text);
  return node;
}
function parseTime(value) {
  if (!value) return new Date();
  return new Date(String(value).replace(" ", "T") + "Z");
}
function fmtTime(value) {
  const d = parseTime(value);
  return d.toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
  }).replaceAll("/", "-");
}
async function api(path, params = {}) {
  const url = new URL(path, location.origin);
  const search = new URLSearchParams(params);
  if (token) search.set("token", token);
  const response = await fetch(url + "?" + search.toString(), { cache: "no-store" });
  if (response.status === 401) throw new Error("token invalid");
  if (!response.ok) throw new Error("HTTP " + response.status);
  return response.json();
}
function addMessageBlock(container, item) {
  const block = el("div", "msg");
  const role = (item && item.role) ? String(item.role) : "unknown";
  block.appendChild(el("div", "role " + role, role));
  block.appendChild(el("pre", "", item && item.content ? item.content : ""));
  container.appendChild(block);
}
function renderDetail(container, detail) {
  if (!detail || typeof detail !== "object") {
    container.appendChild(el("pre", "", String(detail ?? "")));
    return;
  }
  if (Array.isArray(detail.messages)) {
    detail.messages.forEach(item => addMessageBlock(container, item));
    const rest = {...detail};
    delete rest.messages;
    if (Object.keys(rest).length) {
      container.appendChild(el("pre", "", JSON.stringify(rest, null, 2)));
    }
    return;
  }
  if (typeof detail.text === "string" && Object.keys(detail).length === 1) {
    container.appendChild(el("pre", "", detail.text));
    return;
  }
  container.appendChild(el("pre", "", JSON.stringify(detail, null, 2)));
}
function render() {
  const nearBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
  feed.replaceChildren();
  if (!lastRendered.length) {
    feed.appendChild(el("div", "empty", "还没有事件。等一条群消息或私聊消息。"));
    return;
  }
  lastRendered.forEach(item => {
    const article = el("article", "event");
    article.dataset.kind = item.kind || "system";
    const line = el("div", "line");
    line.appendChild(el("time", "", fmtTime(item.created_at)));
    line.appendChild(el("span", "badge", KINDS[item.kind] || item.kind || "事件"));
    if (item.trace_id) line.appendChild(el("span", "trace", "#" + item.trace_id.slice(-10)));
    line.appendChild(el("span", "summary", item.summary || ""));
    article.appendChild(line);
    const detail = el("div", "detail hidden");
    renderDetail(detail, item.detail);
    article.appendChild(detail);
    if (expanded.has(item.id)) detail.classList.remove("hidden");
    line.addEventListener("click", () => {
      if (detail.classList.contains("hidden")) {
        expanded.add(item.id);
        detail.classList.remove("hidden");
      } else {
        expanded.delete(item.id);
        detail.classList.add("hidden");
      }
    });
    feed.appendChild(article);
  });
  if (nearBottom && !pauseEl.checked) window.scrollTo(0, document.body.scrollHeight);
}
async function loadSessions() {
  try {
    const data = await api("/api/sessions");
    const current = sessionSel.value;
    sessionSel.replaceChildren(new Option("全部会话", ""));
    (data.sessions || []).forEach(item => {
      const label = (item.session_id || "?") + " · " + item.total;
      sessionSel.appendChild(new Option(label, item.session_id));
    });
    sessionSel.value = current;
  } catch (error) {}
}
async function loadStats() {
  try {
    const data = await api("/api/stats");
    statsEl.replaceChildren(
      (() => { const s = el("span", "", "24h 事件 "); s.appendChild(el("b", "", data.today)); return s; })(),
      (() => { const s = el("span", "", "会话 "); s.appendChild(el("b", "", data.sessions)); return s; })(),
      (() => { const s = el("span", "", "累计 "); s.appendChild(el("b", "", data.total)); return s; })()
    );
  } catch (error) {}
}
async function load() {
  if (pauseEl.checked) return;
  try {
    const params = { limit: "400" };
    if (sessionSel.value) params.session_id = sessionSel.value;
    if (kindSel.value) params.kind = kindSel.value;
    if (searchEl.value.trim()) params.q = searchEl.value.trim();
    const data = await api("/api/events", params);
    lastRendered = data.events || [];
    conn.textContent = "已连接 · " + new Date().toLocaleTimeString("zh-CN", { hour12: false });
    conn.classList.remove("bad");
    render();
  } catch (error) {
    conn.textContent = error.message === "token invalid" ? "token 无效" : "连接失败：" + error.message;
    conn.classList.add("bad");
  }
}
function restartTimer() {
  if (timer) clearInterval(timer);
  timer = setInterval(load, 2000);
}
document.getElementById("refresh").addEventListener("click", () => { load(); loadStats(); loadSessions(); });
document.getElementById("theme").addEventListener("click", () => {
  const root = document.documentElement;
  const next = root.dataset.theme === "light" ? "dark" : "light";
  root.dataset.theme = next;
  localStorage.setItem("yume-theme", next);
});
sessionSel.addEventListener("change", load);
kindSel.addEventListener("change", load);
let searchTimer = null;
searchEl.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(load, 250);
});
pauseEl.addEventListener("change", () => {
  conn.textContent = pauseEl.checked ? "已暂停" : "已恢复";
});
const savedTheme = localStorage.getItem("yume-theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;
loadSessions();
loadStats();
load();
restartTimer();
setInterval(loadStats, 10000);
setInterval(loadSessions, 30000);
</script>
</body>
</html>
"""


def _create_app(config: DashboardConfig) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def auth(token: str | None) -> None:
        if config.token and token != config.token:
            raise HTTPException(status_code=401, detail="token invalid")

    @app.middleware("http")
    async def no_store(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index(token: str | None = Query(default=None)):
        auth(token)
        return HTMLResponse(DASHBOARD_HTML)

    @app.get("/api/events")
    async def api_events(
        after_id: int = 0,
        limit: int = 400,
        session_id: str = "",
        kind: str = "",
        q: str = "",
        token: str | None = Query(default=None),
    ):
        auth(token)
        events = await store.get_trace_events(
            after_id=after_id,
            limit=min(1000, max(1, limit)),
            session_id=session_id,
            kind=kind,
            search=q,
        )
        return {
            "events": events,
            "latest_id": events[-1]["id"] if events else after_id,
        }

    @app.get("/api/sessions")
    async def api_sessions(token: str | None = Query(default=None)):
        auth(token)
        return {"sessions": await store.get_trace_sessions()}

    @app.get("/api/stats")
    async def api_stats(token: str | None = Query(default=None)):
        auth(token)
        return await store.trace_stats()

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


async def start(config: DashboardConfig) -> None:
    global _server, _task
    if not config.enabled:
        return
    if config.host not in {"127.0.0.1", "localhost", "::1"} and not config.token:
        logger.error(
            "ds_ai_reply 观测台监听非本地地址时必须设置 dashboard.token，已跳过启动"
        )
        return

    app = _create_app(config)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.host,
            port=config.port,
            log_level="warning",
            access_log=False,
        )
    )
    # 不要覆盖 NoneBot 自己的信号处理
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.8)
    if task.done():
        error = task.exception()
        logger.error(f"ds_ai_reply 观测台启动失败：{error}")
        return
    _server = server
    _task = task
    suffix = "（需要 token）" if config.token else ""
    logger.info(
        f"ds_ai_reply 观测台已启动：http://{config.host}:{config.port}/ {suffix}"
    )


async def stop() -> None:
    global _server, _task
    if _server is not None:
        _server.should_exit = True
    if _task is not None and not _task.done():
        try:
            await asyncio.wait_for(_task, timeout=5)
        except asyncio.TimeoutError:
            _task.cancel()
    _server = None
    _task = None
