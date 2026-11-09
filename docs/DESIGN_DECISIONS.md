# 关键工程决策

每一条都按"问题 → 候选方案 → 选择 → 代价与证据"记录，包括走错又改回来的部分。参照实现固定为开源 Codex CLI `e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`；源码事实、合理推断与本地选择分开标注。没有用相同任务跑过原版对照，因此本文不给"行为一致率"。

## 1. 一个显式状态机，而不是框架节点拼装

**问题：** 需要解释每一次采样、意图提交与恢复决策，而不是展示一条固定流程。候选是直接用 LangGraph / OpenHands、写一个无限 `while` 工具循环，或自己维护显式状态机。

**选择：** 保留 `Engine.run` 与 `Store`，在 ready / model_pending / tool_queued / tool_pending 等阶段走明确分支；原生 8 调用批次串行；模型从结构化反馈中选择动作。复用标准库、SQLite 与系统工具，不复刻一个框架。

**代价：** 机制可追踪、依赖少，但 Engine 职责集中，没有并行调度与复杂依赖图。更多抽象层不会自动带来更好的 Agent 行为。[`engine.py`](../agent_lab/engine.py)

## 2. 多协议保留差异，不能只换 base URL

**问题：** Chat、Responses、Anthropic 三者在工具表示、结果回传、Thinking 与终止信号上都不同。只包一个"兼容 OpenAI"的客户端，会误判半响应并丢掉推理状态。

**选择：** `protocols.build_request / normalize_response / consume_sse` 分别实现 wire 细节，`OpenAIModel` 统一消息、usage 与错误。native continuation 存私有历史，公开投影去掉 native 块。

**代价：** 标准库 HTTP 控制更细，代价是必须自己维护边界与故障测试，也不会有 SDK 升级带来的默认收益。[`protocols.py`](../agent_lab/protocols.py)

## 3. 可以增量显示，必须完整结束后才能执行工具

**问题：** 流里出现 tool name 或"看起来能解析"的 JSON 片段，不代表模型已经提交了完整调用。候选是尽早执行以降延迟，或等完整响应。

**选择：** delta 只用于显示；三种协议的完成项、终止边界与参数校验全部通过后，才交给 Engine 的工具队列。HTTP 200 与 EOF 都不能替代完成标记。

**代价：** 放弃最早的投机执行，换取清晰的副作用边界。`--stream-text` 与 `--stream-tools` 都不是完成回执。[`protocols.py`](../agent_lab/protocols.py) · [`test_incremental_stream.py`](../tests/dev/test_incremental_stream.py)

## 4. Runner 锁与执行者生命周期必须分开

**问题：** V0 独立审计时实际发现：Runner 死亡后旧子进程仍可能晚到写入。只加 SQLite 锁或 `finally: kill` 处理不了 `SIGKILL`。

**选择：** `process_guard` 的执行 lease + generation、可信 worker/supervisor、owner pipe、native job 持久隔离。先确认旧执行者状态，再允许新 Runner 产生效果；未确认的 job 不靠 TTL 解锁。

**代价：** 宁可阻塞、要求人工诊断，也不让两个执行者并发改同一工作区。它不回滚已产生的效果，也不自动杀死未知外部进程。[`native_jobs.py`](../agent_lab/native_jobs.py) · [`process_guard.py`](../agent_lab/process_guard.py)

## 5. Checkpoint 保存成功与外部效果成功是两件事

**问题：** 工具已经改了文件，进程却在反馈持久化之前死亡；重新执行可能重复 append 或重复外部动作。候选是全部重试、全部停下，或按操作语义分类确认。

**选择：** 先持久化 planned/running 意图；文件冻结 before/desired SHA 与普通 mode，可对账确认；不确定的命令、append、MCP 进入 `needs_confirmation`。`Store.save` 先构造候选再提交，保存失败只重试同一事件，不重做工具。

**代价：** 可安全恢复的范围被主动收窄，未知不会被"自动变成功"。文件匹配不能推广为远端 exactly-once；普通 mode 也不等于 ACL / xattr。[`tools.py`](../agent_lab/tools.py) · [`store.py`](../agent_lab/store.py)

## 6. 摘要预算适配要按完整消息组，并保留原归档

**问题：** 初始完整历史超限时直接拒绝，明明存在可容纳的子前缀；摘要返回后旧工作视图可能仍然过大。简单截断会丢工具配对或原约束。

**选择：** `compaction.prepare` 选择最大完整可容纳的投影前缀；保留上一代笔记、原消息索引与 hash，安装严格推进或缩减的中间笔记；工作窗口与摘要输入窗口分开。次数、步骤与 deadline 共享原 Turn，不无限压缩。

**代价：** UTF-8 字节启发式实现简单、可离线复现，但不是 provider 的 token 计数。摘要可能漏信息；旧消息仍在归档里，模型可回查。[`compaction.py`](../agent_lab/compaction.py)

