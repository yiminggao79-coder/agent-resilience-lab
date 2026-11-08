# 运行中用户反馈：Codex steering 源码研究

固定参照：[`openai/codex@e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`](https://github.com/openai/codex/tree/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9)。2026-11-07 通过 GitHub 连接器取得下列真实文件内容，阅读了本文列出的函数体、分支和测试体；未安装、编译、执行上游或运行其测试。本次只读研究，不接触模型 API、凭据、账本或 V2 题目，不修改 runtime。

本地检查时分支 `codex-reconstruction`，HEAD `771465e6e654cd7be2478d1fa167f998546af3f0`；runtime 有他人正在进行的未提交改动，已保留。已读 PROJECT_STATUS/NEXT_ACTIONS/COVERAGE_MATRIX/EVALUATION 与 session-turn-design。本文补齐旧设计仅引用 turn.rs 输入循环的缺口；其“运行中 steering 尚未实现”描述属于旧增量，不在这里改写历史。

**结论**：上游将“steer 路由接受”“输入进入 history”“某个 sampling 使用该 history”分开。定向 steer 在 active-turn 锁内校验目标并入该 turn 的内存队列；下一采样边界取出、运行 hooks、记录 history/rollout 后再克隆请求视图。任务将结束时还会持同一 active-turn 锁重查队列，避免把刚接受的输入留在正常完成之后。不能由 steer 返回成功推导输入已落盘或模型已按其完成任务。

## 真实调用链：从 app-server 到下一请求

| 顺序 | 已读函数体及实际状态变化 | 证据 |
|---|---|---|
| 1 | `TurnProcessor::turn_steer_inner` 加载 thread、检查 direct input/provider、拒绝空 expected_turn_id/超大输入；将输入映射为 CoreInputItem，带 client_user_message_id 与 additional_context 调用 thread.steer_turn。 | [turn_processor.rs1084–1135](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/src/request_processors/turn_processor.rs#L1084-L1135) |
| 2 | `CodexThread::steer_turn→submit_turn_input_with_mode` 固定 Steer{expected_turn_id}；调用 io.submit_turn_input，不走启动新 turn 的 capacity 申请。 | [codex_thread.rs694–727](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/codex_thread.rs#L694-L727) |
| 3 | `submit_turn_input` 创建 submission_id 和 oneshot reply，包装 `Op::TurnInput`；`submit_with_id` 发送至 tx_sub；只等待 Core 的 routing decision。调用方丢弃 waiter 不撤回已排队调用。 | [session/mod.rs1192–1235](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L1192-L1235) |
| 4 | `submission_loop` 接收 rx_sub，匹配 Op::TurnInput，调用 `turn_input::handle` 并通过 reply 送回结果。handle 对 Steer 分支调用 `steer`。 | [handlers.rs508–511](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/handlers.rs#L508-L511)、[626–636](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/handlers.rs#L626-L636)、[turn_input.rs317–328](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn_input.rs#L317-L328) |
| 5 | `steer` 只允许 UserInput，准备 settings；`steer_input` 持有 active_turn 锁，检查 active task、expected ID、Regular task kind、非空 content、output schema；失败在合并 context 与入队之前返回。 | [turn_input.rs680–733](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn_input.rs#L680-L733)、[902–955](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn_input.rs#L902-L955) |
| 6 | 在同一 active-turn 锁作用域中，合并 additional_context、更新响应 metadata、分配 acceptance_order，将用户 content 移入 TurnInput，再 `extend_pending_input_and_notify_for_turn_state`。后者持 turn_state 锁 append 并 notify watch。 | [turn_input.rs956–995](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn_input.rs#L956-L995)、[input_queue.rs400–418](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/input_queue.rs#L400-L418) |
| 7 | app-server 对 Steered 回传原 active turn_id；NoActiveTurn、ExpectedTurnMismatch、不可 steer kind、空输入等映射成明确错误。**定向 steer 不隐式成为下一 turn**。 | [turn_processor.rs1144–1226](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/src/request_processors/turn_processor.rs#L1144-L1226) |
| 8 | `run_turn` 在允许 drain 时 `get_pending_input`；后者持 active-turn/turn-state 锁用 mem::take 取队列。首次新 turn 及部分 auto-compact continuation 会延后 drain，避免超过初始输入。 | [turn.rs534–563](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L534-L563)、[input_queue.rs435–470](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/input_queue.rs#L435-L470) |
| 9 | `run_hooks_and_record_inputs` 逐项 inspect_pending_input；接受的 item 交给 `record_pending_input`。UserInput 进入 record_user_prompt_and_emit_turn_item；工具输出即使混在 steered batch 也保留 Standard persistence context。 | [turn.rs1038–1083](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L1038-L1083)、[hook_runtime.rs751–809](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/hook_runtime.rs#L751-L809) |
| 10 | `record_user_prompt_and_emit_turn_item` 把 acceptance_order 放入 envelope；`record_prepared_conversation_items` 修改 state.history，再 persist_rollout_items，随后发 user item events、请求 ensure_rollout_materialized。 | [session/mod.rs5870–5906](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L5870-L5906)、[4114–4148](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L4114-L4148) |
| 11 | LocalThreadStore append→write_and_project→durable_write→recorder.record_canonical_items+flush；persist(context) 的具体策略另由 store 决定。 | [live_thread.rs229–296](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/live_thread.rs#L229-L296)、[local/mod.rs578–596](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/mod.rs#L578-L596)、[local/live_writer.rs414–469](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/live_writer.rs#L414-L469) |
| 12 | history 记录完成后，`run_turn` clone_history 构造 sampling input；sampling 捕获 history.input_sequence。一次采样及工具结束后再判断 pending，令 needs_follow_up=true。 | [turn.rs641–690](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L641-L690)、[1934–1946](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L1934-L1946) |

这条链止于已经看过的本地 storage adapter/recorder 接口，不把文件 flush 推论为任意 OS 掉电无损；也不把模型请求包含文本当作模型遵从文本的语义证明。client_user_message_id 在此链中用于相关联和 user item 显示；**本次所读代码没有证明它是跨进程重试的幂等键**。

## 函数体摘录：重要边界

以下为未修改的官方源码短摘录，仅加行号；Apache-2.0，Copyright 2025 OpenAI。完整 许可 与 上游 NOTICE 已保留。

### Active turn 校验与队列所有权

[codex-rs/core/src/session/turn_input.rs:912–929](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn_input.rs#L912-L929)

```rust
912:         let mut active = self.active_turn.lock().await;
913:         let Some(active_turn) = active.as_mut() else {
914:             return Err(NotSubmittedReason::NoActiveTurn);
915:         };
916: 
917:         let Some(active_task) = active_turn.task.as_ref() else {
918:             return Err(NotSubmittedReason::NoActiveTurn);
919:         };
920:         let active_turn_id = &active_task.turn_context.sub_id;
921: 
922:         if let Some(expected_turn_id) = expected_turn_id
923:             && expected_turn_id != active_turn_id
924:         {
925:             return Err(NotSubmittedReason::ExpectedTurnMismatch {
926:                 expected: expected_turn_id.to_string(),
927:                 actual: active_turn_id.clone(),
928:             });
929:         }
```

随后 L931–954 检查 Regular kind、content 和 schema；只有通过后才到以下 enqueue。队列附着于刚校验的 `active_turn.turn_state`，不是另一次查询出来的“当前 turn”。

[codex-rs/core/src/session/turn_input.rs:985–995](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn_input.rs#L985-L995)

```rust
985:         pending_input.push(input);
986:         self.input_queue
987:             .extend_pending_input_and_notify_for_turn_state(
988:                 active_turn.turn_state.as_ref(),
989:                 pending_input,
990:             )
991:             .await;
992:         Ok((
993:             active_turn_id.clone(),
994:             active_task.turn_context.root_turn_id(),
995:         ))
```

[codex-rs/core/src/session/input_queue.rs:407–417](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/input_queue.rs#L407-L417)

```rust
407:         {
408:             let mut turn_state = turn_state.lock().await;
409:             turn_state.pending_input.notified_activity =
410:                 turn_state.pending_input.notified_activity.max(activity);
411:             turn_state.pending_input.items.extend(input);
412:             if !queue_only_mail {
413:                 turn_state.accept_mailbox_delivery_for_current_turn();
414:             }
415:         }
416:         self.activity_tx
417:             .send_replace(activity.unwrap_or(InputQueueActivity::Steer));
```

**事实**：这里更新内存状态并发送 activity notification，没有 SQLite/rollout 调用。**推论**：此处的 Steered acknowledgment 不是“已耐崩溃写入 history”的承诺；宿主突然死亡前仍可能有未 drain 的内存输入。本次没有执行故障实验来测其具体窗口。

### Drain、hooks 和 history 持久化

[codex-rs/core/src/session/turn.rs:1058–1074](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L1058-L1074)

```rust
1058:             // Tool outputs retain their durability barrier, including in mixed input batches.
1059:             let input_persist_context = if persist_context == PersistContext::SteeredUserInput
1060:                 && matches!(input_item.as_ref(), TurnInput::FunctionCallOutput(_))
1061:             {
1062:                 PersistContext::Standard
1063:             } else {
1064:                 persist_context
1065:             };
1066:             record_pending_input(
1067:                 sess,
1068:                 turn_context,
1069:                 model_info,
1070:                 input_item.into_owned(),
1071:                 hook_outcome.additional_contexts,
1072:                 input_persist_context,
1073:             )
1074:             .await;
```

[codex-rs/core/src/session/mod.rs:4114–4120](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L4114-L4120)

```rust
4114:             state
4115:                 .pending_audio_duration_ids
4116:                 .extend(file_audio::file_ids(&items).cloned());
4117:             state
4118:                 .history
4119:                 .record_annotated_items(&mut items, model_info.truncation_policy.into());
4120:         }
```

随后 session/mod.rs L4143–4148 将 envelope 变为 RolloutItem 并调用 persist_rollout_items；该方法 L5310–5317 在 append 错误时记录 error 并返回 false。record_prepared_conversation_items 没有把此 false 变成调用方异常。

[codex-rs/core/src/session/mod.rs:1631–1647](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L1631-L1647)

```rust
1631:     pub(crate) async fn try_ensure_rollout_materialized(
1632:         &self,
1633:         context: PersistContext,
1634:     ) -> std::io::Result<()> {
1635:         if let Some(live_thread) = self.live_thread() {
1636:             live_thread
1637:                 .persist(context)
1638:                 .await
1639:                 .map_err(std::io::Error::other)?;
1640:         }
1641:         Ok(())
1642:     }
1643: 
1644:     pub(crate) async fn ensure_rollout_materialized(&self, context: PersistContext) {
1645:         if let Err(e) = self.try_ensure_rollout_materialized(context).await {
1646:             warn!("failed to materialize thread persistence: {e}");
1647:         }
```

[codex-rs/thread-store/src/store.rs:110–124](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/store.rs#L110-L124)

```rust
110:     /// A turn is about to begin sampling after its input has been recorded.
111:     TurnStart,
112:     /// Accepted user input is being recorded before an active turn's next sampling request.
113:     /// This does not apply to tool outputs, cancellation, or task cleanup.
114:     SteeredUserInput,
115: }
116: 
117: impl PersistContext {
118:     /// Whether a store may enqueue this checkpoint before returning and fence it at a later
119:     /// durability barrier. Stores may still choose to persist synchronously.
120:     pub fn allows_background_persistence(self) -> bool {
121:         match self {
122:             Self::ThreadPreparation | Self::Standard => false,
123:             Self::SubagentSpawn | Self::TurnStart | Self::SteeredUserInput => true,
124:         }
```

**事实**：SteeredUserInput 的接口允许 store 后台排队、稍后 fence；固定版本 LocalThreadStore 仍会走同步 recorder flush。ensure_rollout_materialized 是 warning wrapper，不是错误传播给 steer requester 的结果；memory history 更新和持久化失败不是单一跨资源事务。**本项目选择**：为了 durable mailbox，提交成功应该只在本地 SQLite 事务确认后返回，并且把 history 追加与 inbox 已消费状态放进同一事务。这个更强局部保证是我们自己实现的约定。

### 采样边界，不在正在执行的请求里改写输入

[codex-rs/core/src/session/turn.rs:641–655](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L641-L655)

```rust
641:             // Construct the input that we will send to the model.
642:             let sampling_request_input = sess
643:                 .clone_history()
644:                 .instrument(trace_span!("run_turn.prepare_sampling_request_input"))
645:                 .await;
646: 
647:             run_sampling_request(
648:                 Arc::clone(&sess),
649:                 Arc::clone(&step_context),
650:                 Arc::clone(&turn_context.extension_data),
651:                 Arc::clone(&turn_diff_tracker),
652:                 &mut client_session,
653:                 sampling_request_input,
654:                 cancellation_token.child_token(),
655:             )
```

[codex-rs/core/src/session/turn.rs:675–690](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L675-L690)

```rust
675:                 can_drain_pending_input = true;
676:                 // Process async hooks only after sampling and its tools have finished.
677:                 drain_async_hook_results(&sess, &turn_context, /*before_user_prompt*/ false).await;
678:                 let (has_pending_input, token_status) = async {
679:                     let has_pending_input =
680:                         sess.input_queue.has_pending_input(&sess.active_turn).await;
681:                     let token_status = super::context_window::context_window_token_status(
682:                         sess.as_ref(),
683:                         turn_context.as_ref(),
684:                     )
685:                     .await;
686:                     (has_pending_input, token_status)
687:                 }
688:                 .instrument(trace_span!("run_turn.collect_post_sampling_state"))
689:                 .await;
690:                 let needs_follow_up = model_needs_follow_up || has_pending_input;
```

输入抵达时，正在运行的模型请求已经有自己的历史视图。上游另有可选抢占路径：run_sampling_request 在 step_context.preempt 存在时装 watch_user_input；watch 先 subscribe 再检查 queue，防止错过到达。stream receive 被 preempt 后可请求 transport Drain 或结束本次 sampling，再进入 follow-up。[watch/input_queue.rs306–341](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/input_queue.rs#L306-L341)、[turn.rs1901–1907](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L1901-L1907)、[3091–3126](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L3091-L3126)。原生 agent mailbox 还有 commentary/reasoning item 边界的抢占与 `DeferMailboxPreemption` feature；它不是所有用户 steer 都立即杀模型/工具的统一规则。[turn.rs3280–3375](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L3280-L3375)。本次未继续追 step_context.preempt 的所有配置来源，不能声称该路径默认总开启。

### 正常完成的最后一次竞态检查

[codex-rs/core/src/tasks/mod.rs:732–752](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod.rs#L732-L752)

```rust
732:         let can_continue =
733:             regular_output.is_some() && turn_context.terminal_error.lock().await.is_none();
734:         let finishing = CancellationToken::new();
735:         let finish_recording = finishing.clone().drop_guard();
736:         let (turn_state, unadmitted_task, mut pending_input, outcome_handoff) = {
737:             let mut active = self.active_turn.lock().await;
738:             let active_turn = active.as_mut()?;
739:             if !Arc::ptr_eq(&active_turn.task.as_ref()?.turn_context, &turn_context) {
740:                 return None;
741:             }
742:             if can_continue {
743:                 let state = active_turn.turn_state.lock().await;
744:                 if state.accepts_mailbox_delivery_for_current_turn()
745:                     && !state.pending_input.is_empty()
746:                 {
747:                     return regular_output.map(|output| output.mcp_startup_requirements);
748:                 }
749:             }
750:             let task = active_turn.task.take()?;
751:             task.handle.detach();
752:             active_turn.finishing = Some(finishing);
```

返回 requirements 后，任务外层不会直接发 completed，而是再次运行相同 turn 的 run_turns：

[codex-rs/core/src/tasks/mod.rs:457–471](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod.rs#L457-L471)

```rust
457:                 let Some(requirements) = session
458:                     .on_task_finished(Arc::clone(&ctx_for_finish), task_result)
459:                     .await
460:                 else {
461:                     break;
462:                 };
463:                 task_result = RegularTask::run_turns(
464:                     Arc::clone(&session),
465:                     Arc::clone(&ctx_for_finish),
466:                     Vec::new(),
467:                     requirements,
468:                     /*prewarmed_client_session*/ None,
469:                     task_cancellation_token.child_token(),
470:                 )
471:                 .await;
```

**事实**：正常可继续的 turn 在 closing 锁内发现 pending 输入会继续；否则 task.take() 先关闭 steer admission。steer_input 必须找到 task，因此之后的定向输入得到 NoActiveTurn，不会被偷偷放进下一个 turn。异常/不可继续路径可能把已取出的 pending 输入仅记录到历史，然后终结，并不保证该输入曾被模型采样。[tasks/mod.rs765–800](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod.rs#L765-L800)。这个区别也要求我们的 receipt 不应把“存入历史”写成“模型已经应用”。

## 对当前独立实现的最小建议（本项目选择）

本地读到的 Engine.run 在整轮持有 workspace/db cooperative lock，并把 state 保留在内存；Store.save 会写回整份 snapshot。仅让第二个进程 load→messages.append→save 会被运行者下一次旧 snapshot 覆盖。复用现有 SQLite、事件和 snapshot 提交机制即可，不需要增加另一套执行器或后台调度框架。

1. **单独持久 inbox，不并发改 snapshot。** 入口例如 `steer(task_id, expected_turn_id, text, submission_id)`。建立极小 inbox 行：session/task/expected_turn、稳定 submission_id、数据库分配的递增 seq、私有 text、状态和关联 message index。外部提交只持短 SQLite 写事务，不获取 runner 整轮 workspace 锁，不修改 tasks.state/messages/revision。DB busy 返回明确暂不可提交，不能声称 queued。用与现有私有状态相同保护；metadata trace 不放正文。
2. **Admission 与终结共用数据库门。** 在同一写事务里检查指定 turn 的 admission-open 状态、有效身份和输入上限后 insert。每个 turn 的 gate 可放独立小行或与已有 turn 索引复用；不要依赖无事务的“先读 phase 后插入”。首次打开/恢复沿用同一 turn，不换身份、不扩预算。迟到、目标不符、未知效果、已终结必须明确拒绝或记 not_applied；不自动创建新 turn。
3. **稳定身份防重。** 同一 task/turn/submission_id 加唯一约束；相同 ID+相同正文重试返回既有 receipt，ID 相同正文不同则冲突。正文可保留私有，hash 仅做身份绑定。client ID/正文 hash 不等于“模型已经遵从”；不引入外部 exactly-once 承诺。
4. **只在完整工具批次结束的安全点追加。** 当前 assistant 一次可发多个 tool_calls，必须让 pending、tool_queue 与全部 tool responses 成对结束后再追加 user 消息；不得在 call/result 之间插入，也不在半个 patch step 内打断。模型响应完成后先保留规范化 assistant/tool 历史，再应用 steering。顺序由 inbox.seq 决定；一次应用有界数量，余项保留，不重置 max_steps/deadline/cost。
5. **History + applied receipt 同事务。** Runner 从自己的最新 state 构建 candidate，在持 SQLite 短事务时读取已 queued 的同 turn 行，把 user 消息追加、记录 applied message index、保存 candidate/turn/event，并将这些 inbox 行标成 applied 一并 commit；commit 失败则都不生效，不从内存提前忘记它们。崩溃前 queued 的重开后可应用；commit 后重开不能重复追加。applied 的明确定义是“已进入可供下次采样的持久 history”；若要显示“已发送给模型”，另记 request identity/accepted watermark，不能混用。
6. **终结与入队线性化，补掉末尾窗口。** `_request` 前查 inbox 仍不够。正常 final 在同一个 SQLite 写事务中重查 inbox：有可应用输入且预算允许，保留助手回答但继续当前 turn；没有则关闭该 turn gate 并提交 completed。排在 close 之前的提交必须被继续处理，或在终止条件下原子标明 not_applied(reason)；排在 close 之后的提交必须被拒绝。预算耗尽/usage_unknown/needs_confirmation/未知副作用不能被 steer 解锁，已排队项需留下可查询未应用原因。
7. **先提供外部 CLI 入口与 status receipt。** 第一版可用另一个本地进程 `steer --turn-id ... --submission-id ...` 提交；现有同步 chat 是否接入可另增量处理。返回 queued/applied/not_applied/rejected 的区别和 target turn，默认等到下一完整批次边界；不宣传“实时抢占”或“撤销已发生副作用”。

这里的 applied 状态不代表自然语言效果已被执行成功，也不代表本次模型请求一定已发出；正常终结与预算失败都必须有可解释归属。后续若加 stream preemption、同 turn 审批等待即时反馈或逐工具取消，应另定义其安全点，不能顺手放宽这一最小版本。

## 有区分力的验证建议（尚未执行）

- 实际两个进程：runner 模型请求阻塞期间提交 inbox，允许 runner 保存新 snapshot 后核验 queued 行仍在；当前请求不包含新文本，下一请求包含一次且仅一次。
- 两工具批次：第一工具处阻塞并提交，第二工具和两个结果全部完成后才出现 user 消息；不产生孤儿 tool call/result。
- 正常 final closing barrier：分别安排 enqueue commit 在 gate-close commit 前后；前者继续/明确未应用，后者拒绝，均不得静默进入下一 turn。
- 同 ID 重试、同 ID 不同内容、错 turn、已终结、预算耗尽、approval/unknown outcome；每项核对回执与 snapshot/事件实际状态。
- 实际 kill/reopen 覆盖 enqueue 后未 drain、history+applied commit 前失败、commit 后未返回；不丢、不重，不把未知文件效果当作未发生。
- 并发多个输入按持久 seq 稳定排序；队列/文本上限与 busy 失败不伪报接受。提交文本仅在私有表中，公共 metadata 无泄露。

上游测试**只读体未运行**：`turn_steer_requires_active_turn` 验无 active 返回 -32600；`turn_steer_returns_active_turn_id` 在真实 sleep 工具期间提交，验相同 turn_id、client_id 事件以及下一模型请求包含 context 在 user 文本之前；`turn_steer_rejects_context_only_input_without_merging_context` 验拒空输入且不偷偷合并 context。[turn_steer.rs41–86](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/tests/suite/v2/turn_steer.rs#L41-L86)、[258–445](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/tests/suite/v2/turn_steer.rs#L258-L445)、[451–567](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/tests/suite/v2/turn_steer.rs#L451-L567)。另读 input_queue.rs647–763 的 activity 通知与已有 pending 检测。它们是 mock-model/本机契约设计，不能转换成真实模型质量或我们实现的测试通过。

## 来源身份与研究边界

| 本文直接引用的文件 | Git blob SHA |
|---|---|
| [codex-rs/app-server/src/request_processors/turn_processor.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/src/request_processors/turn_processor.rs) | `45a1f5e12b5fa103e56f6e5dddc62d4e5e4252f8` |
| [codex-rs/core/src/codex_thread.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/codex_thread.rs) | `159334b268e4f14eb559914026b701ed8adee465` |
| [codex-rs/core/src/session/mod.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs) | `c52dd4e9c7b6ca1e834bac631c30d015cff7c557` |
| [codex-rs/core/src/session/handlers.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/handlers.rs) | `d230fc4caf1010a7406aa6c44e08ab6d04dc405a` |
| [codex-rs/core/src/session/turn_input.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn_input.rs) | `5e32f8d984af4e5ab74af1f790aed7cbcada76e0` |
| [codex-rs/core/src/session/input_queue.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/input_queue.rs) | `955491e708aaaaf5d066a873b713a99fb69e6e9c` |
| [codex-rs/core/src/session/turn.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs) | `1fdf62faca3d396b4f1b6b7c64d879f99be7b2e9` |
| [codex-rs/core/src/hook_runtime.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/hook_runtime.rs) | `09235da17db6ce0c513515a2aac2919da35375e4` |
| [codex-rs/thread-store/src/store.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/store.rs) | `b5e43dae125ae75a7a590f5fcd366c41e4a0ecc9` |
| [codex-rs/thread-store/src/live_thread.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/live_thread.rs) | `0c45c2ea6db3a2805d62215872a419baaffdcc6c` |
| [codex-rs/thread-store/src/local/mod.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/mod.rs) | `c61c125f7f721d90398372406af720bbe00e4b1d` |
| [codex-rs/thread-store/src/local/live_writer.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/live_writer.rs) | `0fba3f55f8a40cf58b49c91edf33f9e70a25367a` |
| [codex-rs/core/src/tasks/mod.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tasks/mod.rs) | `af0f10df1d7f444ccff7b5a97589ac98ef5d10d5` |
| [codex-rs/app-server/tests/suite/v2/turn_steer.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/tests/suite/v2/turn_steer.rs) | `38f5cace78cb78927c8f3aa20b320468af8cb7c7` |

以上均为函数体/类型的定向阅读；辅助定位还查看了 core state/turn、step_context、step_activation、submission 和 thread-store exports/types，未将名称扫描计作完整研究。没追 app-server JSON-RPC 最外层 dispatch、所有 hook veto、完整配置抢占来源、全部 storage backend 与恢复 replay；没有证明 upstream queue 的崩溃持久性或客户端请求重试去重。本文在这些边界外的建议明确是本项目选择。

文档完整性验证（2026-11-07）：11 个带行号代码块与连接器取得的原始源码行逐字一致；本地引用存在，官方引用全部固定至上述 commit。此检查仅验证研究资料，没有执行 runtime 或上游测试。
