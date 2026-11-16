# 推演练习答案与依据

每题给出结论、判断路径和可复核的出处。出处分三类：`artifacts/` 内的留痕
（transcript 步骤、steps.json、events/、restart-check.json）、仓库代码位置、
README 段落。复跑后曝光 ID 会变，但步骤号、账目数字与事件序列不变。

## Q1 承诺盘点（S12 账目）

**答案**：仍有效的承诺有两笔——bravo 256 单位（第 2 代，g2）与 charlie
256 单位（第 1 代，g1），合计 512。overcommitted=112 **不**意味着任何已接收
任务被撤销；它只是 `reserved − capacity = 512 − 400` 的账面相减，表示当前承诺
总量暂时超出新策略上限。缩容只约束**新的接收**，不追溯撤销旧承诺。

**依据**：
- `steps.json` S12：bravo=`queued·承诺256·g2`、charlie=`queued·承诺256·g1`，
  两者 allocation.active=1。
- `facetproof/resources.py:39-48`：`revise` 只更新策略行并记事件，不触碰
  `sample_allocations`；`facetproof/resources.py:28-33`：overcommitted 的定义。
- 反证：若任务被撤销，S15 的 `worker/step` 不会成功渲染 bravo/charlie
  （transcript S15 两次 succeeded）。

## Q2 取消不等于腾出（S4）

**答案**：取消返回 200，但响应里 `state` 仍是 `running`、
`cancel_requested=1`；紧随的 `GET /resources` 显示 `reserved=1049088` 不变——
1,048,576 单位**没有**腾出。接班后看到「alpha 已取消」的日志，**不能**把这部分
额度当可用：取消被接受只是登记了停止请求，工作器还没确认终态。

**依据**：
- transcript S4：POST cancel → 200（state=running, cancel_requested=1）；
  同步骤 GET resources → reserved=1049088；GET exposure → allocation.active=1。
- `facetproof/exposures.py:70-79`：对 running 曝光，`cancel` 只置
  `cancel_requested` 并保持 running，不调用 release（只有 queued 才立即释放）。
- `facetproof/resources.py:82-92`：`release` 先校验曝光已进入
  succeeded/failed/cancelled 之一，否则 409——制度上保证「接受取消 ≠ 腾出」。

## Q3 终态才销账（S5，事件顺序）

**答案**：在 S5。事件顺序是 `cancel_requested`（S4 登记）→ 若干
`tile_committed`（工作器在心跳间隙继续提交了已开始的块）→ `cancelled`
（工作器心跳发现取消标记，进入终态）→ `capacity_released{reason:cancelled}`。
`capacity_released` 不可能在 `cancelled` 之前，因为释放与终态落定在同一个
SQLite 事务里，且 release 本身以终态为前提。

**依据**：
- `events/exposure-alpha.json` 的事件序列（共 43 个 tile_committed 后才是
  cancel_requested → cancelled → capacity_released）。
- `facetproof/worker.py:21-26`：checkpoint 心跳；`facetproof/worker.py:48-50`：
  心跳抛出 cancelled 后走 `exposures.fail`；`facetproof/exposures.py:91-101`：
  `fail` 把状态置为 cancelled 并在同一事务里 release。
- 账目佐证：`steps.json` S4→S5 reserved 从 1,049,088 降到 512。

## Q4 同键重发（S6）

**答案**：相同请求重发返回 200 与**同一个**曝光 ID（状态 cancelled）——这是
幂等命中，不是新承诺，账目不动；不同尺寸重发返回 409 `key_conflict`。
判断「同一 key 反复提交」有没有重复占额度：看 `GET /resources` 的 reserved
是否变化、看返回的曝光 ID 是否与首次相同。同键同参永远命中首次承诺。

**依据**：
- transcript S6：两次 POST 的响应（200 同 ID / 409 key_conflict）及
  前后不变的 reserved=512。
- `facetproof/exposures.py:27-43`：dedup_key 命中且 request_hash 相同则直接
  返回原曝光；不同则 409。
- `facetproof/resources.py:70-80`：对同一曝光的重复 reserve 直接返回旧
  allocation，不重复扣减。

## Q5 失败与再执行（S7–S11）

**答案**：failed 是终态，bravo 的 256 单位在 S7 已随终态归还
（`capacity_released{reason:failed}`，reserved 从 512 降到 256）。所以 S9 的
retry 是一次**新的承诺**，必须重新过闸门：当时 capacity=400、available=144
< 256，被 429 `capacity_reserved` 拒绝。「曾经被接受过」不保留任何额度优先权。
S10 值班长提额到 512 后，S11 retry 成功，承诺代次变为 **g2**——同一曝光身份
（ID、dedup key）不变，但额度承诺是新的一代，事件流里出现第二条
`capacity_reserved{generation:2}`。

**依据**：
- transcript S7/S9/S10/S11 与 `steps.json` 对应行的 reserved 变化。
- `events/exposure-bravo.json`：failed → capacity_released →
  capacity_reserved(g2) → retried → claimed → succeeded → capacity_released。
- `facetproof/exposures.py:81-89`：retry 调 `reserve` 重新预留；
  `facetproof/resources.py:70-80`：reserve 的 available 闸门与代次递增。

