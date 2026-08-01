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
        # 使用共享的 ThreadPoolExecutor，避免无限开线程；on_done/on_error 自动 after(0) 回主线程
"""
import threading
import queue
import os
import atexit
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Callable, Optional
from logger import default_logger as logger


# ===== 全局线程池：所有 TaskManager 共享，避免多个实例各自开池 =====
# max_workers = min(8, CPU+2)，防止老机器资源耗尽；退出时 atexit 自动 shutdown
_MAX_WORKERS = min(8, (os.cpu_count() or 2) + 2)
_global_executor: "ThreadPoolExecutor | None" = ThreadPoolExecutor(
    max_workers=_MAX_WORKERS, thread_name_prefix="TmPool"
)
_global_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """懒获取全局 executor；若已 shutdown 则重建一个新的。"""
    global _global_executor
    with _global_executor_lock:
        if _global_executor is None or getattr(_global_executor, "_shutdown", False):
            _global_executor = ThreadPoolExecutor(
                max_workers=_MAX_WORKERS, thread_name_prefix="TmPool"
            )
        return _global_executor


def _shutdown_global_executor(wait: bool = True) -> None:
    """程序退出时调用；外部 stop() 时也可以手动触发。"""
    global _global_executor
    with _global_executor_lock:
        ex, _global_executor = _global_executor, None
    if ex is not None:
        try:
            ex.shutdown(wait=wait, cancel_futures=True)  # type: ignore[call-arg]
        except TypeError:
            # L-3 修复：旧版 concurrent.futures (<= Python 3.8) 不支持 cancel_futures
            # 降级时记录 warning，便于排查"老环境下程序退出很慢"这类问题
            try:
                logger.warning(
                    "当前 Python 版本 concurrent.futures 不支持 cancel_futures 参数，"
                    "已降级为不取消未完成任务（退出可能稍慢）。"
                )
            except Exception:
                pass
            try:
                ex.shutdown(wait=wait)
            except Exception:
                pass
        except Exception:
            pass


atexit.register(lambda: _shutdown_global_executor(wait=False))


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
        # 模式2：run_async（使用全局线程池，这里仅记录 futures 方便 stop 时取消）
        self._one_shot_futures: list[Future] = []
        self._futures_lock = threading.Lock()

    # ================================================================
    # 模式1：常驻 worker（app 启动时 start，退出时 stop）
    # ================================================================
    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        # ———— 非守护线程：解释器会等它结束，不会在 I/O 中被硬杀导致 Fatal Python error ————
        self._worker_thread = threading.Thread(target=self._worker, daemon=False, name="TmWorker")
        self._worker_thread.start()
        self._poll_results()

    def stop(self):
        # ———— 先停全局线程池（TmPool 线程），避免 run_async 的线程在退出时被强制终止 ————
        try:
            _shutdown_global_executor(wait=True)
        except Exception:
            pass
        self._running = False
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            logger.info("等待 TaskManager 工作线程退出...")
            # 5s 内每 0.1s join 一次，既不阻塞太久又能让 99% 情况正常结束
            for _ in range(50):
                self._worker_thread.join(timeout=0.1)
                if not self._worker_thread.is_alive():
                    break
            if self._worker_thread.is_alive():
                logger.warning("工作线程 5 秒内未退出，继续关闭（守护=False 时解释器仍会等它）")
        # 把 run_async 挂到这个实例上的未完成 futures 取消一下（尽力而为）
        with self._futures_lock:
            fs = list(self._one_shot_futures)
            self._one_shot_futures.clear()
        for f in fs:
            try:
                f.cancel()
            except Exception:
                pass

    def submit(self, func, *args, progress_callback=None, **kwargs):
        self._task_queue.put((func, args, kwargs, progress_callback))

    def _worker(self):
        try:
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
        finally:
            # 保证哪怕 while 里抛未知异常，也能留下停止日志，避免 "worker 去哪了" 不好排查
            try:
                logger.info("常驻 TaskManager 工作线程已停止")
            except Exception:
                pass

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
        except Exception as e:
            logger.debug("_poll_results 轮询异常: %s", e)
        finally:
            # stop() 后停止递归调度，不要再给已经在 destroy 的 app.after() 塞回调
            if self._running:
                try:
                    self.app.after(100, self._poll_results)
                except Exception:
                    pass

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
    # 模式2：run_async —— 全局线程池提交（不再每次开线程）
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
        把 func 提交到共享 ThreadPoolExecutor 中执行；完成后自动 after(0) 回主线程调 on_done/on_error。

        func 可通过 kwargs 接收：
          - _progress_callback(percent: float, message: str)
          - _log(message: str, level: str = 'info')
        """
        def _progress_wrapper(percent, message):
            if on_progress is None:
                return
            try:
                hlp = getattr(self.app, 'helpers', None)
                up = getattr(hlp, 'update_progress', None)
                if callable(up):
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
                    on_log(str(message), str(level))
                except Exception as e:
                    logger.debug("log wrapper 异常: %s", e)

        def _pool_body() -> Any:
            return func(
                _progress_callback=_progress_wrapper,
                _log=_log_wrapper,
            )

        def _on_future_done(fut: "Future") -> None:
            # ---- 从实例的 futures 引用列表中移除自己，避免无限增长 ----
            try:
                with self._futures_lock:
                    try:
                        self._one_shot_futures.remove(fut)
                    except ValueError:
                        pass
            except Exception:
                pass

            # ---- 把结果 / 异常调度回主线程 ----
            exc = fut.exception()
            if exc is None:
                result = fut.result()
                if on_done is not None:
                    try:
                        # 用默认参数绑定 result，避免 on_done 在调度前被其他 future 覆盖
                        self.app.after(0, lambda r=result: on_done(r))
                    except Exception as e:
                        logger.debug("调度 on_done 失败: %s", e)
                return

            # ---- 异常分支 ----
            logger.exception("一次性后台任务异常: %s", exc)
            err_msg = str(exc)
            if on_error is not None:
                try:
                    # 同样用默认参数绑定 err_msg
                    self.app.after(0, lambda m=err_msg: on_error(m))
                except Exception as e2:
                    logger.debug("调度 on_error 失败: %s", e2)
            elif on_done is None:
                hlp = getattr(self.app, 'helpers', None)
                if hlp is not None:
                    on_log = getattr(hlp, 'on_log', None)
                    if callable(on_log):
                        try:
                            on_log(f"后台任务失败：{err_msg}", "error")
                        except Exception:
                            pass

        executor = _get_executor()
        try:
            fut = executor.submit(_pool_body)
        except RuntimeError:
            # 某些旧环境在 interpreter 清理阶段可能会抛 shutdown 中异常
            logger.warning("线程池已关闭，run_async 退化为同步直接执行")
            try:
                _on_future_done_result: Any = None
                _on_future_done_exc: BaseException | None = None
                class _FakeFuture:
                    def __init__(self):
                        self._r = None
                        self._e: BaseException | None = None
                    def exception(self):
                        return self._e
                    def result(self):
                        if self._e is not None:
                            raise self._e
                        return self._r
                ff = _FakeFuture()
                try:
                    ff._r = _pool_body()
                except BaseException as _be:
                    ff._e = _be
                _on_future_done(ff)
            except Exception:
                pass
            return

        with self._futures_lock:
            self._one_shot_futures.append(fut)
            # 定期清理（长度超过 512 时扫一遍已完成的）
            if len(self._one_shot_futures) > 512:
                self._one_shot_futures = [x for x in self._one_shot_futures if not x.done()]
        fut.add_done_callback(_on_future_done)
