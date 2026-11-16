#!/usr/bin/env python3
"""值班交接额度演练驱动（可复跑、自核对）。

用真实 HTTP 请求在 Facet Proof 上安排一段交错局面，供接班者练习
「从真实请求判断哪些画布工作量已经承诺、哪些只是请求停止」：

  - 运行中取消：alpha 在渲染途中收到取消，终态前额度不归还（S3–S5）；
  - 失败后再执行：bravo 被注入工作器崩溃，retry 重新过额度闸门（S7–S11）；
  - 额度缩小：先 1200000→400，再 512→400，旧承诺保留、账面暂时超额（S8–S13）；
  - 服务重启：同一数据根上沿同一曝光核对承诺去向（S14）。

每一步的请求与响应写入 transcript.jsonl；每步结束时的额度账目与各曝光
承诺写入 steps.json；最终图版、事件历史、重启核对落盘；并生成 REPORT.md。

只有两类动作不经过 HTTP，transcript 中均以 marker 明示：
  * 故障注入（S7）：进程内 claim+fail，模拟工作器领取任务后崩溃；
  * 服务重启（S14）：停止并重新启动服务进程，验证持久承诺。

用法：
    python3 drills/quota_handover/scenario.py --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from facetproof.application import Application  # noqa: E402  仅用于故障注入

API = "/facet/v1/"
UNIT_ALPHA = 512 * 512 * 2 ** 2   # 1_048_576 高采样像素
UNIT_SMALL = 16 * 16 * 1 ** 2     # 256 高采样像素


class DrillError(Exception):
    pass


def expect(condition, message):
    if not condition:
        raise DrillError(message)


class Client:
    """记录每一次真实 HTTP 请求与响应到 transcript.jsonl。"""

    def __init__(self, base, transcript):
        self.base = base
        self.transcript = transcript
        self.seq = 0
        self.step = "S0"

    def _log(self, entry):
        self.seq += 1
        entry["seq"] = self.seq
        entry["step"] = self.step
        self.transcript.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.transcript.flush()

    def mark(self, note, **extra):
        self._log({"type": "marker", "note": note, **extra})

    def call(self, method, path, body=None, expect_status=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(self.base + API + path, data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise DrillError(f"{method} {path} 连接失败: {exc}") from exc
        parsed = json.loads(raw) if raw else None
        self._log({"type": "http", "method": method, "path": API + path,
                   "request": body, "status": status, "response": parsed})
        if expect_status is not None:
            expect(status == expect_status,
                   f"{method} {path} 期望 {expect_status}，实际 {status}: {parsed}")
        return status, parsed

    def download(self, path, expect_status=200):
        req = urllib.request.Request(self.base + API + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        expect(status == expect_status, f"GET {path} 期望 {expect_status}，实际 {status}")
        self._log({"type": "http", "method": "GET", "path": API + path,
                   "request": None, "status": status,
                   "response": {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}})
        return raw


def prepare_dirs(root, output, force):
    for path in (root, output):
        resolved = path.resolve()
        expect(resolved not in (Path("/"), REPO_ROOT, REPO_ROOT.parent),
               f"拒绝使用危险目录: {resolved}")
        if resolved.exists() and any(resolved.iterdir()):
            expect(force, f"目录非空: {resolved}（用 --force 清除后重跑）")
            shutil.rmtree(resolved)
        resolved.mkdir(parents=True, exist_ok=True)
    expect(root.resolve() != output.resolve(), "数据根与产物目录必须不同")


def wait_health(host, port, proc, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise DrillError(f"服务进程提前退出，退出码 {proc.returncode}")
        try:
            with urllib.request.urlopen(f"http://{host}:{port}{API}health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.1)
    raise DrillError("服务健康检查超时")


def start_server(root, host, port, output, tag):
    log = (output / f"server-{tag}.log").open("wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "facetproof", "--root", str(root),
         "--host", host, "--port", str(port)],
        cwd=REPO_ROOT, stdout=log, stderr=log)
    proc.logfile = log
    wait_health(host, port, proc)
    return proc


def stop_server(proc, host, port):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    proc.logfile.close()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex((host, port)) != 0:
                return
        time.sleep(0.1)


def cell(exposure):
    if exposure is None:
        return "—"
    alloc = exposure.get("allocation")
    if alloc and alloc["active"]:
        promise = f"承诺{alloc['units']}·g{alloc['generation']}"
    elif alloc:
        promise = f"已释放·{alloc['release_reason']}"
    else:
        promise = "无承诺记录"
    return f"{exposure['state']}·{promise}"


def render_report(meta, steps, ids, plates, restart, final_ledger):
    lines = [
        "# 值班交接额度演练账目（scenario.py 自动生成）",
        "",
        f"- 运行时间：{meta['finished']}",
        f"- 数据根：`{meta['root']}`（服务已停止，可复跑重建）",
        f"- 额度单位：高采样像素（width × height × samples²），不是物理内存上限",
        f"- 最终账目：capacity={final_ledger['capacity']} reserved={final_ledger['reserved']} "
        f"available={final_ledger['available']} overcommitted={final_ledger['overcommitted']}",
        "",
        "## 曝光与承诺一览",
        "",
        "| 键 | 曝光 ID | 请求 | 单位 | 最终状态 | 承诺去向 | 图版 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in meta["roster"]:
        lines.append("| " + " | ".join(row) + " |")
    lines += [
        "",
        "## 逐步账目（每步结束时的真实 GET 结果）",
        "",
        "| 步骤 | 动作 | capacity | reserved | available | overcommitted | alpha | bravo | charlie | delta |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in steps:
        ledger = entry.get("ledger", {})
        exposures = entry.get("exposures", {})
        cells = [entry["step"], entry["title"],
                 str(ledger.get("capacity", "")), str(ledger.get("reserved", "")),
                 str(ledger.get("available", "")), str(ledger.get("overcommitted", ""))]
        cells += [cell(exposures.get(key)) for key in ("alpha", "bravo", "charlie", "delta")]
        lines.append("| " + " | ".join(cells) + " |")
        if entry.get("note"):
            lines.append(f"| | ↳ {entry['note']} | | | | | | | | |")
    lines += [
        "",
        "## 关键证据（可在 transcript.jsonl 按 step 过滤复核）",
        "",
        "- **S4→S5**：`POST /exposures/{alpha}/cancel` 返回 200 时 reserved 仍为 "
        f"{restart.get('cancel_reserved', '见 steps.json')}，alpha 终态（cancelled）后才归还。"
        "接受取消 ≠ 立即腾出空间。",
        "- **S8→S12**：缩容只改策略上限，bravo/charlie 的承诺不消失；S12 后 "
        "overcommitted=112 是暂时超额，不是已接收任务被撤销。",
        "- **S9**：bravo 在 failed 后第一次 retry 被 429 拒绝——终态已归还额度，"
        "再执行是新的承诺，必须重新过闸门。",
        "- **S13**：delta 被 429 拒绝后账目无任何变化：拒绝不留孤立预留。",
        "- **S14**：服务重启前后 resources、各曝光状态与承诺逐字段一致，"
        "reconcile 返回全空（无需补记），事件历史完整。",
        "- **S15**：available=0 且账面超额时，已承诺的 bravo/charlie 照常渲染成功；"
        "领取任务只看有效承诺，不看策略余量。",
        "",
        "## 重启核对（S14）",
        "",
        "```json",
        json.dumps(restart.get("summary", {}), ensure_ascii=False, indent=2),
        "```",
        "",
        "## 最终图版",
        "",
    ]
    if plates:
        for item in plates:
            lines.append(f"- `{item['key']}` → 图版 `{item['plate_id']}`，校验 valid="
                         f"{item['valid']}，通道文件：`plates/{item['key']}-color.png` 等，"
                         f"交付包：`plates/{item['key']}-bundle.zip`")
    else:
        lines.append("- （无）")
    lines += [
        "- `alpha`：无图版（已取消）。其已提交分块仍可由 "
        "`GET /exposures/{id}/progress` 核对，但进度不等于图版。",
        "",
        "## 复跑",
        "",
        "```sh",
        "python3 drills/quota_handover/scenario.py --force",
        "```",
        "",
        "练习题目见 `WORKBOOK.md`，答案与依据见 `ANSWERS.md`。",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="值班交接额度演练")
    parser.add_argument("--root", default="facet-drill-data", help="服务数据根（需为空或不存在）")
    parser.add_argument("--output", default="drills/quota_handover/artifacts", help="演练产物目录")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--force", action="store_true", help="清除已存在的数据根与产物目录")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    output = Path(args.output).resolve()
    prepare_dirs(root, output, args.force)

    ids = {"alpha": None, "bravo": None, "charlie": None, "delta": None}
    steps = []
    plates = []
    restart = {}
    server = None
    finished = time.strftime("%Y-%m-%d %H:%M:%S %z")

    transcript_path = output / "transcript.jsonl"
    with transcript_path.open("w", encoding="utf-8") as transcript:
        client = Client(f"http://{args.host}:{args.port}", transcript)

        def capture(title, note=""):
            entry = {"step": client.step, "title": title, "note": note}
            _, entry["ledger"] = client.call("GET", "resources", expect_status=200)
            entry["exposures"] = {}
            for key, exposure_id in ids.items():
                if exposure_id:
                    _, entry["exposures"][key] = client.call(
                        "GET", f"exposures/{exposure_id}", expect_status=200)
            steps.append(entry)
            return entry

        def ledger():
            _, snap = client.call("GET", "resources", expect_status=200)
            return snap

        try:
            # ---- S0 环境初始化 ------------------------------------------------
            client.step = "S0"
            server = start_server(root, args.host, args.port, output, 1)
            client.mark("服务启动（数据根为空，首次初始化）", root=str(root), port=args.port)
            client.call("GET", "health", expect_status=200)
            _, mesh = client.call("POST", "meshes", {
                "name": "斜翼",
                "vertices": [[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0], [0, 0, 1.2]],
                "faces": [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4], [0, 3, 2], [0, 2, 1]]},
                expect_status=200)
            _, pigment = client.call("POST", "pigments",
                                     {"name": "釉青", "color": [.12, .62, .61], "two_sided": True},
                                     expect_status=200)
            _, assembly = client.call("POST", "assemblies",
                                      {"name": "斜翼结构说明图", "lens": {"eye": [3, 2, 4]}},
                                      expect_status=200)
            _, assembly = client.call("PUT", "assemblies/" + assembly["id"], {
                "revision": 1,
                "placements": [{"key": "wing", "mesh_id": mesh["id"],
                                "pigment_id": pigment["id"],
                                "transform": {"euler": [0, 20, 0]}}]}, expect_status=200)
            _, seal = client.call("POST", f"assemblies/{assembly['id']}/seal",
                                  {"revision": 2}, expect_status=200)
            baseline = ledger()
            expect(baseline["capacity"] == 4194304 and baseline["revision"] == 1,
                   f"默认策略异常: {baseline}")
            _, revised = client.call("PUT", "resources",
                                     {"capacity": 1200000, "revision": 1}, expect_status=200)
            expect(revised["revision"] == 2, f"策略修订异常: {revised}")
            capture("建快照并把画布额度设为 1,200,000 高采样像素",
                    "默认策略 4,194,304 → 1,200,000；本班容量基线")

            # ---- S1 接收 alpha（大画布） --------------------------------------
            client.step = "S1"
            _, alpha = client.call("POST", f"seals/{seal['id']}/exposures",
                                   {"key": "alpha", "width": 512, "height": 512, "samples": 2},
                                   expect_status=200)
            ids["alpha"] = alpha["id"]
            expect(alpha["allocation"]["active"] == 1 and
                   alpha["allocation"]["units"] == UNIT_ALPHA, "alpha 承诺缺失")
            entry = capture("接收 alpha：512×512×2 = 1,048,576 单位", "接收与预留同一事务")
            expect(entry["ledger"]["reserved"] == UNIT_ALPHA, "alpha 预留未入账")

            # ---- S2 接收 bravo -------------------------------------------------
            client.step = "S2"
            _, bravo = client.call("POST", f"seals/{seal['id']}/exposures",
                                   {"key": "bravo", "width": 16, "height": 16, "samples": 1},
                                   expect_status=200)
            ids["bravo"] = bravo["id"]
            entry = capture("接收 bravo：16×16×1 = 256 单位")
            expect(entry["ledger"]["reserved"] == UNIT_ALPHA + UNIT_SMALL, "bravo 预留未入账")

            # ---- S3 工作器领取 alpha 开渲染；接收 charlie ----------------------
            client.step = "S3"
            worker_result = {}

            def background_worker():
                worker_result["response"] = client.call("POST", "worker/step",
                                                        expect_status=200)[1]

            thread = threading.Thread(target=background_worker, daemon=True)
            client.mark("后台发起 POST /worker/step（同步领取并渲染最老的 queued 曝光）")
            thread.start()
            deadline = time.monotonic() + 90
            while True:
                _, progress = client.call("GET", f"exposures/{ids['alpha']}/progress",
                                          expect_status=200)
                if progress["committed_tiles"] >= 1:
                    break
                expect(time.monotonic() < deadline, "alpha 渲染迟迟未提交首个分块")
                time.sleep(0.25)
            client.mark("alpha 已提交首个高采样块", progress=progress)
            _, charlie = client.call("POST", f"seals/{seal['id']}/exposures",
                                     {"key": "charlie", "width": 16, "height": 16, "samples": 1},
                                     expect_status=200)
            ids["charlie"] = charlie["id"]
            entry = capture("alpha 渲染途中接收 charlie：256 单位",
                            "渲染进行时不影响新接收；三块账目同时挂账")
            expect(entry["ledger"]["reserved"] == UNIT_ALPHA + 2 * UNIT_SMALL,
                   "charlie 预留未入账")

            # ---- S4 运行中取消 alpha：接受 ≠ 腾出 ------------------------------
            client.step = "S4"
            _, cancelled = client.call("POST", f"exposures/{ids['alpha']}/cancel",
                                       expect_status=200)
            expect(cancelled["state"] == "running" and cancelled["cancel_requested"] == 1,
                   f"运行中取消的响应异常: {cancelled['state']}")
            entry = capture("运行中取消 alpha：返回 200，但状态仍 running",
                            "关键：此时 reserved 不变，1,048,576 单位仍挂在 alpha 名下")
            expect(entry["ledger"]["reserved"] == UNIT_ALPHA + 2 * UNIT_SMALL,
                   "接受取消不应立即腾出额度")
            expect(entry["exposures"]["alpha"]["allocation"]["active"] == 1,
                   "接受取消不应释放承诺")

            # ---- S5 等待 alpha 真实终态后才归还 --------------------------------
            client.step = "S5"
            deadline = time.monotonic() + 90
            while True:
                _, current = client.call("GET", f"exposures/{ids['alpha']}", expect_status=200)
                if current["state"] == "cancelled":
                    break
                expect(time.monotonic() < deadline, f"alpha 未进入终态: {current['state']}")
                time.sleep(0.2)
            thread.join(timeout=120)
            expect(worker_result["response"]["state"] == "cancelled",
                   f"工作器应报告 cancelled: {worker_result}")
            client.mark("工作器心跳发现取消，alpha 进入终态 cancelled",
                        worker_response=worker_result["response"])
            _, progress = client.call("GET", f"exposures/{ids['alpha']}/progress",
                                      expect_status=200)
            expect(progress["planned"] and not progress["complete"]
                   and progress["committed_tiles"] >= 1,
                   "已提交分块应保留可查")
            entry = capture("alpha 终态 cancelled：额度此刻才归还",
                            "已提交分块保留可查，但进度不等于图版")
            expect(entry["ledger"]["reserved"] == 2 * UNIT_SMALL, "终态后应只剩 bravo/charlie")
            expect(entry["exposures"]["alpha"]["allocation"]["release_reason"] == "cancelled",
                   "alpha 承诺应以 cancelled 归还")

            # ---- S6 同键重发：不是新承诺 --------------------------------------
            client.step = "S6"
            _, again = client.call("POST", f"seals/{seal['id']}/exposures",
                                   {"key": "alpha", "width": 512, "height": 512, "samples": 2},
                                   expect_status=200)
            expect(again["id"] == ids["alpha"] and again["state"] == "cancelled",
                   "同键重发应返回原曝光")
            status, conflict = client.call("POST", f"seals/{seal['id']}/exposures",
                                           {"key": "alpha", "width": 256, "height": 512,
                                            "samples": 2})
            expect(status == 409 and conflict["error"] == "key_conflict",
                   f"同键异参应 409: {status} {conflict}")
            entry = capture("同键重发 alpha：同一曝光、不重复预留；异参 409",
                            "重发不是新承诺；账目不动")

            # ---- S7 故障注入：bravo 领取后崩溃 --------------------------------
            client.step = "S7"
            client.mark("故障注入开始：进程内 claim+fail，模拟工作器领取 bravo 后崩溃"
                        "（非 HTTP 动作；观察仍走 HTTP）")
            injection = Application(str(root))
            claimed = injection.exposures.claim()
            expect(claimed is not None and claimed["id"] == ids["bravo"],
                   f"应领到最老的 queued 曝光 bravo: {claimed and claimed['id']}")
            injection.exposures.fail(claimed["id"], claimed["token"], "worker_crash")
            client.mark("故障注入完成：bravo 以 worker_crash 进入 failed",
                        attempt=claimed["attempt"])
            entry = capture("bravo 失败终态：额度随终态归还",
                            "failed 是终态；256 单位归还，可用额度回升")
            expect(entry["ledger"]["reserved"] == UNIT_SMALL, "bravo 失败后应只剩 charlie")
            expect(entry["exposures"]["bravo"]["state"] == "failed" and
                   entry["exposures"]["bravo"]["allocation"]["release_reason"] == "failed",
                   "bravo 承诺应以 failed 归还")

            # ---- S8 第一次缩容 -------------------------------------------------
            client.step = "S8"
            _, revised = client.call("PUT", "resources",
                                     {"capacity": 400, "revision": 2}, expect_status=200)
            expect(revised["revision"] == 3 and revised["overcommitted"] == 0,
                   f"缩容异常: {revised}")
            capture("缩容：capacity 1,200,000 → 400",
                    "charlie 的 256 单位仍在；available=144")

            # ---- S9 失败后再执行：第一次 retry 被闸门拒绝 ----------------------
            client.step = "S9"
            status, denied = client.call("POST", f"exposures/{ids['bravo']}/retry", {})
            expect(status == 429 and denied["error"] == "capacity_reserved",
                   f"retry 应被闸门拒绝: {status} {denied}")
            entry = capture("retry bravo 被 429 拒绝：available=144 < 256",
                            "failed 已归还；再执行是新承诺，须重新过闸门")
            expect(entry["ledger"]["reserved"] == UNIT_SMALL, "拒绝不得留下预留")
            expect(entry["exposures"]["bravo"]["state"] == "failed", "拒绝不得改变状态")

            # ---- S10 值班长临时提额 -------------------------------------------
            client.step = "S10"
            _, revised = client.call("PUT", "resources",
                                     {"capacity": 512, "revision": 3}, expect_status=200)
            expect(revised["revision"] == 4 and revised["available"] == UNIT_SMALL,
                   f"提额异常: {revised}")
            capture("值班长决策：capacity 400 → 512 以便重跑 bravo")

            # ---- S11 再次 retry：重新承诺第 2 代 -------------------------------
            client.step = "S11"
            _, retried = client.call("POST", f"exposures/{ids['bravo']}/retry", {},
                                     expect_status=200)
            expect(retried["state"] == "queued" and
                   retried["allocation"]["active"] == 1 and
                   retried["allocation"]["generation"] == 2,
                   f"retry 后应重新预留第 2 代: {retried['allocation']}")
            entry = capture("retry bravo 成功：queued，承诺第 2 代",
                            "同一曝光身份不变，尝试号与承诺代次增长")

            # ---- S12 再次缩容：暂时超额，旧承诺保留 -----------------------------
            client.step = "S12"
            _, revised = client.call("PUT", "resources",
                                     {"capacity": 400, "revision": 4}, expect_status=200)
            expect(revised["revision"] == 5 and revised["overcommitted"] == 112,
                   f"应出现 112 的暂时超额: {revised}")
            entry = capture("缩容：capacity 512 → 400，账面超额 112",
                            "关键：bravo/charlie 承诺不消失，超额只是账面相减")
            expect(entry["exposures"]["bravo"]["allocation"]["active"] == 1 and
                   entry["exposures"]["charlie"]["allocation"]["active"] == 1,
                   "缩容不得撤销已接受承诺")

            # ---- S13 新请求被拒 + 过期策略修订被拒 -----------------------------
            client.step = "S13"
            status, denied = client.call("POST", f"seals/{seal['id']}/exposures",
                                         {"key": "delta", "width": 16, "height": 16,
                                          "samples": 1})
            expect(status == 429 and denied["error"] == "capacity_reserved",
                   f"delta 应被 429 拒绝: {status} {denied}")
            status, stale = client.call("PUT", "resources",
                                        {"capacity": 800, "revision": 4})
            expect(status == 409 and stale["error"] == "stale_revision",
                   f"过期修订应 409: {status} {stale}")
            entry = capture("delta 被 429 拒绝；过期 revision 的提额被 409 拒绝",
                            "拒绝不留痕迹：delta 没有曝光、没有预留；账目不变")
            expect(entry["ledger"]["reserved"] == 2 * UNIT_SMALL, "拒绝不得改变账目")

            # ---- S14 服务重启：沿同一曝光核对承诺去向 ---------------------------
            client.step = "S14"
            before = {"resources": ledger(),
                      "exposures": {},
                      "progress_alpha": client.call(
                          "GET", f"exposures/{ids['alpha']}/progress", expect_status=200)[1]}
            for key in ("alpha", "bravo", "charlie"):
                _, before["exposures"][key] = client.call(
                    "GET", f"exposures/{ids[key]}", expect_status=200)
            client.mark("停止服务进程（模拟交班重启）")
            stop_server(server, args.host, args.port)
            server = None
            server = start_server(root, args.host, args.port, output, 2)
            client.mark("服务在同一数据根重启完成")
            after = {"resources": ledger(),
                     "exposures": {},
                     "progress_alpha": client.call(
                         "GET", f"exposures/{ids['alpha']}/progress", expect_status=200)[1]}
            for key in ("alpha", "bravo", "charlie"):
                _, after["exposures"][key] = client.call(
                    "GET", f"exposures/{ids[key]}", expect_status=200)
            identical = {
                "resources": before["resources"] == after["resources"],
                "progress_alpha": before["progress_alpha"] == after["progress_alpha"],
                **{key: before["exposures"][key] == after["exposures"][key]
                   for key in before["exposures"]},
            }
            expect(all(identical.values()), f"重启前后账目不一致: {identical}")
            _, recovered = client.call("POST", "worker/recover", expect_status=200)
            expect(recovered == {"recovered": []}, f"不应有待恢复租约: {recovered}")
            _, reconcile = client.call("POST", "resources/reconcile", expect_status=200)
            expect(reconcile["adopted"] == [] and reconcile["released"] == [] and
                   reconcile["waiting"] == [], f"账目应自洽无需补记: {reconcile}")
            _, history = client.call("GET", f"events/{ids['alpha']}/history",
                                     expect_status=200)
            expect(len(history["items"]) >= 4, "alpha 事件历史应完整")
            restart = {"before": before, "after": after, "identical": identical,
                       "recover": recovered, "reconcile": reconcile,
                       "cancel_reserved": UNIT_ALPHA + 2 * UNIT_SMALL,
                       "summary": {"逐项一致": identical, "reconcile": {
                           "adopted": reconcile["adopted"], "released": reconcile["released"],
                           "waiting": reconcile["waiting"]}}}
            (output / "restart-check.json").write_text(
                json.dumps(restart, ensure_ascii=False, indent=2), encoding="utf-8")
            capture("服务重启：同一数据根上账目、承诺、进度逐字段一致",
                    "reconcile 全空：承诺去向可沿同一曝光核对，无需补记")

            # ---- S15 排空：超额不阻塞已承诺工作 --------------------------------
            client.step = "S15"
            _, run_bravo = client.call("POST", "worker/step", expect_status=200)
            expect(run_bravo["state"] == "succeeded" and
                   run_bravo["exposure_id"] == ids["bravo"],
                   f"bravo 应成功: {run_bravo.get('state')}")
            mid = ledger()
            expect(mid["reserved"] == UNIT_SMALL, "bravo 成功后应只剩 charlie")
            _, run_charlie = client.call("POST", "worker/step", expect_status=200)
            expect(run_charlie["state"] == "succeeded" and
                   run_charlie["exposure_id"] == ids["charlie"],
                   f"charlie 应成功: {run_charlie.get('state')}")
            _, idle = client.call("POST", "worker/step", expect_status=200)
            expect(idle == {"state": "idle"}, f"队列应排空: {idle}")
            entry = capture("bravo、charlie 依次渲染成功，队列排空",
                            "available=0 时领取只看有效承诺；成功终态归还额度")
            expect(entry["ledger"]["reserved"] == 0, "全部终态后应无挂账")
            for key, run in (("bravo", run_bravo), ("charlie", run_charlie)):
                plate = run["plate"]
                _, verification = client.call("POST", f"plates/{plate['id']}/verify",
                                              expect_status=200)
                expect(verification["valid"], f"图版校验失败: {verification}")
                plates.append({"key": key, "plate_id": plate["id"],
                               "valid": verification["valid"]})

            # ---- S16 收尾：图版、事件、最终账目落盘 -----------------------------
            client.step = "S16"
            plates_dir = output / "plates"
            plates_dir.mkdir(exist_ok=True)
            for item in plates:
                for name in ("color.png", "depth.png", "object.png"):
                    raw = client.download(f"plates/{item['plate_id']}/passes/{name}")
                    (plates_dir / f"{item['key']}-{name}").write_bytes(raw)
                bundle = client.download(f"plates/{item['plate_id']}/bundle")
                (plates_dir / f"{item['key']}-bundle.zip").write_bytes(bundle)
            events_dir = output / "events"
            events_dir.mkdir(exist_ok=True)
            for key in ("alpha", "bravo", "charlie"):
                _, history = client.call("GET", f"events/{ids[key]}/history",
                                         expect_status=200)
                (events_dir / f"exposure-{key}.json").write_text(
                    json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
            _, policy_history = client.call("GET", "events/sample-policy/history",
                                            expect_status=200)
            (events_dir / "sample-policy.json").write_text(
                json.dumps(policy_history, ensure_ascii=False, indent=2), encoding="utf-8")
            final_entry = capture("图版与事件历史落盘，本班结束",
                                  "reserved=0：所有承诺都有终态去向")

            meta = {
                "finished": finished,
                "root": str(root),
                "roster": [
                    ["alpha", ids["alpha"], "512×512×2", str(UNIT_ALPHA), "cancelled",
                     "已释放·cancelled（S5）", "无（取消不产图版）"],
                    ["bravo", ids["bravo"], "16×16×1", str(UNIT_SMALL), "succeeded",
                     "g1 已释放·failed（S7）；g2 已释放·succeeded（S15）", plates[0]["plate_id"]],
                    ["charlie", ids["charlie"], "16×16×1", str(UNIT_SMALL), "succeeded",
                     "已释放·succeeded（S15）", plates[1]["plate_id"]],
                    ["delta", "（未创建）", "16×16×1", str(UNIT_SMALL), "—",
                     "429 拒绝，无承诺记录（S13）", "无"],
                ],
            }
            report = render_report(meta, steps, ids, plates, restart,
                                   final_entry["ledger"])
            (output / "REPORT.md").write_text(report, encoding="utf-8")
            client.mark("演练完成", artifacts=str(output))
        except BaseException as exc:
            client.mark("演练中断", error=repr(exc))
            raise
        finally:
            if server is not None:
                stop_server(server, args.host, args.port)
            (output / "steps.json").write_text(
                json.dumps({"steps": steps}, ensure_ascii=False, indent=2),
                encoding="utf-8")

    print(f"演练完成：{len(steps)} 步，产物在 {output}")
    print(f"  交班账目: {output / 'REPORT.md'}")
    print(f"  请求留痕: {transcript_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DrillError as exc:
        print(f"演练失败: {exc}", file=sys.stderr)
        sys.exit(2)