## 7. 摘要质量用后续任务证明，哈希只证明身份

**问题：** "存在 summary 对象"不证明模型真的看见，更不证明它据此继续改码。只写文档、只改测试、或没调用到实现模块，都可能伪造机制证据。

**选择：** 把安装事件的 source / cursor / note SHA 绑定到**实际请求**，再把成功工具的字节/AST 变化绑定到**外部执行到的实现模块**；功能正确性由冻结的累计 oracle 加原/新测试另判。

**代价：** 仍是模块级的有限证据，不是每个 AST 节点的执行证明。实例：第一个项目最终游标 168，但 call 33 实际用的是 156——只看最终 state 会得出错误结论。[`scripts/context_mission.py`](../scripts/context_mission.py)

## 8. 失败测试和能力不可用，不等于坏工具调用

**问题：** 模型需要看到失败测试才能继续修；可选 Git 在非 Git 工作区里"不可用"是正常现象。旧的错误计数在一批三次有效 Git 查询后提前耗尽预算，导致原 v4 完整成绩 0/2。

**选择：** 已知非零测试退出保留失败反馈供下一次采样；Git 先做参数与路径校验，再把"受信能力不可用"作为一个类型通过 worker 外层传输；宿主按固定工具名、码与只读阶段识别。坏 JSON、坏参数、越界仍计错误；外部 result 正文的伪分类无效；写后异常仍是 unknown。

**代价：** 没有降低成功标准，也没有提高上限；旧的 untyped 记录不补分类。首版用异常子类实现，跨进程丢了类别仍然失败，后来改成 wire 类型并补了路径负控。[`test_git_availability.py`](../tests/dev/test_git_availability.py)

## 9. 有界执行复核，而不是"测试绿了就算完"

**问题：** 部分任务里产物其实已过检查，模型仍在重复读取直到步骤耗尽；另一些是模型自己写错了测试。候选是强制 exit 0 结束、无限提醒，或提供有限的历史证据。

**选择：** 新 Coding Session 持久 marker；在确定测试之后，经过 8 次已知检查/测试决策才提醒，ready 状态最多 2 次，列出历史 exit、索引与剩余请求。潜在变更与新用户输入会让旧证据失效；同批 test → write 按实际次序处理。

**代价：** 不判定任务成功、不指定工具、不保证模型听从；旧的无 marker 会话不迁移。R3/R4 仍然失败，所以不宣称这套启发式稳定提升质量。[`test_execution_review.py`](../tests/dev/test_execution_review.py)

## 10. 宿主授权与扩展内容分层

**问题：** AGENTS、Skills、MCP 输出都可能是提示注入。加载脚本或批准命令，不应该顺带获得额外路径、网络或隐式软件权限。

**选择：** 项目规则是低优先级上下文；脚本捕获源码快照后走既有审批与沙箱；宿主 runtime policy、readonly 资源与二进制身份沿恢复绑定。MCP 是显式受信配置、逐次精确审批，独立于命令沙箱。

**代价：** 兼容性比无约束 shell 窄，MCP server 本身必须由操作者信任。readonly 模式不允许启用无法约束的 MCP；未知外部效果不重放。[`permissions.py`](../agent_lab/permissions.py)

## 11. 离线、实际工具、真实模型与冻结质量分层

**问题：** V0 的 56 个测试与一次 SIGKILL 并没有证明真实 Agent 质量；改测试或事后改口径最容易制造"成功"。

**选择：** 保存首次冻结与曝光记录，逐阶段跑原测试、全测试、累计外部观察与不可变文件检查；unknown 不计判定，后续修复不抹掉先前失败。早期的 v1/v2/v3/v4、失败候选与版本配置全部保留。

**代价：** 报告里会长期存在 0/2 和"未测"，而且新版本、新任务、新资源之间不构成简单提升率。v5 的 2×4 通过只是有限合成范围，没有统计独立性认证。[EVALUATION](EVALUATION.md) · [`evidence/`](../evidence/)

## 12. 证据字节不可改写，清理不能改变冻结身份

**问题：** 整理文档、去掉本机路径、补 license header 这些"看起来无害"的动作，可能改动被冻结的源或原始哈希，导致旧证据看上去在证明新内容。

**选择：** `agent_lab/`、`scripts/`、`tests/` 与 `evidence/`、`evaluation/` 的原始字节保持不变；需要解释的地方写新文档，不改旧记录。失败的候选、unknown 用量与旧报告哈希一律保留。

**代价：** 文档里会同时存在新旧口径，读者需要看清条目对应的版本。换来的是"每个数字都能追到当时那一份原始产物"。

这条规则的直接后果是：本仓库宁可让历史记录里出现 `/Users/...` 这类本机路径，也不去重写那些 JSON——重写会让哈希失效，而哈希失效的证据就不再是证据。
