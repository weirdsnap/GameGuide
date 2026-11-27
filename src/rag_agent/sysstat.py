"""系统资源采样（看板用）——只读 /proc 与 statvfs，零第三方依赖。

口径与取舍（docs/DASHBOARD_PLAN.md §11）：

- 采「能指导动作」的指标：可用内存、swap 用量与换页累计、load average、
  CPU 占用（两次采样做差）、磁盘用量、进程自身 RSS / 线程数 / 句柄数。
- 不采网络带宽、磁盘 IOPS：日均一百来轮的小站上这些曲线恒为平线，
  采集成本比信息量大。
- 采样点落 `<metrics_dir>/YYYY-MM-DD.jsonl`，一天一个文件。过期删文件即可，
  不必重写历史；内存里只留上一次 /proc/stat 快照，用来算 CPU 差值。

所有读取都兜底：读不到就给 None，绝不让采样失败影响 API 主流程。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

# CPU 占用要有前后两次快照才能算，模块级存一份（单进程，够用）
_last_stat: tuple[int, int] | None = None


# ====== 解析层（纯函数，方便单测）======

def parse_meminfo(text: str) -> dict:
    """从 /proc/meminfo 取总量与可用量（单位 MB，kB 值 / 1024 取整）。

    MemAvailable 才是「现在还能给新进程的内存」；MemFree 只代表完全没被用的页，
    大量页在 buff/cache 里，看 MemFree 会一直误以为快没内存了。
    """
    vals: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            vals[key.strip()] = int(parts[0])
    out = {}
    for key, field in (("MemTotal", "mem_total_mb"), ("MemAvailable", "mem_avail_mb"),
                       ("SwapTotal", "swap_total_mb"), ("SwapFree", "swap_free_mb")):
        kb = vals.get(key)
        out[field] = None if kb is None else round(kb / 1024)
    # swap 已用先用 kB 相减再换算，避免两次四舍五入差 1MB
    total_kb, free_kb = vals.get("SwapTotal"), vals.get("SwapFree")
    out["swap_used_mb"] = (None if total_kb is None or free_kb is None
                           else max(0, round((total_kb - free_kb) / 1024)))
    return out


def parse_loadavg(text: str) -> dict:
    """load1/5/15。2 核机器上 load>2 才意味着真排队，单看数字要记住分母。"""
    parts = text.split()
    out = {}
    for i, field in enumerate(("load1", "load5", "load15")):
        try:
            out[field] = float(parts[i])
        except (IndexError, ValueError):
            out[field] = None
    return out


def parse_stat(text: str) -> tuple[int, int] | None:
    """从 /proc/stat 的 cpu 行取 (idle_all, total_all)，用于算两次采样之间的占用率。"""
    for line in text.splitlines():
        if not line.startswith("cpu "):
            continue
        parts = line.split()[1:]
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return None
        if len(nums) < 4:
            return None
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
        return idle, sum(nums)
    return None


def parse_vmstat(text: str) -> dict:
    """swap 换页累计（页数）。累计值本身没意义，看两次采样的增量。"""
    out = {"swap_in_pages": None, "swap_out_pages": None}
    for line in text.splitlines():
        key, _, val = line.partition(" ")
        if key == "pswpin" and val.strip().isdigit():
            out["swap_in_pages"] = int(val)
        elif key == "pswpout" and val.strip().isdigit():
            out["swap_out_pages"] = int(val)
    return out


def parse_self_status(text: str) -> dict:
    """进程自身 RSS（MB）与线程数：这是「我们的 Python 进程吃了多少」。"""
    out = {"rss_mb": None, "threads": None}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        if key.strip() == "VmRSS" and parts[0].isdigit():
            out["rss_mb"] = round(int(parts[0]) / 1024)
        elif key.strip() == "Threads" and parts[0].isdigit():
            out["threads"] = int(parts[0])
    return out


def disk_usage(path) -> dict:
    """磁盘总量/已用/可用（MB）。statvfs 在所有 POSIX 上都有，不需要额外库。"""
    try:
        st = os.statvfs(str(path))
    except OSError:
        return {"disk_total_mb": None, "disk_used_mb": None, "disk_free_mb": None}
    unit = st.f_frsize or st.f_bsize
    total = st.f_blocks * unit
    free = st.f_bavail * unit
    return {
        "disk_total_mb": round(total / 1048576),
        "disk_used_mb": round((total - st.f_bfree * unit) / 1048576),
        "disk_free_mb": round(free / 1048576),
    }


# ====== 采样层 ======

def _read_text(path) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def cpu_percent(stat_now: tuple[int, int] | None) -> float | None:
    """两次 /proc/stat 快照之间的 CPU 占用（%）。首次采样没有基准，返回 None。"""
    global _last_stat
    prev, _last_stat = _last_stat, stat_now
    if not stat_now or not prev:
        return None
    d_idle = stat_now[0] - prev[0]
    d_total = stat_now[1] - prev[1]
    if d_total <= 0:
        return None
    return round(max(0.0, min(100.0, (1 - d_idle / d_total) * 100)), 1)


def sample(proc_root="/proc", disk_path=None, now=None) -> dict:
    """采一个样。proc_root / disk_path 可注入，方便测试与将来换机器。"""
    proc_root = Path(proc_root)
    out = {"ts": int(now if now is not None else time.time())}
    out.update(parse_meminfo(_read_text(proc_root / "meminfo")))
    out.update(parse_loadavg(_read_text(proc_root / "loadavg")))
    out.update(parse_vmstat(_read_text(proc_root / "vmstat")))
    out.update(parse_self_status(_read_text(proc_root / "self" / "status")))
    out["cpu_pct"] = cpu_percent(parse_stat(_read_text(proc_root / "stat")))
    try:
        out["fds"] = len(os.listdir(proc_root / "self" / "fd"))
    except OSError:
        out["fds"] = None
    if disk_path is not None:
        out.update(disk_usage(disk_path))
    return out


# ====== 存储层 ======

def day_of(ts: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def append(metrics_dir, rec: dict) -> None:
    """追加一行采样。按天分文件，跨零点自动换新文件。"""
    directory = Path(metrics_dir)
    directory.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    with open(directory / f"{day_of(rec['ts'])}.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_series(metrics_dir, hours: int = 24, now: int | None = None) -> list[dict]:
    """读最近 hours 小时的采样点（升序）。坏行跳过，缺文件跳过，不抛异常。"""
    directory = Path(metrics_dir)
    now = int(now if now is not None else time.time())
    start = now - hours * 3600
    files = set()
    day = datetime.fromtimestamp(start)
    end_day = datetime.fromtimestamp(now)
    while day.date() <= end_day.date():
        files.add(day.strftime("%Y-%m-%d"))
        day += timedelta(days=1)

    out = []
    for name in sorted(files):
        path = directory / f"{name}.jsonl"
        if not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    ts = rec.get("ts")
                    if isinstance(ts, int) and start <= ts <= now:
                        out.append(rec)
        except OSError:
            continue
    out.sort(key=lambda r: r["ts"])
    return out


def downsample(series: list[dict], buckets: int = 180) -> list[dict]:
    """等分桶取均值，给前端画图用（2880 个点画折线既慢又看不出形状）。"""
    if len(series) <= buckets or buckets <= 0:
        return series
    step = len(series) / buckets
    numeric = ("mem_total_mb", "mem_avail_mb", "swap_total_mb", "swap_used_mb",
               "load1", "load5", "load15", "cpu_pct", "rss_mb", "fds",
               "disk_free_mb")
    out = []
    for i in range(buckets):
        chunk = series[int(i * step):int((i + 1) * step)]
        if not chunk:
            continue
        rec = {"ts": chunk[len(chunk) // 2]["ts"], "n": len(chunk)}
        for key in numeric:
            vals = [c[key] for c in chunk if isinstance(c.get(key), (int, float))]
            rec[key] = round(sum(vals) / len(vals), 1) if vals else None
        out.append(rec)
    return out


def retain(metrics_dir, days: int = 7, now: int | None = None) -> int:
    """删掉 days 天前的采样文件，返回删了几个。文件是天粒度，删文件不影响在读的当天文件。"""
    directory = Path(metrics_dir)
    if not directory.is_dir():
        return 0
    now = int(now if now is not None else time.time())
    cutoff = now - days * 86400
    removed = 0
    for path in directory.glob("*.jsonl"):
        try:
            stamp = time.mktime(time.strptime(path.stem, "%Y-%m-%d"))
        except ValueError:
            continue
        if stamp < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def health(rec: dict) -> dict:
    """把采样翻译成「要不要动手」的判定，看板直接染色，不用人记阈值。

    阈值按这台机器定：3723MB 内存、1987MB swap、69GB 磁盘、2 核。
    内存可用 <300MB 或 swap 用量 >1600MB 是危险线（现在 swap 长期 1.6G 以上，
    这一条已经在响了）。
    """
    mem_avail = rec.get("mem_avail_mb")
    swap_used = rec.get("swap_used_mb")
    swap_total = rec.get("swap_total_mb")
    disk_free = rec.get("disk_free_mb")
    load1 = rec.get("load1")

    def level(cond_bad, cond_warn):
        if cond_bad:
            return "bad"
        if cond_warn:
            return "warn"
        return "ok"

    return {
        "mem": level(mem_avail is not None and mem_avail < 300,
                     mem_avail is not None and mem_avail < 600),
        "swap": level(swap_used is not None and swap_total and swap_used > swap_total * 0.8,
                      swap_used is not None and swap_total and swap_used > swap_total * 0.5),
        "disk": level(disk_free is not None and disk_free < 2048,
                      disk_free is not None and disk_free < 5120),
        "load": level(load1 is not None and load1 > 4,
                      load1 is not None and load1 > 2),
    }
