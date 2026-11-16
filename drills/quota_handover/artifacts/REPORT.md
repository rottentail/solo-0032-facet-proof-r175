# 值班交接额度演练账目（scenario.py 自动生成）

- 运行时间：2026-11-16 14:08:05 +0000
- 数据根：`/workspace/facet-drill-data`（服务已停止，可复跑重建）
- 额度单位：高采样像素（width × height × samples²），不是物理内存上限
- 最终账目：capacity=400 reserved=0 available=400 overcommitted=0

## 曝光与承诺一览

| 键 | 曝光 ID | 请求 | 单位 | 最终状态 | 承诺去向 | 图版 |
| --- | --- | --- | --- | --- | --- | --- |
| alpha | exposure_6d57ed173bb246918ce7bf0271945ad9 | 512×512×2 | 1048576 | cancelled | 已释放·cancelled（S5） | 无（取消不产图版） |
| bravo | exposure_6d88888464724f89b44c86fb7a14bbe1 | 16×16×1 | 256 | succeeded | g1 已释放·failed（S7）；g2 已释放·succeeded（S15） | plate_2ee12becc9234d8382bf9ae2d1a47165 |
| charlie | exposure_81ae8d910f184222b0b0b83f4c57cf7f | 16×16×1 | 256 | succeeded | 已释放·succeeded（S15） | plate_3da788ecab9640458c3875ea9eb4693c |
| delta | （未创建） | 16×16×1 | 256 | — | 429 拒绝，无承诺记录（S13） | 无 |

## 逐步账目（每步结束时的真实 GET 结果）

| 步骤 | 动作 | capacity | reserved | available | overcommitted | alpha | bravo | charlie | delta |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S0 | 建快照并把画布额度设为 1,200,000 高采样像素 | 1200000 | 0 | 1200000 | 0 | — | — | — | — |
| | ↳ 默认策略 4,194,304 → 1,200,000；本班容量基线 | | | | | | | | |
| S1 | 接收 alpha：512×512×2 = 1,048,576 单位 | 1200000 | 1048576 | 151424 | 0 | queued·承诺1048576·g1 | — | — | — |
| | ↳ 接收与预留同一事务 | | | | | | | | |
| S2 | 接收 bravo：16×16×1 = 256 单位 | 1200000 | 1048832 | 151168 | 0 | queued·承诺1048576·g1 | queued·承诺256·g1 | — | — |
| S3 | alpha 渲染途中接收 charlie：256 单位 | 1200000 | 1049088 | 150912 | 0 | running·承诺1048576·g1 | queued·承诺256·g1 | queued·承诺256·g1 | — |
| | ↳ 渲染进行时不影响新接收；三块账目同时挂账 | | | | | | | | |
| S4 | 运行中取消 alpha：返回 200，但状态仍 running | 1200000 | 1049088 | 150912 | 0 | running·承诺1048576·g1 | queued·承诺256·g1 | queued·承诺256·g1 | — |
| | ↳ 关键：此时 reserved 不变，1,048,576 单位仍挂在 alpha 名下 | | | | | | | | |
| S5 | alpha 终态 cancelled：额度此刻才归还 | 1200000 | 512 | 1199488 | 0 | cancelled·已释放·cancelled | queued·承诺256·g1 | queued·承诺256·g1 | — |
| | ↳ 已提交分块保留可查，但进度不等于图版 | | | | | | | | |
| S6 | 同键重发 alpha：同一曝光、不重复预留；异参 409 | 1200000 | 512 | 1199488 | 0 | cancelled·已释放·cancelled | queued·承诺256·g1 | queued·承诺256·g1 | — |
| | ↳ 重发不是新承诺；账目不动 | | | | | | | | |
| S7 | bravo 失败终态：额度随终态归还 | 1200000 | 256 | 1199744 | 0 | cancelled·已释放·cancelled | failed·已释放·failed | queued·承诺256·g1 | — |
| | ↳ failed 是终态；256 单位归还，可用额度回升 | | | | | | | | |
| S8 | 缩容：capacity 1,200,000 → 400 | 400 | 256 | 144 | 0 | cancelled·已释放·cancelled | failed·已释放·failed | queued·承诺256·g1 | — |
| | ↳ charlie 的 256 单位仍在；available=144 | | | | | | | | |
| S9 | retry bravo 被 429 拒绝：available=144 < 256 | 400 | 256 | 144 | 0 | cancelled·已释放·cancelled | failed·已释放·failed | queued·承诺256·g1 | — |
| | ↳ failed 已归还；再执行是新承诺，须重新过闸门 | | | | | | | | |
| S10 | 值班长决策：capacity 400 → 512 以便重跑 bravo | 512 | 256 | 256 | 0 | cancelled·已释放·cancelled | failed·已释放·failed | queued·承诺256·g1 | — |
| S11 | retry bravo 成功：queued，承诺第 2 代 | 512 | 512 | 0 | 0 | cancelled·已释放·cancelled | queued·承诺256·g2 | queued·承诺256·g1 | — |
| | ↳ 同一曝光身份不变，尝试号与承诺代次增长 | | | | | | | | |
| S12 | 缩容：capacity 512 → 400，账面超额 112 | 400 | 512 | 0 | 112 | cancelled·已释放·cancelled | queued·承诺256·g2 | queued·承诺256·g1 | — |
| | ↳ 关键：bravo/charlie 承诺不消失，超额只是账面相减 | | | | | | | | |
| S13 | delta 被 429 拒绝；过期 revision 的提额被 409 拒绝 | 400 | 512 | 0 | 112 | cancelled·已释放·cancelled | queued·承诺256·g2 | queued·承诺256·g1 | — |
| | ↳ 拒绝不留痕迹：delta 没有曝光、没有预留；账目不变 | | | | | | | | |
| S14 | 服务重启：同一数据根上账目、承诺、进度逐字段一致 | 400 | 512 | 0 | 112 | cancelled·已释放·cancelled | queued·承诺256·g2 | queued·承诺256·g1 | — |
| | ↳ reconcile 全空：承诺去向可沿同一曝光核对，无需补记 | | | | | | | | |
| S15 | bravo、charlie 依次渲染成功，队列排空 | 400 | 0 | 400 | 0 | cancelled·已释放·cancelled | succeeded·已释放·succeeded | succeeded·已释放·succeeded | — |
| | ↳ available=0 时领取只看有效承诺；成功终态归还额度 | | | | | | | | |
| S16 | 图版与事件历史落盘，本班结束 | 400 | 0 | 400 | 0 | cancelled·已释放·cancelled | succeeded·已释放·succeeded | succeeded·已释放·succeeded | — |
| | ↳ reserved=0：所有承诺都有终态去向 | | | | | | | | |

