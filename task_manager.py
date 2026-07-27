#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
后台任务管理器 - 使用队列与主线程事件循环通信

支持两种使用模式：
  1) 单例常驻模式 (view.py)：
        tm = TaskManager(app)
        tm.start()                       # 启动常驻 worker 线程
        tm.submit(func, *args, **kwargs, progress_callback=cb)
        tm.stop()

  2) 临时一次性模式 (controller.py / dialogs.py)：
        tm = TaskManager(app, controller=None)   # 第二个参数向后兼容
        tm.run_async(func, on_done=cb, on_error=cb, progress_callback=cb2)
        # 内部创建一个临时 daemon 线程跑完即退，on_done/on_error 自动 after(0) 回主线程
"""
import threading
import queue
import os
from typing import Any, Callable, Optional
from logger import default_logger as logger


class TaskManager:
    def __init__(self, app, controller: Any = None):
        """
        :param app: 必须，用于调用 app.after(0, cb) 把回调调度回主线程
        :param controller: 可选，向后兼容（历史代码传了第二个参数，内部不再使用）
        """
        self.app = app
        # 模式1：常驻 worker（调用 start/stop 才会用到）
        self._running = False
        self._task_queue: "queue.Queue[tuple]" = queue.Queue()
        self._result_queue: "queue.Queue[tuple]" = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # 模式2：run_async 临时线程（不与常驻模式共享）
        self._one_shot_threads: list[threading.Thread] = []

    # ================================================================
    # 模式1：常驻 worker（app 启动时 start，退出时 stop）
    # ================================================================
    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._worker, daemon=True, name="TmWorker")
        self._worker_thread.start()
        self._poll_results()

    def stop(self):
        self._running = False
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)

    def submit(self, func, *args, progress_callback=None, **kwargs):
        self._task_queue.put((func, args, kwargs, progress_callback))

    def _worker(self):
        while not self._stop_event.is_set():
            try:
                func, args, kwargs, progress_callback = self._task_queue.get(timeout=0.5)
                if progress_callback:
                    kwargs['_progress_callback'] = progress_callback
                try:
                    result = func(*args, **kwargs)
                    self._result_queue.put(('success', result, None))
                except Exception as e:
                    logger.exception("常驻任务失败: %s", e)
                    self._result_queue.put(('error', None, str(e)))
                self._task_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.exception("工作线程异常: %s", e)
        logger.info("常驻 TaskManager 工作线程已停止")

    def _poll_results(self):
        try:
            while True:
                typ, result, error = self._result_queue.get_nowait()
                try:
                    if typ == 'success':
                        self.app.after(0, lambda r=result: self._safe_dispatch_done(r))
                    else:
                        self.app.after(0, lambda e=error: self._safe_dispatch_error(e))
                finally:
                    self._result_queue.task_done()
        except queue.Empty:
            pass
        finally:
            if self._running:
                self.app.after(100, self._poll_results)

    def _safe_dispatch_done(self, result):
        cb = getattr(self.app, 'on_task_done', None)
        if callable(cb):
            try:
                cb(result)
            except Exception as e:
                logger.exception("on_task_done 异常: %s", e)

    def _safe_dispatch_error(self, error):
        cb = getattr(self.app, 'on_task_error', None)
        if callable(cb):
            try:
                cb(error)
            except Exception as e:
                logger.exception("on_task_error 异常: %s", e)

    # ================================================================
    # 模式2：run_async —— 一次性临时线程（最常用）
    # ================================================================
    def run_async(
        self,
        func: Callable[..., Any],
        *,
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[float, str], None]] = None,
    ) -> None:
        """
        开一个 daemon 线程跑 func(*args, **kwargs)，完成后自动 after(0) 回主线程调 on_done/on_error。

        func 可通过 kwargs 接收：
          - _progress_callback(percent: float, message: str)
          - _log(message: str, level: str = 'info')
        """
        def _progress_wrapper(percent, message):
            if on_progress is None:
                return
            try:
                # 先尝试 helpers.update_progress（节流），否则直接 after
                hlp = getattr(self.app, 'helpers', None)
                up = getattr(hlp, 'update_progress', None)
                if callable(up):
                    # 只需要数值 0..100，message 用第二参数传
                    try:
                        up(float(percent), str(message))
                        return
                    except Exception:
                        pass
                self.app.after(0, lambda p=percent, m=message: on_progress(p, m))
            except Exception as e:
                logger.debug("progress wrapper 异常: %s", e)

        def _log_wrapper(message: str, level: str = 'info'):
            hlp = getattr(self.app, 'helpers', None)
            on_log = getattr(hlp, 'on_log', None)
            if callable(on_log):
                try:
                    # 在子线程记日志即可 —— logger/GuiLogHandler 自己处理回主线程
                    on_log(str(message), str(level))
                except Exception as e:
                    logger.debug("log wrapper 异常: %s", e)

        def _thread_body():
            try:
                result = func(
                    _progress_callback=_progress_wrapper,
                    _log=_log_wrapper,
                )
                if on_done is not None:
                    try:
                        self.app.after(0, lambda: on_done(result))
                    except Exception as e:
                        logger.debug("调度 on_done 失败: %s", e)
            except Exception as e:
                logger.exception("一次性后台任务异常: %s", e)
                err_msg = str(e)
                if on_error is not None:
                    try:
                        self.app.after(0, lambda: on_error(err_msg))
                    except Exception as e2:
                        logger.debug("调度 on_error 失败: %s", e2)
                elif on_done is None:
                    # 既没 on_done 也没 on_error：友好提示
                    hlp = getattr(self.app, 'helpers', None)
                    if hlp is not None:
                        on_log = getattr(hlp, 'on_log', None)
                        if callable(on_log):
                            try:
                                on_log(f"后台任务失败：{err_msg}", "error")
                            except Exception:
                                pass

        t = threading.Thread(target=_thread_body, daemon=True, name="TmOneShot")
        self._one_shot_threads.append(t)
        # 清理已死的线程引用，防止无限增长
        if len(self._one_shot_threads) > 64:
            self._one_shot_threads = [x for x in self._one_shot_threads if x.is_alive()]
        t.start()
