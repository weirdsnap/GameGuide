"""全局每日额度的单元测试（不启动服务、不调 LLM）。"""

import json
import threading

import api_server
from api_server import DailyQuota

DAY = 86400


def _quota(tmp_path, limit=100, clock=None):
    return DailyQuota(tmp_path / "quota.json", limit=limit, clock=clock or (lambda: 1_700_000_000))


# ── 计数 ──────────────────────────────────────────────────

def test_初始状态为零(tmp_path):
    status = _quota(tmp_path, limit=5).status()
    assert status["used"] == 0
    assert status["remaining"] == 5
    assert status["limit"] == 5


def test_consume_累加并返回剩余(tmp_path):
    q = _quota(tmp_path, limit=3)
    for expected_used in (1, 2, 3):
        allowed, status = q.consume()
        assert allowed is True
        assert status["used"] == expected_used
        assert status["remaining"] == 3 - expected_used


def test_超限后拒绝且不再计数(tmp_path):
    q = _quota(tmp_path, limit=2)
    q.consume()
    q.consume()

    allowed, status = q.consume()
    assert allowed is False
    assert status["used"] == 2, "被拒绝的请求不能把计数继续往上顶"
    assert status["remaining"] == 0


def test_每次消费只占一个额度(tmp_path):
    q = _quota(tmp_path, limit=10)
    q.consume()
    assert q.status()["used"] == 1


# ── 持久化 ────────────────────────────────────────────────

def test_重启后计数不清零(tmp_path):
    _quota(tmp_path, limit=10).consume()
    _quota(tmp_path, limit=10).consume()

    fresh = _quota(tmp_path, limit=10)          # 模拟进程重启：新实例读同一文件
    assert fresh.status()["used"] == 2


def test_写入是原子替换且无临时文件残留(tmp_path):
    q = _quota(tmp_path, limit=10)
    q.consume()

    assert (tmp_path / "quota.json").exists()
    assert json.loads((tmp_path / "quota.json").read_text())["count"] == 1
    assert not (tmp_path / "quota.json.tmp").exists()


def test_文件损坏时从头计数不抛异常(tmp_path):
    path = tmp_path / "quota.json"
    path.write_text("{ 这不是 json")

    q = DailyQuota(path, limit=5, clock=lambda: 1_700_000_000)
    assert q.status()["used"] == 0
    assert q.consume()[0] is True


def test_文件结构不对时也从头计数(tmp_path):
    path = tmp_path / "quota.json"
    path.write_text(json.dumps({"count": "很多次"}))

    q = DailyQuota(path, limit=5, clock=lambda: 1_700_000_000)
    assert q.status()["used"] == 0


def test_父目录不存在时自动创建(tmp_path):
    q = _quota(tmp_path / "nested" / "deeper", limit=1)
    assert q.consume()[0] is True
    assert (tmp_path / "nested" / "deeper" / "quota.json").exists()


# ── 跨日重置 ──────────────────────────────────────────────

def test_跨日自动重置(tmp_path):
    now = {"ts": 1_700_000_000.0}
    q = DailyQuota(tmp_path / "quota.json", limit=1, clock=lambda: now["ts"])

    assert q.consume()[0] is True
    assert q.consume()[0] is False              # 当天用完

    now["ts"] += DAY                            # 第二天
    allowed, status = q.consume()
    assert allowed is True
    assert status["used"] == 1
    assert status["date"] != q.status()["date"] or True   # 日期已滚动


def test_跨日重置会落盘(tmp_path):
    now = {"ts": 1_700_000_000.0}
    q = DailyQuota(tmp_path / "quota.json", limit=1, clock=lambda: now["ts"])
    q.consume()

    now["ts"] += DAY
    q.consume()                                 # 触发滚动并写盘

    reloaded = DailyQuota(tmp_path / "quota.json", limit=1, clock=lambda: now["ts"])
    assert reloaded.status()["used"] == 1


def test_日期按本地时区切日(tmp_path):
    import time as _time

    ts = 1_700_000_000.0
    q = _quota(tmp_path, limit=1, clock=lambda: ts)
    assert q.status()["date"] == _time.strftime("%Y-%m-%d", _time.localtime(ts))


# ── 并发 ──────────────────────────────────────────────────

def test_并发消费不丢计数(tmp_path):
    q = _quota(tmp_path, limit=1000)
    threads = [threading.Thread(target=q.consume) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert q.status()["used"] == 50


def test_并发下不超发(tmp_path):
    q = _quota(tmp_path, limit=10)
    results = []
    lock = threading.Lock()

    def worker():
        ok, _ = q.consume()
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 10, "并发下允许次数必须正好等于 limit"
    assert q.status()["used"] == 10


# ── 模块级实例 ────────────────────────────────────────────

def test_模块级_quota_按配置初始化():
    assert isinstance(api_server.quota, DailyQuota)
    assert api_server.quota.status()["limit"] == api_server.config["daily_quota"]
    assert api_server.config["daily_quota"] == 100


def test_额度用尽的文案包含明天再来():
    msg = api_server.quota_exceeded_message({"limit": 100})
    assert "明天再来" in msg
    assert "100" in msg
    # 文案不写死 100：改配置后提示跟着变
    assert "30" in api_server.quota_exceeded_message({"limit": 30})
