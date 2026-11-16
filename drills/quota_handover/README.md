# 值班交接额度演练（quota handover drill）

给接班操作者的一套可复跑演练：在 Facet Proof 上用**真实 HTTP 请求**安排一段
「额度缩小 × 运行中取消 × 失败后再执行」三者交错的局面，把每一步的请求、
额度账目和最终图版全部留痕，供接班者练习判断——

> 哪些画布工作量已经承诺（必须见到终态才能销账），哪些只是请求停止
> （取消被接受但额度还没腾出来）。

## 局面设计

四个曝光键，一段 17 步的时间线（S0–S16）：

| 键 | 请求 | 单位（高采样像素） | 在局面中的角色 |
| --- | --- | --- | --- |
| `alpha` | 512×512×2 | 1,048,576 | 渲染途中被取消：接受取消时额度**不**立即归还 |
| `bravo` | 16×16×1 | 256 | 工作器崩溃 → failed → retry 重新过闸门 → 成功 |
| `charlie` | 16×16×1 | 256 | 全程排队的对照组，超额期间照常完成 |
| `delta` | 16×16×1 | 256 | 超额期间提交，被 429 拒绝，不留任何痕迹 |

容量曲线：默认 4,194,304 → 1,200,000（S0）→ 400（S8）→ 512（S10）→ 400（S12）。
S12 之后账面 `reserved=512 > capacity=400`，`overcommitted=112` 是**暂时超额**：
bravo/charlie 的承诺继续有效，并在 S15 照常渲染成功。

## 复跑

```sh
python3 drills/quota_handover/scenario.py --force
```

- 需要 Python 3.11+，从仓库根目录运行；只依赖标准库。
- 默认数据根 `./facet-drill-data`、产物目录 `drills/quota_handover/artifacts/`、
  端口 8799，可用 `--root/--output/--port` 改。目录非空时需 `--force` 清除重建。
- 脚本内置断言：任何一步的账目或状态与预期不符都会以退出码 2 失败，
  因此每次复跑都是一次自核对。
- 整个运行中只有两类动作不经过 HTTP，且都在 transcript 里以 marker 明示：
  - **S7 故障注入**：进程内 `claim`+`fail`，模拟工作器领取 bravo 后崩溃；
  - **S14 服务重启**：停止并在同一数据根上重新启动服务进程。

## 产物清单（artifacts/）

| 文件 | 内容 | 用途 |
| --- | --- | --- |
| `transcript.jsonl` | 每一次 HTTP 请求/响应（含状态码与错误体），按 `step` 字段分步 | 接班者据真实请求推演 |
| `steps.json` | 每步结束时的 `GET /resources` 账目与各曝光承诺（active/units/generation/release_reason） | 逐步额度账目 |
| `REPORT.md` | 由运行数据生成的交班账目：承诺一览、逐步表格、关键证据、图版清单 | 交班阅读材料 |
| `restart-check.json` | S14 重启前后 resources/曝光/进度逐字段对比 + recover/reconcile 结果 | 重启后核对承诺去向 |
| `events/exposure-*.json` | 各曝光的完整事件历史（capacity_reserved/released、claimed、cancel_requested…） | 答案依据 |
| `events/sample-policy.json` | 容量策略全部修订记录 | 答案依据 |
| `plates/*.png`、`plates/*-bundle.zip` | bravo/charlie 的最终图版通道与交付包 | 最终图版留档；bundle 可用 `python3 -m facetreader` 离线核验 |
| `server-1.log`、`server-2.log` | 重启前后两个服务进程的请求日志 | 交叉核对 |

## 交接使用建议

1. 交班前由交班者（或值班长）复跑一次，确认退出码 0。
2. 接班者先只读 `transcript.jsonl` 与 `steps.json`，按 `WORKBOOK.md` 的题目推演。
3. 对照 `ANSWERS.md` 核对判断，逐条追到他引用的 transcript 步骤、事件与代码位置。
4.  optional：把 `plates/bravo-bundle.zip` 交给 `python3 -m facetreader` 离线核验，
   体会「包内一致 ≠ 当前审阅仍有效」。

练习题目见 [WORKBOOK.md](WORKBOOK.md)，答案与依据见 [ANSWERS.md](ANSWERS.md)。
