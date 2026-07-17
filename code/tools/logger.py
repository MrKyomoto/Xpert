import sys, os, datetime, contextlib

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")


class Tee:
    """同时写入终端和日志文件。"""
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.file = open(path, "w", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, text):
        self.stdout.write(text)
        self.file.write(text)
        self.file.flush()

    def flush(self):
        self.stdout.flush()
        self.file.flush()

    def close(self):
        self.file.close()


@contextlib.contextmanager
def capture_output(run_tag: str = ""):
    """替换 sys.stdout 为 Tee，日志保存到 logs/{timestamp}.log。"""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{run_tag}" if run_tag else ""
    log_path = os.path.join(LOG_DIR, f"{ts}{tag}.log")
    tee = Tee(log_path)
    old = sys.stdout
    sys.stdout = tee
    try:
        yield log_path
    finally:
        sys.stdout = old
        tee.close()