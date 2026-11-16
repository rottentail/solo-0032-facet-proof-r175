# 接班操作者推演练习

你是接班者。上一班留下了一次额度演练的完整留痕（`artifacts/`），服务现已停止。
除特别说明外，**只允许**依据 `artifacts/` 里的真实请求与账目作答——这正是交接时
你手里有的东西。每题标注了建议查看的素材；答案与依据在 `ANSWERS.md`，
请先独立作答再核对。

背景参数：额度单位是高采样像素（width × height × samples²）；本班容量从
1,200,000 一路缩到 400；四个曝光键 alpha/bravo/charlie/delta 的角色见
`README.md` 的局面表。

## 第一部分：读账推演

**Q1（承诺盘点）** 翻到 `steps.json` 里 S12 结束时的账目：capacity=400、
reserved=512、overcommitted=112。请列出此刻仍然有效的画布工作量承诺
（曝光键、单位、承诺代次），并回答：overcommitted=112 是否意味着有已接收的
任务被撤销了？如果不是，它表示什么？

**Q2（取消不等于腾出）** 在 `transcript.jsonl` 的 S4 里找到
`POST /exposures/{alpha}/cancel` 及其后的 `GET /resources`。取消请求返回了
什么状态？此刻 alpha 的 1,048,576 单位腾出来了吗？如果交班日志只写了一句
「alpha 已取消」，你接班后能否把这部分额度当作可用？为什么？

**Q3（终态才销账）** alpha 的额度具体在哪一步、哪个事件之后才归还？
在 `events/exposure-alpha.json` 里指出 `cancel_requested`、`cancelled`、
`capacity_released` 的先后顺序，并说明为什么 `capacity_released` 不可能
出现在 `cancelled` 之前。

**Q4（同键重发）** S6 里有人用 key=alpha 重发了**相同**请求，又用**不同尺寸**
重发了一次。两次各自得到什么结果？第一次是否构成新的额度承诺？如果交接时
你在监控里看到「同一 key 反复提交」，应如何判断它有没有重复占额度？

**Q5（失败与再执行）** bravo 在 S7 进入 failed。S9 的第一次 retry 为什么被
429 拒绝——bravo 不是早就被系统接受过了吗？failed 状态的曝光占不占额度？
S11 的 retry 成功后，bravo 的承诺代次（generation）变成几？这说明了什么？

**Q6（拒绝不留痕迹）** delta 在 S13 被 429 拒绝。这次拒绝在额度账目和曝光
列表里留下了什么？如果交接时你只看到一条 429 日志，能否断定 delta
「曾经排队、后来被清掉了」？正确的表述应该是什么？

**Q7（重启后核对）** S14 服务重启。接班后你要核对「承诺的去向」，应该发哪些
请求、期望看到什么？`restart-check.json` 里哪些字段证明了持久承诺没有漂移？
如果 `POST /resources/reconcile` 返回的 `waiting` 非空，意味着什么、
你该怎么处理？

**Q8（图版与进度）** 本班最终有几张图版，分别来自哪个曝光的哪一代承诺？
alpha 已经提交了 43 个高采样块（见 `events/exposure-alpha.json`），
这些分块现在还能查到吗？它们算不算「一张没画完的图版」？为什么进度查询
不能当作图版成功的事实？

## 第二部分：动手任务

**T1（复跑自核对）** 从仓库根目录执行
`python3 drills/quota_handover/scenario.py --force`，确认退出码 0。
注意产物里的曝光 ID 会变化（每次运行是新身份），但 S0–S16 的账目数字必须
与本练习引用的完全一致。哪一步的哪个数字是你核对的第一锚点？

**T2（重启核对演练）** 复跑后服务已停止，但数据根 `./facet-drill-data` 还在。
自己启动服务（`python3 -m facetproof --root ./facet-drill-data --port 8799`），
用 `GET /facet/v1/resources`、`GET /facet/v1/exposures/{id}`、
`POST /facet/v1/resources/reconcile` 沿 alpha/bravo/charlie 各曝光核对承诺去向，
写下你看到的 state 与 allocation。然后停止服务。

**T3（离线核验交付包）** 执行
`python3 -m facetreader drills/quota_handover/artifacts/plates/bravo-bundle.zip --output /tmp/received-bravo`，
确认退出码 0。阅读输出里的 `trust` 字段：这次核验**没有**证明什么？

**T4（情景处置）** 你接班后收到工单：「delta 很重要，请立刻让它跑起来」。
当前账目 capacity=400、reserved=512、available=0。请写出你的处置步骤
（要发哪些请求、按什么顺序、每步预期什么），并指出至少两条**错误**做法。

## 交班模板（练习后填写）

接班确认时，请能默写出本班每个曝光的「承诺去向」一句话，例如：

- alpha：g1 承诺 1,048,576 → 运行中取消 → 终态 cancelled → 已归还（事件
  capacity_released/cancelled），无图版；
- bravo：g1 承诺 256 → failed 归还；g2 承诺 256 → succeeded 归还 → 图版
  plate_…；
- charlie：g1 承诺 256 → succeeded 归还 → 图版 plate_…；
- delta：从未成为承诺（429），无账目痕迹。
