"""每项目一个进程锁；进程退出由操作系统释放，不用残留文件判断正在运行。"""

import os
from contextlib import contextmanager
from pathlib import Path


class AnalysisBusy(ValueError):
    pass


@contextmanager
def analysis_lock(directory: Path):
    with (directory / 'analysis.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AnalysisBusy('此项目正在拆解，请等待完成；不要重复启动') from exc
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def analysis_running(directory: Path):
    try:
        with analysis_lock(directory):
            return False
    except AnalysisBusy:
        return True
