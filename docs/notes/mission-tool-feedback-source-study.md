# 工具反馈：正常非零退出与调用异常

日期：2026-11-09。固定 Codex ref：
`e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`。本次只新增读取三个实际源码体；
循环边界引用[上一份已完成研究](mission-execution-guidance-source-study.md)，不扩展
其他子系统。未读取封存题或私有记录，未调用付费模型，未运行上游行为或修改本地
runtime。任务背景由主线程提供，不作为本研究独立验证的实测结论。

证据目录及清单 保存路径、
blob、已读范围、有限原文摘录、许可和导出哈希。路径由先前取得的固定完整 Git 树
确认，再取实际文件；不是根据名称猜测。

| 本轮源码 | 固定 Git blob |
|---|---|
| `tools/handlers/unified_exec/exec_command.rs` | `2b746b85d13e06b3a52f6f13a20d02ffc9ab373b` |
| `tools/orchestrator.rs` | `9ba96e57eea42bcd988b1d4d257d92149a50cc3a` |
| `unified_exec/process_manager.rs` | `caa45622a2c826ae7a7c14c1edd2c849e8f81799` |

路径前缀统一为 `codex-rs/core/src/`。本研究讨论 generic exec_command；
没有发现/假设一个专门识别测试用例是否应通过的上游工具。

## 事实：正常进程结果有自己的退出码

[process_manager.rs L518–534](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L518) 的
`exec_command` 返回 `Result<ExecCommandToolOutput, UnifiedExecError>`。
追到内部 [L737–874](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L737)：
完成进程的退出码从 process/status 取得；通过网络审批收尾、failure-message 和沙箱
拒绝检查后，将 `raw_output`、`exit_code`、耗时、chunk/process身份等装入
`ExecCommandToolOutput`，最后 `Ok(response)`。该响应构造处没有
`exit_code != 0 => Err` 的分支。活跃进程则可返回 process id 供后续收集。

这证明：在检查未判定为执行/策略异常的普通完成路径，非零退出码仍是结构化进程
结果；它并不被这层 Rust `Result::Ok` 解释为程序业务/测试通过。
[L711–735](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L711) 的 failure-message 路径
明确产生 `UnifiedExecError::process_failed`；
L768–775、L845–850 的沙箱拒绝检查也可以返回错误。该检查函数体未扩展读取，
因此本研究不概括哪些具体 exit/status/output 会被分类为沙箱拒绝。

## 事实：handler区分结果和异常，但两者都可能反馈模型

[exec_command.rs L149–167](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs#L149)
对不支持的 payload 返回 `FunctionCallError::RespondToModel`；参数解析/准备通过
`?` 传播其错误，未把参数错误伪造为一个进程退出。

[L232–275](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs#L232) 调用 process
manager：`Ok(response)` 直接包装为工具输出，不因退出码非零改走异常分支。
`SandboxDenied` 另保留聚合输出和原始退出码，以 terminal
`ExecCommandToolOutput` 返回，且无可恢复 process id。其他
`UnifiedExecError` 则生成有长度界限的失败信息，以
`FunctionCallError::RespondToModel` 返回。

因此“返回工具输出对象”也不等于允许执行、命令成功或测试通过：被拒绝的输出
仍可作为模型可读反馈。不能只看外层 Ok 或 success 字段推断质量状态。
ToolOutput到具体模型协议内容的 formatter/registry转换不在三文件范围内，
本研究不声称其确切文本格式、success字段值或每类异常是否终止整Turn。

## 事实：编排重试按异常类别及审批决定

[process_manager.rs L1517–1558](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L1517)
在打开带沙箱的会话时实际创建 `ToolOrchestrator` 和 runtime，调用
`orchestrator.run`；沙箱拒绝映射保留输出，其他打开会话失败另分类。

[orchestrator.rs L105–122](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/orchestrator.rs#L105) 执行 runtime，
正常返回值与 `ToolError` 分离；网络审批收尾还可能改变错误结果。
[L389–449](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/orchestrator.rs#L389) 对 `Ok(out)` 返回结果；对
`ToolError::Codex`，只有匹配 `SandboxErr::Denied` 才继续考虑后续审批重试，
其他错误直接返回。拒绝后是否升级还受 `escalate_on_failure`、审批策略和权限
检查约束，并非“任何非零退出就自动重跑”。
[L598–643](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/orchestrator.rs#L598) 只执行一次明确的第二次 attempt，
该次结果/错误直接返回；L649–658 单独区分拒绝、超时、信号的遥测分类。
这不是本地工具异常次数上限的上游对应物。

## 既有循环证据与未覆盖边界

上一份固定 [turn.rs L660–690、L805–855](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L660)
证据表明，采样输出的 follow-up 与 pending input 决定是否继续；
无后续需求才进入 Stop hook 路径。L3331–3337 累积工具 future 和输出处理返回的
follow-up。本轮没有重读其完整调用错误转换链，不据此断言所有工具错误都会继续、
都会终止，或 upstream 没有其他错误/预算限制。

本轮三个源码体没有建立“普通非零退出会增加一个全局连续调用错误 counter”这一
机制，也没有覆盖所有工具、registry、失败重试策略、一次性执行实现、超时/取消
及模型协议序列化。故结论仅限：**命令退出结果、调用异常、策略异常和任务质量
是不同维度；不能从一个 success/Ok 值推出统一 counter 行为。**

## 本地比较与独立实现选择

**本地事实。** 只读 `agent_lab/engine.py`：
`_feedback` L634–662 先写工具反馈，再在 L650 用 `result['ok']` 重置/递增
`state.errors`；随后才识别 int退出码并生成 `process_exit_nonzero` 元数据。
所以虽然遥测已经区分普通退出，预算更新仍合并了这些结果。
`_tool` L813–820 对只读 ToolError 生成 `ok:false/error`；可写工具异常保留
未知效果暂停。运行循环 L1328–1329 按累计 errors 触发 error_limit。

**独立本地选择，未实施。** 在可信工具完成路径区分“调用产生一个确定进程结果”
与“参数/执行调用失败”。正常 exit1 保留 `ok:false`、退出码、测试输出及质量
失败，不消耗 invocation-error 预算；真正参数/调用失败仍计入原预算。不要仅按
模型/工具任意内容中出现一个 exit_code 字段就授予此分类。正常完成是否重置已有
连续错误由本地预算语义明确决定并测试，不能冒称上游规定。

max_steps、deadline等有限执行边界继续生效；失败测试不被标为成功，受保护验证
标准不变，未知效果暂停也不变。以测试的失败反馈继续诊断/修改/验证是一种通用
Coding能力，不应依题目、工具顺序或验收答案选择行为。本研究不提供或读取题意。

## 许可及验证界限

OpenAI Codex，Copyright 2025 OpenAI，Apache License 2.0。固定
[LICENSE](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/LICENSE) blob
`4606e72e042564097e8780d66c1d4dcb611869bd`；
[NOTICE](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/NOTICE) blob
`2805899d56d0332d175cfc613c67d45d6f006db7`。完整许可和NOTICE随本次有限
公开摘录保存。研究包装/报告为本地新增；没有移植上游实现，未执行上游测试或
声称本地错误预算修复已通过验收。