## 关键证据（可在 transcript.jsonl 按 step 过滤复核）

- **S4→S5**：`POST /exposures/{alpha}/cancel` 返回 200 时 reserved 仍为 1049088，alpha 终态（cancelled）后才归还。接受取消 ≠ 立即腾出空间。
- **S8→S12**：缩容只改策略上限，bravo/charlie 的承诺不消失；S12 后 overcommitted=112 是暂时超额，不是已接收任务被撤销。
- **S9**：bravo 在 failed 后第一次 retry 被 429 拒绝——终态已归还额度，再执行是新的承诺，必须重新过闸门。
- **S13**：delta 被 429 拒绝后账目无任何变化：拒绝不留孤立预留。
- **S14**：服务重启前后 resources、各曝光状态与承诺逐字段一致，reconcile 返回全空（无需补记），事件历史完整。
- **S15**：available=0 且账面超额时，已承诺的 bravo/charlie 照常渲染成功；领取任务只看有效承诺，不看策略余量。

## 重启核对（S14）

```json
{
  "逐项一致": {
    "resources": true,
    "progress_alpha": true,
    "alpha": true,
    "bravo": true,
    "charlie": true
  },
  "reconcile": {
    "adopted": [],
    "released": [],
    "waiting": []
  }
}
```

## 最终图版

- `bravo` → 图版 `plate_2ee12becc9234d8382bf9ae2d1a47165`，校验 valid=True，通道文件：`plates/bravo-color.png` 等，交付包：`plates/bravo-bundle.zip`
- `charlie` → 图版 `plate_3da788ecab9640458c3875ea9eb4693c`，校验 valid=True，通道文件：`plates/charlie-color.png` 等，交付包：`plates/charlie-bundle.zip`
- `alpha`：无图版（已取消）。其已提交分块仍可由 `GET /exposures/{id}/progress` 核对，但进度不等于图版。

## 复跑

```sh
python3 drills/quota_handover/scenario.py --force
```

练习题目见 `WORKBOOK.md`，答案与依据见 `ANSWERS.md`。
