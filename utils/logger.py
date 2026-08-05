#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🪵 升级日志模块（Python 3.10+ / Aurora Frost 风格）
───────────────────────────────────────────────
  ① 控制台彩色输出（ANSI，仅 TTY）：DEBUG=灰 / INFO=白 / SUCCESS=极光绿
                                   WARNING=火焰橙 / ERROR=红 / CRITICAL=红底白字
  ② 日志文件按日期切分：TimedRotatingFileHandler（每天 0 点，保留 14 天）
        过期日志自动打包为 .gz（节省 60%~85% 磁盘）
  ③ 可选 JSONL 结构化日志（适合后处理 / Grafana / jq）：环境变量
        MOLMAN_JSON_LOG=1 即可开启
  ④ 全局上下文注入：session_id / work_dir（用 LogFilter，非修改 record）
  ⑤ 🕐 performance_timer 装饰器 + 上下文管理器（ms 级），结果记 DEBUG 并累计 Top-N
  ⑥ MolManagerHandler：在不阻塞 GUI 的前提下把日志丢进 UI log_text

重构说明：
  - 移除重复的 _app_data_dir()，改用 path_utils.get_app_data_dir()
  - 保持所有外部接口不变
"""
import gzip
import io
import json
import logging
import logging.config
import logging.handlers
import os
import shutil
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, Optional, List, Tuple

from utils.path_utils import get_app_data_dir

# GUI 依赖：如果是非 GUI 环境（cli 脚本 / 测试），不 import tkinter，
# 但 GuiLogHandler 需要 tk.END 等常量，这里在模块开头先确定值。
try:
    import tkinter as _tk  # noqa: F401
    _TK_END = _tk.END
    _TK_AFTER = hasattr(_tk.Misc, "after")
    _HAS_TK = True
except Exception:  # pragma: no cover
    _tk = None  # type: ignore[assignment]
    _TK_END = "end"
    _TK_AFTER = False
    _HAS_TK = False

# ---------------- 目录 & 全局 ----------------
APP_DATA_DIR = get_app_data_dir()
LOG_DIR = APP_DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "mol_manager.log"
JSON_LOG_FILE = LOG_DIR / "mol_manager.jsonl"

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
VERBOSE_FMT = "%(asctime)s | %(levelname)-7s | %(name)-22s | SID=%(session_id)s | %(message)s"

# 自定义 SUCCESS 级别（=25，在 INFO/WARNING 之间）
LEVEL_SUCCESS = 25
logging.addLevelName(LEVEL_SUCCESS, "SUCCESS")


def success(self: logging.Logger, msg: object, *args: Any, **kwargs: Any) -> None:
    if self.isEnabledFor(LEVEL_SUCCESS):
        self._log(LEVEL_SUCCESS, msg, args, **kwargs)


logging.Logger.success = success  # type: ignore[attr-defined]

# ---------------- ANSI 彩色 ----------------


class _Ansi:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    GRAY    = "\033[38;5;246m"
    WHITE   = "\033[38;5;255m"
    GREEN   = "\033[38;2;14;162;136m"   # 极光绿
    BLUE    = "\033[38;2;59;110;255m"   # 量子蓝
    PURPLE  = "\033[38;2;139;92;246m"   # 分子紫
    ORANGE  = "\033[38;2;255;138;61m"   # 火焰橙
    RED     = "\033[38;2;229;72;77m"    # 红
    RED_BG  = "\033[48;2;229;72;77m"    # 红底


_LEVEL_STYLES: Dict[int, str] = {
    logging.DEBUG:    _Ansi.DIM + _Ansi.GRAY,
    logging.INFO:     _Ansi.WHITE,
    LEVEL_SUCCESS:    _Ansi.BOLD + _Ansi.GREEN,
    logging.WARNING:  _Ansi.BOLD + _Ansi.ORANGE,
    logging.ERROR:    _Ansi.BOLD + _Ansi.RED,
    logging.CRITICAL: _Ansi.BOLD + _Ansi.RED_BG + _Ansi.WHITE,
}


class ColorFormatter(logging.Formatter):
    """TTY 下的彩色 formatter；非 TTY 自动去掉颜色。自动补齐 session_id 默认值。"""

    def __init__(self, fmt: str, datefmt: str, use_color: bool | None = None):
        super().__init__(fmt=fmt, datefmt=datefmt)
        if use_color is None:
            self._use = (sys.stderr is not None
                         and hasattr(sys.stderr, "isatty")
                         and bool(sys.stderr.isatty())
                         and not bool(os.environ.get("NO_COLOR")))
        else:
            self._use = use_color

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "session_id"):
            try:
                record.session_id = _LOGGER_CONTEXT.session_id
            except Exception:
                record.session_id = "-"
        s = super().format(record)
        if not self._use:
            return s
        style = _LEVEL_STYLES.get(record.levelno, "")
        return f"{style}{s}{_Ansi.RESET}"


# ---------------- 过期日志自动 .gz 压缩 ----------------


class GzTimedRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """每日切分 + 切下来的旧日志自动 gzip（节省 ~75% 磁盘）"""

    def rotate(self, source: str, dest: str) -> None:  # pragma: no cover
        super().rotate(source, dest)
        gz_target = dest + ".gz"
        try:
            with open(dest, "rb") as fi, gzip.open(gz_target, "wb") as fo:
                shutil.copyfileobj(fi, fo, length=1 << 20)
            os.remove(dest)
        except OSError:
            pass  # 压缩失败就保留原文件，不抛

    def getFilesToDelete(self) -> list[str]:  # pragma: no cover
        # 同时清 .gz
        raw = super().getFilesToDelete()
        result: list[str] = []
        dir_name, base_name = os.path.split(self.baseFilename)
        for fp in raw:
            result.append(fp)
            gz = fp + ".gz"
            if os.path.exists(gz):
                result.append(gz)
        # 额外扫描 .gz 同格式的残余
        try:
            prefix = base_name + "."
            for f in os.listdir(dir_name):
                if f.startswith(prefix) and f.endswith(".gz"):
                    full = os.path.join(dir_name, f)
                    if full not in result and os.path.getmtime(full) < (time.time() - self.interval * (self.backupCount + 2)):
                        result.append(full)
        except OSError:
            pass
        return result


# ---------------- JSONL 格式化（支持 session_id / thread / file / line）----------------


class JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "session_id"):
            try:
                record.session_id = _LOGGER_CONTEXT.session_id
            except Exception:
                record.session_id = "-"
        rec: Dict[str, Any] = {
            "ts":   datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "lvl":  record.levelname,
            "name": record.name,
            "msg":  record.getMessage(),
            "sid":  getattr(record, "session_id", "-"),
            "t":    record.thread,
        }
        if record.exc_info:
            rec["exc"] = self.formatException(record.exc_info)
        if record.pathname:
            rec["file"] = f"{record.pathname}:{record.lineno}"
        try:
            return json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            rec["msg"] = repr(rec.get("msg"))
            return json.dumps(rec, ensure_ascii=False, separators=(",", ":"))


class VerboseFormatter(logging.Formatter):
    """文件 handler 用：保证 session_id 存在，避免 KeyError"""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "session_id"):
            try:
                record.session_id = _LOGGER_CONTEXT.session_id
            except Exception:
                record.session_id = "-"
        return super().format(record)


# ---------------- LogFilter：注入 session_id / work_dir ----------------


class ContextFilter(logging.Filter):
    def __init__(self, state: "LoggerContext"):
        super().__init__()
        self._state = state

    def filter(self, record: logging.LogRecord) -> bool:
        record.session_id = self._state.session_id
        return True


# ---------------- 全局上下文 & 性能计时 ----------------


@dataclass
class LoggerContext:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    # 性能 Top-N 排行（只保留最耗时的前 32 条，防止无限增长）
    _perf_records: list[Dict[str, Any]] = field(default_factory=list)
    work_dir: str = ""

    def record_perf(self, name: str, seconds: float, meta: Optional[Dict[str, Any]] = None) -> None:
        self._perf_records.append({
            "name": name, "ms": round(seconds * 1000, 2), "meta": meta,
            "ts": datetime.now().strftime(DATE_FORMAT),
        })
        if len(self._perf_records) > 128:
            self._perf_records.sort(key=lambda x: x["ms"], reverse=True)
            self._perf_records[:] = self._perf_records[:32]

    def top_perf(self, n: int = 10) -> list[Dict[str, Any]]:
        return sorted(self._perf_records, key=lambda x: x["ms"], reverse=True)[:n]


_LOGGER_CONTEXT = LoggerContext()
_CONTEXT_FILTER = ContextFilter(_LOGGER_CONTEXT)


def get_context() -> LoggerContext:
    return _LOGGER_CONTEXT


def set_work_dir(path: str | os.PathLike[str]) -> None:
    _LOGGER_CONTEXT.work_dir = str(path)


# ---------- performance_timer ----------

def performance_timer(name: Optional[str] = None, logger: Optional[logging.Logger] = None,
                      level: int = logging.DEBUG, min_ms: float = 1.0,
                      meta: Optional[Dict[str, Any]] = None) -> Callable[[Any], Any]:
    """
    装饰器 / 上下文管理器两用：性能计时。
    只记 >= min_ms 毫秒的调用，避免 DEBUG 噪音。
    """
    _lg = logger or default_logger
    _meta = meta or {}
    is_ctx = False  # 是否作为上下文管理器被用

    class _Ctx:
        def __init__(self, label: str):
            self.label = label
            self._t0: float = 0.0

        def __enter__(self) -> "_Ctx":
            self._t0 = time.perf_counter()
            return self

        def __exit__(self, exc_type, exc, tb):
            sec = time.perf_counter() - self._t0
            ms = sec * 1000
            if ms >= min_ms:
                _LOGGER_CONTEXT.record_perf(self.label, sec, _meta)
                mstr = "" if not _meta else f"  {_meta}"
                _lg.log(level, f"PERF {self.label}  →  {ms:,.2f} ms{mstr}")
            return False

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        label = name or f"{fn.__module__}.{fn.__qualname__}"

        @wraps(fn)
        def _inner(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                sec = time.perf_counter() - t0
                ms = sec * 1000
                if ms >= min_ms:
                    _LOGGER_CONTEXT.record_perf(label, sec, _meta)
                    mstr = "" if not _meta else f"  {_meta}"
                    _lg.log(level, f"PERF {label}  →  {ms:,.2f} ms{mstr}")
        return _inner

    if callable(name) and meta is None and not isinstance(name, str):
        # 裸装饰用法：@performance_timer 不带括号
        real_fn = name
        return _decorator(real_fn)
    return lambda fn: _decorator(fn) if callable(fn) else _Ctx(str(name))


# ---------------- GUI 异步 Handler（避免子线程直接改 Tk widget）----------------


class GuiLogHandler(logging.Handler):
    """
    把日志转发到 Tk Text 控件。
    注意：用 `after(0)` 把 widget 写入推回主线程，避免子线程直接改 Tk widget。
    """

    def __init__(self, get_app_callable: Callable[[], Any]):
        super().__init__(level=logging.DEBUG)
        # 问题四（日志面板空白）根因修复：
        # 原先这里用 `weakref.ref(get_app_callable)`，而调用方传的是内联 `lambda: self`。
        # 该 lambda 在 `attach_gui_handler` 返回后即无其他强引用，被 CPython 立即回收；
        # 而 `repaint_all` / `_flush` 是通过 `after(0)` 异步触发的，等到它们真正执行时，
        # `weakref.ref(...)()` 已经返回 None → `_resolve_app()` 返回 None → 两个方法直接 return，
        # 于是日志面板永远空白（但文件日志正常，极具迷惑性）。
        # handler 仅被 root logger 与全局 `_GUI_HANDLER` 持有，MainView 并不反向引用 handler，
        # 不存在循环引用，因此这里改存强引用是安全且正确的。
        self._get_app = get_app_callable
        self._use_weakref = False
        self._active: Dict[str, bool] = {
            "DEBUG": True, "INFO": True, "SUCCESS": True,
            "WARNING": True, "ERROR": True, "CRITICAL": True,
        }
        # _queue / _all_records 会被 emit（多线程）+ _flush/repaint/clear（主线程）同时读写，
        # 所有读写必须进入同一把 _lock，避免 list 内部结构被 race 破坏。
        self._lock = threading.Lock()
        self._queue: list[tuple[int, str, str]] = []
        self._all_records: list[tuple[int, str, str]] = []
        self._max_records = 50000

    def get_all_records(self) -> list[tuple[int, str, str]]:
        with self._lock:
            return list(self._all_records)

    def get_records_for_export(self) -> list[Dict[str, Any]]:
        from datetime import datetime
        out = []
        with self._lock:
            snap = list(self._all_records)
        for lvl, lvl_name, msg in snap:
            out.append({
                "time": datetime.now().strftime(DATE_FORMAT),
                "level": lvl_name,
                "level_no": lvl,
                "message": msg.rstrip("\n"),
            })
        return out

    def set_active(self, level_name: str, active: bool) -> None:
        k = level_name.upper()
        if k in self._active:
            self._active[k] = bool(active)

    def is_active(self, level_name: str) -> bool:
        return self._active.get(level_name.upper(), True)

    def _resolve_app(self) -> Any:
        g = self._get_app
        if callable(g):
            try:
                return g()
            except Exception:
                return g
        return g

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            rec = (record.levelno, record.levelname, msg)
            schedule_flush = False
            with self._lock:
                self._all_records.append(rec)
                if len(self._all_records) > self._max_records:
                    self._all_records[:] = self._all_records[-self._max_records // 2:]
                if self._active.get(record.levelname, True):
                    self._queue.append(rec)
                    if len(self._queue) > 4000:
                        self._queue[:] = self._queue[-2000:]
                    schedule_flush = True
            if schedule_flush:
                app = self._resolve_app()
                if app is not None and hasattr(app, "after"):
                    app.after(0, self._flush)
        except Exception:
            self.handleError(record)

    def repaint_all(self) -> None:
        """过滤芯片变化时：清屏并把 _all_records 里符合条件的重画一遍"""
        app = self._resolve_app()
        if app is None or not hasattr(app, "log_text"):
            return
        log_text = app.log_text
        with self._lock:
            snap = list(self._all_records)
            active_snap = dict(self._active)
        try:
            log_text.configure(state="normal")
            log_text.delete("1.0", _TK_END)
            count = 0
            max_lines = 8000
            total = len(snap)
            start_idx = max(0, total - max_lines)
            for i in range(start_idx, total):
                lvl, lvl_name, msg = snap[i]
                if not active_snap.get(lvl_name, True):
                    continue
                tag = "info"
                if lvl == logging.DEBUG:    tag = "debug"
                elif lvl == LEVEL_SUCCESS:  tag = "success"
                elif lvl == logging.WARNING: tag = "warning"
                elif lvl == logging.ERROR:   tag = "error"
                elif lvl >= logging.CRITICAL: tag = "critical"
                block = f"[{lvl_name:^7s}] {msg}\n"
                before_insert_line = int(log_text.index("end-1c linestart").split(".")[0])
                log_text.insert(_TK_END, block)
                new_end_line = int(log_text.index("end-1c linestart").split(".")[0])
                if new_end_line > before_insert_line:
                    start = f"{before_insert_line}.0"
                    end = f"{new_end_line}.end"
                    log_text.tag_add(tag, start, end)
                count += 1
            log_text.see(_TK_END)
        finally:
            try:
                log_text.configure(state="disabled")
            except Exception:
                pass

    def clear_all(self) -> None:
        with self._lock:
            self._all_records.clear()
            self._queue.clear()
        app = self._resolve_app()
        if app is None or not hasattr(app, "log_text"):
            return
        log_text = app.log_text
        try:
            log_text.configure(state="normal")
            log_text.delete("1.0", _TK_END)
        finally:
            try:
                log_text.configure(state="disabled")
            except Exception:
                pass

    def _flush(self) -> None:
        # 先加锁，快照 + 清空 queue 原子完成，避免并发 emit 在我们 flush 到一半时丢数据
        with self._lock:
            if not self._queue:
                return
            buf = self._queue[:]
            self._queue.clear()
        app = self._resolve_app()
        if app is None or not hasattr(app, "log_text"):
            return
        log_text = app.log_text
        try:
            log_text.configure(state="normal")
            for lvl, lvl_name, msg in buf:
                tag = "info"
                if lvl == logging.DEBUG:    tag = "debug"
                elif lvl == LEVEL_SUCCESS:  tag = "success"
                elif lvl == logging.WARNING: tag = "warning"
                elif lvl == logging.ERROR:   tag = "error"
                elif lvl >= logging.CRITICAL: tag = "critical"
                block = f"[{lvl_name:^7s}] {msg}\n"
                before_insert_line = int(log_text.index("end-1c linestart").split(".")[0])
                log_text.insert(_TK_END, block)
                new_end_line = int(log_text.index("end-1c linestart").split(".")[0])
                if new_end_line > before_insert_line:
                    start = f"{before_insert_line}.0"
                    end = f"{new_end_line}.end"
                    log_text.tag_add(tag, start, end)
            last_line = int(log_text.index("end-1c linestart").split(".")[0])
            if last_line > 30000:
                log_text.delete("1.0", f"{last_line - 20000}.0")
            log_text.see(_TK_END)
        finally:
            try:
                log_text.configure(state="disabled")
            except Exception:
                pass


# ---------------- 启动配置 ----------------

_MAIN_LOGGER_NAME = "MolManager"


def _make_console_handler() -> logging.Handler:
    h = logging.StreamHandler(stream=sys.stderr)
    h.setLevel(logging.INFO)
    h.setFormatter(ColorFormatter(fmt="%(asctime)s  [%(levelname)-7s]  %(name)-20s  %(message)s",
                                 datefmt=DATE_FORMAT))
    return h


def _make_file_handler() -> logging.Handler:
    h = GzTimedRotatingFileHandler(
        filename=str(LOG_FILE), when="midnight", interval=1,
        backupCount=14, encoding="utf-8", delay=False, utc=False,
    )
    h.setLevel(logging.DEBUG)
    h.setFormatter(VerboseFormatter(fmt=VERBOSE_FMT, datefmt=DATE_FORMAT))
    return h


def _make_json_handler() -> Optional[logging.Handler]:
    if not bool(int(os.environ.get("MOLMAN_JSON_LOG", "0"))):
        return None
    h = GzTimedRotatingFileHandler(
        filename=str(JSON_LOG_FILE), when="midnight", interval=1,
        backupCount=7, encoding="utf-8", delay=True,
    )
    h.setLevel(logging.DEBUG)
    h.setFormatter(JsonLinesFormatter())
    return h


def setup_logging() -> logging.Logger:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h); h.close()
    root.setLevel(logging.DEBUG)
    root.addFilter(_CONTEXT_FILTER)
    # —— 问题二：日志空白修复 ——
    # 在 GUI handler 挂载之前，所有日志先存进 _STARTUP_RECORDS，等 attach_gui_handler 时批量回放。
    startup_handler = _StartupRecordCollector()
    startup_handler.setLevel(logging.DEBUG)
    startup_handler.setFormatter(VerboseFormatter(fmt="%(message)s"))
    root.addHandler(startup_handler)
    # 去重（同一 session 不要重复添加）
    root.addHandler(_make_console_handler())
    root.addHandler(_make_file_handler())
    jh = _make_json_handler()
    if jh is not None:
        root.addHandler(jh)
    logger_obj = logging.getLogger(_MAIN_LOGGER_NAME)
    logger_obj.setLevel(logging.DEBUG)
    # 启动就写 banner：方便定位 session
    logger_obj.debug("=" * 68)
    logger_obj.info("🟢 启动新 Session sid=%s  work_dir=%s",
                    _LOGGER_CONTEXT.session_id, _LOGGER_CONTEXT.work_dir or "(未设置)")
    return logger_obj


# ---------------- 问题二：启动日志回放 ----------------
# _STARTUP_RECORDS 是 logger.py 级别全局队列，存储 setup_logging → attach_gui_handler 这段时间的所有日志，
# 解决「用户第一次打开 GUI 时日志面板一片空白」问题（因为 banner/PSI4 导入失败等都发生在 GUI 挂载前）。
_STARTUP_RECORDS: List[Tuple[int, str, str]] = []
_STARTUP_RECORDS_LOCK = threading.Lock()
_STARTUP_COLLECTED_MAX = 10000  # 再长就截断避免占内存


class _StartupRecordCollector(logging.Handler):
    """在 setup_logging 阶段临时挂载：所有日志除了 console/file，也入队 _STARTUP_RECORDS。"""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            try:
                msg = str(record.getMessage())
            except Exception:
                msg = ""
        rec = (record.levelno, record.levelname, msg)
        with _STARTUP_RECORDS_LOCK:
            _STARTUP_RECORDS.append(rec)
            if len(_STARTUP_RECORDS) > _STARTUP_COLLECTED_MAX:
                _STARTUP_RECORDS[:] = _STARTUP_RECORDS[-_STARTUP_COLLECTED_MAX // 2:]


def drain_startup_records() -> List[Tuple[int, str, str]]:
    """取出启动队列并清空；只在 attach_gui_handler 回放时用一次。"""
    with _STARTUP_RECORDS_LOCK:
        snap = list(_STARTUP_RECORDS)
        _STARTUP_RECORDS.clear()
    return snap


default_logger = setup_logging()

# 全局 GUI handler：MainView.build_ui 后调用 `attach_gui_handler(app)` 才有效
_GUI_HANDLER: Optional[GuiLogHandler] = None


def attach_gui_handler(app_ref: Callable[[], Any]) -> GuiLogHandler:
    """给 MainView 的 log_text 挂上 live 日志输出。返回 handler 供 UI 切过滤芯片时用。
    同时会把 setup_logging → 此刻之间 暂存的启动日志（_STARTUP_RECORDS）批量「回放」到 handler._all_records，
    并调用一次 repaint_all，确保 UI 打开后不会是空白面板。
    """
    global _GUI_HANDLER
    if _GUI_HANDLER is not None:
        try:
            logging.getLogger().removeHandler(_GUI_HANDLER)
        except Exception:
            pass
    handler = GuiLogHandler(app_ref)
    # 格式上 GUI 只展示 message，前缀由 on_log 自己加 tag；尽量简洁
    handler.setFormatter(logging.Formatter("%(message)s"))
    # —— 启动日志回放：填到 handler._all_records，再 repaint_all 一次即可渲染到 log_text ——
    startup = drain_startup_records()
    if startup:
        with handler._lock:
            handler._all_records.extend(startup)
            if len(handler._all_records) > handler._max_records:
                handler._all_records[:] = handler._all_records[-handler._max_records // 2:]
            # 启动日志默认也按过滤芯片可见（active 默认全 True），所以直接入 queue 也行，
            # 但更稳的是走 repaint_all（已经包含可见性过滤逻辑），因此 queue 不塞也行。
    logging.getLogger().addHandler(handler)
    _GUI_HANDLER = handler
    # —— 关键：触发一次重绘，把启动日志真正画到 log_text ——
    try:
        app = handler._resolve_app()
        if app is not None and hasattr(app, "after"):
            app.after(0, handler.repaint_all)
    except Exception:
        try:
            handler.repaint_all()
        except Exception:
            pass
    return handler


def get_gui_handler() -> Optional[GuiLogHandler]:
    return _GUI_HANDLER


__all__ = [
    "APP_DATA_DIR", "LOG_DIR", "LOG_FILE", "JSON_LOG_FILE",
    "DATE_FORMAT", "LEVEL_SUCCESS",
    "ContextFilter", "ColorFormatter", "JsonLinesFormatter",
    "GzTimedRotatingFileHandler", "GuiLogHandler",
    "LoggerContext", "setup_logging", "default_logger",
    "get_context", "set_work_dir", "performance_timer",
    "attach_gui_handler", "get_gui_handler",
    "_HAS_TK",
]
