# Facet Proof

Facet Proof 是纯 Python 标准库实现的技术插图渲染后端：接收三角网格、材质及场景布置，冻结资源快照，渲染颜色、深度和物体编号图版，支持图版比对、校验、审阅和归档。使用 CPU 光栅化，不需要 GPU。

需要 macOS 或 Linux，以及 Python 3.11 或更高版本。启动：

```sh
python3 -m facetproof --root ./facet-data --host 127.0.0.1 --port 8796
```

默认绑定本机；服务没有账户或认证机制，只适合受信任的本地环境。持久化根目录包含 SQLite 数据库和内容寻址的图版文件。`POST /facet/v1/worker/step` 同步领取并执行一个待处理任务；`POST /facet/v1/worker/recover` 回收过期租约。进程启动时也会回收已过期租约。

可运行完整生产演示，输出三张 PNG：

```sh
python3 -m facetproof.demo --flow render-plates --output ./preview
```

演示使用临时数据库，不影响服务数据。其他流程名见 `facetproof/demo.py` 的 `FLOWS`。输入按顺序为：创建 `/facet/v1/meshes` 和 `/pigments`，创建 `/assemblies`，以版本号更新布置，再调用 `/assemblies/{id}/seal`；对 `/seals/{id}/exposures` 提交带唯一 `key` 的请求，然后执行 worker。图版的 `/passes/color.png`、`depth.png`、`object.png` 可供查看；`/pick?x=...&y=...` 返回物体及摄像机前向距离。`/verify` 校验文件，`/bundle` 导出包含快照及全部通道的 ZIP。

坐标使用右手空间，镜头支持透视和正交投影，旋转以度为单位；颜色输入为 0–1 sRGB，内部以线性光计算。每个图版包含 RGB、32 位浮点深度、32 位无符号物体编号和对应 PNG；背景深度为正无穷。图版保留颜色与深度的输入快照和摘要。服务有图像尺寸、采样、面数和渲染时间上限。

曝光现在按高采样画布的 64×64 块保存计算进度。`GET /facet/v1/exposures/{id}/progress` 返回已经提交的块和像素数。块完成不等于图版成功：只有所有块完整、内容可读、租约仍有效且未取消，才会生成最终图版。进程中断后，先恢复过期租约，再执行工作器即可复用同一冻结输入的已提交块；显式重试失败曝光也复用完整块。渲染仍有每次尝试 45 秒预算，服务不会自动启动后台工作器。

`GET /facet/v1/resources` 查询画布额度，`PUT` 同一路径以 `capacity` 和 `revision` 修改策略。额度单位为高采样像素，即 `width × height × samples²`，不是进程物理内存上限。接收新曝光与预留额度属于同一事务；同键重发不重复预留。运行中的取消须等工作器确认终态才归还额度，失租接管继续持有原曝光的额度。缩小策略不撤销已经接受的承诺，响应会显示超出新策略的部分。首次打开旧目录时会补记已有排队及运行任务的承诺。`POST /facet/v1/resources/reconcile` 核对持久状态，无法补足的条目保留等待事实，不伪造成功或取消。

交付接收者可以脱离服务核验 ZIP：

```sh
python3 -m facetreader ./plate-bundle.zip --output ./received-plate
```

独立读取器不导入渲染服务代码，自行检查 ZIP、PNG、原始通道和冻结来源；有效时退出 0，拒绝时退出 2。它限制输入和解压体积，只接受当前两份元数据及六个通道。核验通过表示包内事实一致，不能证明外部来源真实，也不能证明服务中当前审阅意见仍有效。输出目录提供颜色图、来源报告和拾取样例。

完整在线备份分为固定快照和续做复制两步：`POST /facet/v1/maintenance/backups` 返回操作标识；`POST /facet/v1/maintenance/backups/{id}/resume` 复制并核对该快照引用的最终通道及在途分块。可以在中断后对同一操作续做，不会重新抓取另一个 SQL 时点。`GET /facet/v1/maintenance/backups/{id}` 查询状态，末尾追加 `/verify` 独立复核完成后的备份。缺失或损坏的来源内容会明确失败。备份位于数据根的 `backups/{id}/data`；恢复演练应先复制这份目录到新的数据根，再启动服务，避免改写备份本身。SQL 中的维护控制行属于备份时的历史记录，不能在另一数据根继续原机器的操作。

`GET /facet/v1/maintenance/inspect` 提供固定 SQL 观察边界下的通道引用、共享关系及字节核验。无引用内容与可能仍在写入的内容须区分，巡检不提供删除操作。图版逻辑清退继续保留内容；本服务没有物理垃圾回收功能。

当前基线不包含测试套件，自动化测试有意延期；`facetproof.demo` 提供有界的业务流程检查，不应当作完整的测试覆盖。

## 值班交接演练

`drills/quota_handover/` 提供一套可复跑的额度交接演练：用真实 HTTP 请求安排额度缩小、运行中取消与失败后再执行交错的局面，逐步留痕请求、额度账目与最终图版，并含服务重启后的承诺核对：

```sh
python3 drills/quota_handover/scenario.py --force
```

产物（交班账目 `REPORT.md`、请求留痕、事件历史、图版与交付包）写入 `drills/quota_handover/artifacts/`；操作者练习见该目录的 `WORKBOOK.md`，答案依据见 `ANSWERS.md`。