## Q6 拒绝不留痕迹（S13）

**答案**：什么都没留下。delta 没有曝光 ID、没有预留记录，reserved 保持 512
不变。看到一条 429 日志，**不能**说 delta「曾经排队、后来被清掉」——它从未
成为承诺。正确表述：「delta 的接收请求在 S13 被容量闸门拒绝，未产生承诺；
如需执行须重新提交并通过当时的闸门」。

**依据**：
- transcript S13：POST exposures → 429 capacity_reserved；随后 GET resources
  reserved 不变；`steps.json` 中 delta 一栏始终为「—」。
- `facetproof/exposures.py:27-43`：reserve 与 INSERT 在同一事务，reserve
  抛错即整体回滚，不留孤立行。
- 同步骤的过期提额（revision=4）被 409 `stale_revision` 拒绝，同样不改账目
  （`facetproof/resources.py:39-48` 的乐观修订）。

## Q7 重启后核对（S14）

**答案**：核对顺序——`GET /facet/v1/resources`（策略与挂账总量）→ 逐曝光
`GET /facet/v1/exposures/{id}`（state 与 allocation：active/units/generation/
release_reason）→ `GET /facet/v1/exposures/{id}/progress`（在途分块）→
`POST /facet/v1/resources/reconcile`（应返回全空）。`restart-check.json` 里
`identical` 全为 true：resources、alpha/bravo/charlie 的完整曝光视图、alpha
的分块进度在重启前后逐字段一致；`recover` 返回空、`reconcile` 三组皆空。
若 `waiting` 非空，意味着存在**已接受但无法补记承诺**的曝光（例如旧目录在
额度机制之前遗留、且当前余量不足）——系统不会伪造成功或取消来掩盖它，
你应把它列入交班风险，等有余量时再 reconcile 或人工处置。

**依据**：
- `restart-check.json` 全量对比；transcript S14 的 recover/reconcile 响应。
- `facetproof/server.py:57-59`：启动时回收过期租约；
  `facetproof/resources.py:94-117`：reconcile 只补记真实状态，无法补足的
  保留在 waiting（代码注释明示不伪造）。

## Q8 图版与进度（S15–S16）

**答案**：两张图版——bravo（g2 承诺，plate 见 REPORT.md）与 charlie（g1
承诺）。alpha 的 43 个已提交分块仍可查：`GET /exposures/{alpha}/progress`
返回 committed_tiles=43、complete=false。但它们**不是**「一张没画完的图版」——
图版只以整幅画布为边界提交，alpha 没有 plate_id，也没有任何可读图版通道；
进度查询只是持久化的中间事实，不能当作图版成功。

**依据**：
- `steps.json` S16 与 REPORT.md 图版清单；`events/exposure-alpha.json` 无
  succeeded 事件。
- `facetproof/tile_store.py:150-171`：progress 的文档字符串「committed tiles
  are not plate success」；README「块完成不等于图版成功」一段。
- `facetproof/plates.py:26-40`：图版提交与 succeeded、额度归还在同一事务。

## T1–T4 要点

**T1**：第一锚点建议用 S4 的 `reserved=1049088`（取消被接受但额度未动）——
它同时钉住了容量基线、三笔挂账和「接受取消不腾出」三件事。退出码非 0 时
transcript 末尾的 marker 会指出中断步骤。

**T2**：预期看到——alpha：state=cancelled、allocation.active=0、
release_reason=cancelled；bravo：state=succeeded、active=0、
release_reason=succeeded、generation=2；charlie：state=succeeded、
release_reason=succeeded；resources：capacity=400、reserved=0、
overcommitted=0；reconcile 三组全空。任何一项不符都说明数据根被换过或
演练未完整复跑。

**T3**：`trust` 字段明示：包内字节一致 ≠ 外部来源真实，也不证明当前审阅
仍有效、不证明渲染正确（通道只被解码交叉核对，没有独立重渲）。

**T4**：正确顺序——
1. `GET /resources` 确认 available=0，确认 delta 从未成为承诺（无曝光记录）；
2. 二选一：(a) 等 bravo/charlie 终态释放后重提 delta；(b) 由值班长
   `PUT /resources` 提额（必须带**当前** revision，S13 已演示过期 revision
   被 409）；
3. 重新 `POST /seals/{id}/exposures`（key=delta 可安全重用：S13 的 429 没有
   留下任何记录，不会 key_conflict）；
4. `POST /worker/step` 执行，`GET /exposures/{id}` 跟进到终态再销账。

错误做法示例：
- 以为「取消 bravo/charlie 就能立刻腾出 512」——运行中取消在终态前不释放
  （Q2/Q3）；且 queued 取消虽立即释放，但那是撤销别人的已接受承诺，须值班长
  决策而非默认动作；
- 以为 retry 一个 failed 曝光不占新额度（Q5）；
- 用旧 revision 反复 PUT 提额（S13 的 409）；
- 看 `available=0` 就断定「队列里什么都没有」——reserved=512 说明两笔承诺
  仍在，超额不代表任务消失（Q1）。
