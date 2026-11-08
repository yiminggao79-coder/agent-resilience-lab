# 有限取消协议：Codex 源码补证与本地第一步

固定 [`openai/codex@e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`](https://github.com/openai/codex/tree/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9)，2026-11-07 经 GitHub 连接器取得本文6个直接来源。只补既有参考矩阵、steering 与 managed-command 资料中的取消边界；未执行上游/测试，不读私有checkpoint、凭据、账本或V2，不调用模型API，不改runtime。

本地读取分支 `codex-reconstruction`、HEAD `e65e6c6a913394796be67c2733240fc8d0bc8777`；diagnostics等未提交改动已保留。

**结论**：上游取消包含目标检查、token、有限宽限、task abort+join、终结事件与flush；这些步骤不能合并成“请求一接受，OS进程和外部副作用都已取消”。本地先做同Turn持久stop、在安全边界拒绝后续工作，是合理的较小独立实现；当前调用尚不能主动中断时，应明确等待其完成/现有timeout。恢复时有running未知结果，stop必须优先阻止补做副作用，保留未知并暂停。

## 四个关键函数体：实际能证明什么

1. **外部请求：`turn_interrupt_inner`。** [turn_processor.rs1477–1527](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/src/request_processors/turn_processor.rs#L1477-L1527) 校验请求turn与app-server所知active turn，不符/已终结返回错误；普通turn先将request_id加入pending_interrupts，再提交Op::Interrupt，成功提交返回None，留待TurnAborted路径回复。startup空turn-id例外在提交后立即回复。本文未追最终JSON-RPC事件监听器，事实限于该函数实际入队/返回行为。
2. **核心停止：`handle_task_abort`。** handlers匹配Op::Interrupt→interrupt→Session.interrupt_task→abort_all_tasks；后者取active turn、交给handle_task_abort，并在取消处理后清pending审批，避免把cancel误呈现成模型可见reject。[handlers.rs545–556](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/handlers.rs#L545-L556)、[L61–66](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/handlers.rs#L61-L66)、[session/mod.rs5938–5944](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L5938-L5944)、[tasks/mod.rs630–656](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod.rs#L630-L656)。handle_task_abort [L1047–1114](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod.rs#L1047-L1114) 先cancel token，可选interrupt code cells，等待done或100ms宽限，随后abort并await task handle，再执行task-specific abort。宽限常量在 [L75](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod.rs#L75)。拿走active turn与取消token并非OS进程回收确认。
3. **模型边界：`try_run_sampling_request`。** stream request/receive通过or_cancel受turn token约束；取消后普通stream事件不能继续触发工具，返回TurnAborted。provider Cancel还要求InterruptOnCancel feature、ResponsesLite与可用interrupt handle。[turn.rs3009–3033](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L3009-L3033)、[3091–3135](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L3091-L3135)、[3757–3763](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L3757-L3763)。函数还drain已在途工具结果、记录已取得usage后再返回取消：[L3789–3811](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L3789-L3811)。没有证据说provider cancel消息会回滚远端推理或费用。
4. **终结仍在`handle_task_abort`。** task-specific abort后记录interrupted marker，尝试flush；构造带turn/root id/reason的TurnAborted，发送事件后再次flush。[tasks/mod.rs1116–1134](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod.rs#L1116-L1134)、[1162–1178](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod.rs#L1162-L1178)。flush失败是warning路径，不能由收到TurnAborted倒推存储一定成功。CleanBackgroundTerminals是另一个Op，调用close_unified_exec_processes；普通interrupt不能等同“终止所有背景终端”。[handlers.rs65–66](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/handlers.rs#L65-L66)、[tasks/mod.rs1029–1033](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod.rs#L1029-L1033)。

关键原始行（仅加行号，Apache-2.0；许可/NOTICE已保留）：

[codex-rs/core/src/tasks/mod.rs:1080–1094](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod.rs#L1080-L1094)

```rust
1080:         select! {
1081:             _ = task.done.notified() => {
1082:             },
1083:             _ = tokio::time::sleep(Duration::from_millis(GRACEFULL_INTERRUPTION_TIMEOUT_MS)) => {
1084:                 warn!("task {sub_id} didn't complete gracefully after {}ms", GRACEFULL_INTERRUPTION_TIMEOUT_MS);
1085:                 // Forced teardown can abort independently spawned tool dispatches
1086:                 // whose guards are destroyed later. Do not publish complete totals
1087:                 // for a dependency graph that has not settled.
1088:                 task.turn_context.turn_timing_state.invalidate_critical_path();
1089:             }
1090:         }
1091: 
1092:         task.handle.abort();
1093:         // Aborting schedules destruction; join before freezing the turn's timing.
1094:         let _ = task.handle.await;
```

[codex-rs/core/src/session/turn.rs:3757–3763](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L3757-L3763)

```rust
3757:     if cancellation_token.is_cancelled() && !response_completed && can_interrupt {
3758:         let deadline = tokio::time::Instant::now() + Duration::from_secs(1);
3759:         if let Some(interrupt) = stream.interrupt.take() {
3760:             let _ = interrupt.send(codex_api::ResponseInterrupt::Cancel { deadline });
3761:         }
3762:     }
3763:     drop(stream);
```

[codex-rs/core/src/tasks/mod.rs:1164–1178](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod.rs#L1164-L1178)

```rust
1164:         let event = EventMsg::TurnAborted(TurnAbortedEvent {
1165:             root_turn_id: Some(task.turn_context.root_turn_id()),
1166:             turn_id: Some(task.turn_context.sub_id.clone()),
1167:             reason,
1168:             error,
1169:             started_at,
1170:             completed_at,
1171:             duration_ms,
1172:         });
1173:         self.send_event(task.turn_context.as_ref(), event).await;
1174:         // Regular items were flushed before this terminal event was appended; buffering
1175:         // thread writers may not flush it without another explicit barrier.
1176:         if let Err(err) = self.flush_rollout().await {
1177:             warn!("failed to flush rollout after emitting terminal turn event: {err}");
1178:         }
```

已读测试体：`UncooperativeTimedTask` 忽略取消token并永远pending；`hard_abort_drops_timing_guards_before_profile_completion` 调abort_all_tasks后确认该任务timing guards已消失，critical-path snapshot不可再当作完整结果。[mod_tests.rs597–665](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod_tests.rs#L597-L665)。**只读，未运行**；这是Rust任务强制销毁/统计边界测试，不是进程退出、无迟到文件写入或外部API撤销实验。既有进程/lease/fork证据见 managed-command-implementation 与 fork研究，本次不重复扩展OS研究。

## 四个不能混用的状态

| 状态 | 能证明 | 不能证明 |
|---|---|---|
| stop request已持久接受 | 同turn的控制意图不会被runner旧snapshot覆盖，后续可查询。 | runner已经观察到、当前调用已退出。 |
| runner关闭新工作admission | 在明确线性化边界之后，不再批准新的模型请求/工具执行。 | 边界前已批准的调用尚未发生副作用；OS没有运行中worker。 |
| 本地执行者已静止 | 在既有lease/supervisor契约及确认回执覆盖的范围内，当前本地执行已结束。 | 外部服务器没有继续工作、此前文件效果已回滚。 |
| 效果已确认或未知 | 有operation receipt/内容+mode等对账可说明已确认效果；否则保留unknown。 | checkpoint自动提供外部exactly-once或把unknown变成未执行。 |

第一步应显示“停止请求已接受，等待当前调用结束/timeout”，随后才显示runner已停止。不能把一条stop回执命名为“所有进程已杀死”。

## 本地有限第一步（独立选择，尚未实现/验收）

复用现有SQLite控制消息/事务与operation状态，不添加通用取消框架，不用强杀来伪造结果。现有Engine.run整轮持workspace锁，外部stop应使用独立短SQLite事务，**不拿该长锁、不修改tasks整份snapshot**。

- **同Turn且幂等。** 入口携带task_id、expected_turn_id、稳定request_id；短事务检查当前turn/可接收状态后写stop行+metadata事件。重复相同请求返回原回执；错turn/迟到有明确拒绝，不污染下一turn。stop状态可为requested→acknowledged；ack表示runner按其契约停止，结果不确定另保留在既有pending/outcome字段，勿混为success。仅提交成功后返回accepted。
- **原子关启动门。** stop接受与runner每次新的model_started/tool_started（含patch_step_started）提交必须经同一SQLite写事务检查/claim，避免“查完stop→外部接受stop→又开始下一工具”。若调用claim先提交，它属于已批准的当前调用，可能在stop接受后才真正进入OS/网络；第一版明确等待它。不能仅靠run循环顶部读一次，就承诺request accepted后绝无新OS启动。
- **当前调用不主动打断。** 既有timeout和supervisor保持；model/tool返回时先记录真实结果、usage/receipt，再应用stop，不发下一模型/工具。模型已返回的未启动tool批次不能执行；queued/planned调用可生成明确not_started/cancelled反馈以闭合call/result历史，不能伪造成工具成功。多文件patch若在步骤边界停，记录已提交前缀与remaining_steps；不要继续后面的写步骤来凑成“完整批次”。若选择整次patch返回才停，必须明确这整个patch是已批准当前调用。
- **恢复分支优先拦截。** 当前run遇running会先调用Tools.recover；某些verify恢复在原效果未发生时会补做write。stop存在时应先检查：已提交且确认完成的结果可正常保留；planned/尚未启动项不执行；running/patch_inflight未确认则保留pending、receipt、outcome_unresolved并needs_confirmation。第一版可以完全不做running重对账，仅保守暂停；不能为了响应stop调用会写的recover、清空pending或按用户abort就说未发生。只读观察以后可单独增加，不把它藏进此增量。
- **完成/审批/steer竞争。** 正常final/终结事务与stop提交串行化：stop先到则成为user-stopped结果，已正常完成先到则stop返回late。awaiting_approval没有正在运行的项目副作用时可立即停止并拒绝后来的approve；pending steering明确withdrawn/not_applied，不能让反馈把stop转成新一轮工作。stop不会延长deadline、重置step/cost、解除unknown或重开已终结turn。
- **费用与私有诊断照旧。** 不释放旧unknown reservation、不重新发请求以取得“取消确认”、不伪造token/cost=0。模型调用若尚未确认结果/usage，继续保留unknown。原API预算ledger完全不因取消改变；公共回执只给请求/turn状态和等待原因，私有详情沿用既有诊断边界。

本地当前任意外部效应要区分三种结果：已知已发生、已知未启动、无法确认。仅当未决操作都已确认/确实未启动时，user_stopped可作为resolved终结；未知操作的展示可以“停止且需确认”，但不能变为允许下一turn的resolved失败。

## 足够的小验收集合（建议，未执行）

1. 两进程：runner阻塞在model/tool时另一进程提交stop，不受长workspace锁阻止，回执是requested/waiting；当前调用完成后结果/usage仍记录，下一工具/模型不被批准。
2. 事务barrier分别让stop和下一dispatch claim先提交，证明两种结果符合公开契约；相同request重试、错turn、normal-final竞争不跨turn。
3. 实际kill/reopen：留下running写/patch_step后提交stop，恢复不得再写；pending与unknown保留，原inode/mtime/内容不出现为“完成取消”而补做的更改。模型pending亦不能重发，计费unknown不释放。
4. awaiting_approval与已返回多tool批次的未启动余项被明确取消；已确认前缀不丢。实际执行仍由既有trusted supervisor收尾，stop不冒称OS立即静止。

只完成以上边界是持续Coding Agent中的一个运行控制增量；active worker/model主动取消、可观测OS终止回执、远端取消语义仍留后续，不能据此宣称完整上游取消parity。

## 六个直接来源身份

| 源码 | Git blob SHA |
|---|---|
| [codex-rs/app-server/src/request_processors/turn_processor.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/src/request_processors/turn_processor.rs) | `45a1f5e12b5fa103e56f6e5dddc62d4e5e4252f8` |
| [codex-rs/core/src/session/handlers.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/handlers.rs) | `d230fc4caf1010a7406aa6c44e08ab6d04dc405a` |
| [codex-rs/core/src/session/mod.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs) | `c52dd4e9c7b6ca1e834bac631c30d015cff7c557` |
| [codex-rs/core/src/tasks/mod.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod.rs) | `af0f10df1d7f444ccff7b5a97589ac98ef5d10d5` |
| [codex-rs/core/src/session/turn.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs) | `1fdf62faca3d396b4f1b6b7c64d879f99be7b2e9` |
| [codex-rs/core/src/tasks/mod_tests.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod_tests.rs) | `701607eb4683ee966caad12fee5a338d7b828035` |

本次只追上述关键函数体及小型桥接/测试；没有追app-server最终响应监听、所有tool-runtime取消分支或OS进程回收，也没有执行行为测试。官方事实、边界推断和本地有限stop设计已分开。

资料校验：6个直接来源、3组代码摘录逐字比对通过，25个固定源码URL及行范围有效，本地链接存在；不替代运行时或上游行为验证。
