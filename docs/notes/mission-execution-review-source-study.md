# 执行复核提示：固定源码窄研究

日期：2026-11-09；固定 ref：`e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`。
结论：读到 token/会话预算提醒与 Stop hook continuation 实际记入模型对话的机制；
**没有找到“本Turn剩余模型步数＋最新确定测试退出码、最多两次复核”的直接对应。**
这是三个真实源码文件的有限结论，不是全仓不存在该机制的证明。没有读取任何验收题、
私有记录或凭据，没有付费模型调用、runtime改动或上游执行。

证据清单 记录固定 blob、
实际已读行、有限摘录、许可及导出哈希。路径来自此前已确认的固定完整Git树。

| 文件（前缀 `codex-rs/core/src/`） | 固定 Git blob |
|---|---|
| `session/token_budget.rs` | `6e77a57a3646464d646fa4d05343a2a0905b4f2e` |
| `session/rollout_budget.rs` | `bc05663dc1628210533653ab106b59eab55fff94` |
| `session/turn.rs`（复用并重读） | `1fdf62faca3d396b4f1b6b7c64d879f99be7b2e9` |

**事实：上下文预算提醒。**
[token_budget.rs L159–196](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/token_budget.rs#L159) 仅在 TokenBudget feature、
窗口剩余token数及配置存在时工作。低于配置阈值后，调用
`state.claim_token_budget_reminder()`；若允许，使用配置模板与剩余token数构造
`TokenBudgetReminder`，通过 `ContextualUserFragment::into` 转为输入项并
`record_conversation_items`。L199–222 在允许fallback、余额为0、有fallback提示且
状态claim成功时，另记录 `AutoCompactFallbackPrompt`。
[L750–758](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L750) 确认实际Turn循环调用此函数。
这里计量token而非模型调用次数；claim函数体及fragment文字renderer未扩源读取，
不推断其精确重置周期、次数上限或最终措辞。

**事实：会话预算提醒及硬停止分离。**
[rollout_budget.rs L11–39](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/rollout_budget.rs#L11) 在 Controller runtime
向 agent_control 请求当前thread/window的 pending reminder，把其中
`remaining_tokens` 写入 `RolloutBudgetContext` 对话项，然后标记已送达。
Host runtime明确跳过这条提醒路径。L41–75 对usage accounting另作处理；
Host上报返回 Exhausted 时产生 `SessionBudgetExceeded`，Continue则放行。
提醒内容与预算是否终止不是同一个动作；pending/mark helper内部预算政策未追踪。

**事实：确实存在送往下一次采样的路径。**
[turn.rs L568–576](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L568) 在准备step前记录会话预算提醒；
[L637–655](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L637) 随后clone_history并交给
`run_sampling_request`。此前研究已追踪该函数将历史投影组成Prompt并送stream。
因此这不是仅写UI/日志的通知。上下文预算提醒写入对话后也进入后续历史；若随后
Turn结束且没有下一次采样，不能声称模型已经消费这条新提醒。

**事实：Stop hook能提供继续反馈。**
[turn.rs L805–854](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L805) 在 `!needs_follow_up` 时调用
`run_turn_stop_hooks`。若 should_block 且
`build_hook_prompt_message(continuation_fragments,...)` 产生消息，则记录该输入、
接受mailbox输入、设 `stop_hook_active=true` 并continue；没有消息则发警告，
should_stop分支可break。MemoryConsolidation来源还有特殊失败分支。
这里读到的是消息真正回到循环的边界；hook执行器、fragment角色/序列化和反馈
策略正文没有追踪，不能从名字推断完整审查行为或质量判断。

**差异与推断。** 预算提醒提供约束信息；Stop hook在模型本来准备停止时允许
带反馈继续。两者都不证明模型必然改写代码或正确结束，也不以外部功能检查通过
为事实来源。本地拟议复核是在仍可继续的ready边界，由宿主最多两次给出本Turn
剩余模型步数及最新确定测试退出码，让模型选择下一步；该触发时机、计量单位、
test-exit字段和次数上限均为**独立本地设计**，不能称为固定Codex复现。

**本地选择边界，未实施。** 若主线程采用此设计，提示只能陈述可核验状态：
测试非零不等于调用异常，exit0也不等于验收通过；提示不更改评分、权限、错误
结果或max_steps，不脚本化工具顺序。以这种简短复核能否改善持续Coding必须由新
实测验证，本报告不为效果背书，也不推断既有失败的原因。

OpenAI Codex，Copyright 2025 OpenAI，Apache License 2.0。固定
[LICENSE](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/LICENSE) blob
`4606e72e042564097e8780d66c1d4dcb611869bd`；
[NOTICE](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/NOTICE) blob
`2805899d56d0332d175cfc613c67d45d6f006db7`。原文随摘录保存；包装/报告为本地新增。
