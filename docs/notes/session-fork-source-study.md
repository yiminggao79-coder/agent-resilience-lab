# Session/thread 分支：固定 Codex 的历史边界与价值判断

固定参照 [`openai/codex@e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`](https://github.com/openai/codex/tree/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9)。2026-11-08 定向阅读3个实现文件及1个关键测试来源（session/mod.rs复用已取得的固定源码）。只写本文，不改runtime/开始实现，不读用户私有sessions、keys、ledger或V2，不调用模型API或运行上游。本地检查分支 `codex-reconstruction`、HEAD `46a056df909f1ee49e471f86e7dde7a8b5f8935a`；tool cancellation等未提交变更已保留，未干扰fullsuite。

**结论**：上游fork创建新的thread执行环境，继承选择的**历史**与有效配置；历史turn ID保留，mid-turn副本可补interrupted边界。所读链没有自动复制项目目录、创建Git worktree或回滚文件。它也不是复制父线程的live task、进程、审批waiter或pending工具。会话分支能比较后续思路，但不能被包装为文件隔离或任意运行中任务的安全复制。

## 函数体链：读取历史→选择边界→启动新线程→持久化

1. `fork_legacy_thread` 从ThreadStore按rollout_path读带history的线程，转InitialHistory；`fork_thread_from_history` 将snapshot与history交给 `fork_thread_with_initial_history`。[manager1752–1799](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L1752-L1799)。这是基于已取得/持久历史的分叉入口，不是复制操作系统进程。
2. 后者识别来源thread lineage，捕获供新线程使用的instructions，按options.config确定multi-agent语义，调用 `fork_history_from_snapshot`，再组新ThreadSpawnRequest→spawn_thread。[manager1836–1888](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L1836-L1888)。ThreadSpawnRequest::new默认是Copied；并不携带running_task字段，inherited_exec_policy默认None。[manager462–483](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L462-L483)。
3. spawn按options的config/environment请求进入Session::spawn；默认environment请求仍由config.cwd与workspace_roots产生。[manager2508–2524](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L2508-L2524)、[2802–2855](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L2802-L2855)。Session构造配置时以该config建立approval_policy、permission_profile、shell/env policy、cwd和workspace roots，并validate。[session/mod.rs1008–1051](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L1008-L1051)。
4. Session的 `record_initial_history(InitialHistory::Forked)` 为history分配缺失item ID、apply_rollout_reconstruction、读出usage显示信息；然后按Copied/Referenced策略写入child rollout，记录child有效settings，调用Standard持久化及非subagent flush。[session/mod.rs1851–1922](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L1851-L1922)。

原始行摘录（只加行号；Apache-2.0/Copyright 2025 OpenAI，LICENSE/NOTICE已保留）：

[codex-rs/core/src/thread_manager.rs:1877–1888](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L1877-L1888)

```rust
1877:         let interrupted_marker = InterruptedTurnHistoryMarker::from_config_and_version(
1878:             &options.config,
1879:             multi_agent_version,
1880:         );
1881:         options.initial_history = fork_history_from_snapshot(snapshot, history, interrupted_marker);
1882:         let agent_control = self.agent_control_for_config(&options.config);
1883:         let mut request =
1884:             ThreadSpawnRequest::new(options, Arc::clone(&self.state.auth_manager), agent_control);
1885:         request.forked_from_thread_id = source_thread_id;
1886:         request.fork_persistence = fork_persistence;
1887:         request.inherited_instructions = Some(instructions);
1888:         Box::pin(self.state.spawn_thread(request)).await
```

## 历史/ID/运行中状态的准确边界

| 方面 | 已核实的原版事实 | 不能外推的结论 |
|---|---|---|
| Nth-user snapshot | manager的truncate_before_nth_user_message调用0-based user边界helper；若越界但source历史mid-turn，退回active turn的start index/最后user之前截断，不带未完成后缀；为空返回InitialHistory::New。[3032–3067](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L3032-L3067) | 底层user-position分类helper本轮没有展开，不能宣称对任意synthetic/multimodal边界均已研究或测试。 |
| Interrupted snapshot | snapshot_turn_state读取rollout lifecycle/ThreadHistoryBuilder，判断ends_mid_turn；将Resumed转Forked，必要时append marker+TurnAborted。旧explicit turn/root ID进入新增的历史终结事件。[3079–3139](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L3079-L3139)、[3142–3218](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L3142-L3218) | 这是副本边界，不代表父thread被发Interrupt，更不说明父进程已静止或外部副作用不存在。 |
| 新thread身份 | fork_legacy_thread契约明确fresh id；spawn的agent_control_factory分支对Forked使用reserved/generate_thread_id，Resumed才沿用conversation_id。[1744–1748](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L1744-L1748)、[2590–2603](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L2590-L2603) | 本轮没再展开Session::new的所有默认ID生成分支；不把copied历史里的旧turn ID当作新thread ID。 |
| 新Turn | fork初始化会new_default_turn用于reconstruction，但历史旧turn ID保留。之后submit_turn_input生成新的submission UUID7。[session/mod.rs1213–1235](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L1213-L1235)、[1292–1296](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L1292-L1296)。既有 [steering源码研究](steering-source-study.md) 已追过started input以submission_id建立new turn context。 | fork本身不等于立即执行一个用户任务；本轮没有跑“fork后首Turn”上游测试，不能声称完整ID端到端实测。 |
| running/pending | Interrupted可处理**历史**仍mid-turn的副本；fork入口传的是InitialHistory与config，并不接管父RunningTask/worker handle或live queue。 | 已读关键测试创建的是带mid-turn记录的历史，并非真实在运行的worker。app-server对实际active/running fork的完整准入、本地在内存未落盘pending会否被包含，本轮未核实。不能笼统说“上游任意running都可安全fork”。 |

[codex-rs/core/src/thread_manager.rs:3152–3171](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L3152-L3171)

```rust
3152:         ForkSnapshot::Interrupted => {
3153:             let history = match history {
3154:                 InitialHistory::New => InitialHistory::New,
3155:                 InitialHistory::Cleared => InitialHistory::Cleared,
3156:                 InitialHistory::Forked(history) => InitialHistory::Forked(history),
3157:                 InitialHistory::Resumed(resumed) => {
3158:                     InitialHistory::Forked(Arc::unwrap_or_clone(resumed.history))
3159:                 }
3160:             };
3161:             if snapshot_state.ends_mid_turn {
3162:                 append_interrupted_boundary(
3163:                     history,
3164:                     snapshot_state.active_turn_id,
3165:                     snapshot_state.active_turn_root_id,
3166:                     snapshot_state.active_turn_started_at,
3167:                     interrupted_marker,
3168:                 )
3169:             } else {
3170:                 history
3171:             }
```

append_interrupted_boundary创建reason=Interrupted事件，completed_at/duration保持未知；原explicit turn ID被保留或旧历史无ID时仍None。[manager3179–3218](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L3179-L3218)。工具call缺结果的下一请求规范化可复用 [terminal-history研究](terminal-history-source-study.md) 的prompt-only aborted规则；这不证明父call未执行，也不能作为本地复制未知pending的授权。

## Rollout 与权限配置：已读到哪里

**Copied**：保留历史prefix，把child effective ThreadSettingsApplied放到同一次append的末尾；避免冷恢复把祖先settings当作child最新值。**Referenced**：祖先在history_base后面，仅将snapshot产生的child边界和有效settings本地写入。CopiedDeferred对subagent另有延后barrier。[session/mod.rs1870–1921](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L1870-L1921)。

[codex-rs/core/src/session/mod.rs:1870–1896](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L1870-L1896)

```rust
1870:                 let thread_settings_applied =
1871:                     RolloutItem::EventMsg(thread_settings::applied_event(self).await);
1872:                 match &self.fork_persistence {
1873:                     ForkPersistence::Referenced {
1874:                         inherited_item_count,
1875:                         ..
1876:                     } => {
1877:                         // Ancestor records remain behind history_base; only effective child
1878:                         // settings and boundaries synthesized by snapshot processing are local.
1879:                         rollout_items.drain(..*inherited_item_count);
1880:                         rollout_items.insert(0, thread_settings_applied);
1881:                     }
1882:                     ForkPersistence::Copied | ForkPersistence::CopiedDeferred
1883:                         if is_paginated_subagent =>
1884:                     {
1885:                         // Paginated subagents already persist inherited context when their live
1886:                         // thread is created.
1887:                         rollout_items.clear();
1888:                         rollout_items.push(thread_settings_applied);
1889:                     }
1890:                     ForkPersistence::Copied | ForkPersistence::CopiedDeferred => {
1891:                         // Keep the copied prefix and effective child settings in one append so a
1892:                         // cold resume cannot observe inherited settings as the latest value.
1893:                         rollout_items.push(thread_settings_applied);
1894:                     }
1895:                 }
1896:                 self.persist_rollout_items(&rollout_items).await;
```

manager.fork_prepared_thread将PreparedFork.model_context包装为Resumed输入，再设置Referenced{history_base,inherited_item_count}并走同一fork处理。[1807–1832](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L1807-L1832)。LocalThreadStore.prepare_fork只转发paginated_fork::prepare，[local/mod.rs652–654](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/mod.rs#L652-L654)；**本轮没有展开paginated_fork内部事务/祖先生命周期**，因此只确认child写入策略，不宣称已研究完整引用分支存储。ensure/flush的错误语义沿用既有资料，本文不额外证明掉电持久性。

config不是简单“复制旧rollout最后一份权限”。fork_options_from_parent从parent.session.fork_config捕获当前有效config、settings与environments，显式携带history mode、环境选择、MCP extensions、disabled plugin IDs、reasoning pin，并把部分thread settings overrides返回给调用方，要求首次turn前应用。[manager1714–1741](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L1714-L1741)、[session/mod.rs2415–2460](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L2415-L2460)。普通fork也可由caller提供StartThreadOptions，spawn用其config创建child权限配置。已读链支持“有明确配置/override来源”，不支持继承一次性批准、审批waiter或未确认操作；本轮没追全部权限snapshot内部、审批cache与动态grants，不能声称它们全部继承或全部清空。

## 工作区没有自动隔离

默认环境仍指向config.cwd/workspace_roots；fork_options_from_parent还可继承当前环境选择。所读fork链没有copytree、Git worktree创建或文件状态回滚。ThreadSpawnRequest的SessionIsolation与runtime配置，不能因名称含Isolated就当作文件系统副本。attachment metadata的copy也不是复制项目目录。

**推断**：父/child如果共用同一cwd，fork之后父新写入会成为child工具看到的当前文件，而copied history可能描述旧结果。没有工作区快照就没有“回到对话中某一步的文件”。独立新workspace只有caller显式提供并实际准备、验证后才成立；不能光改child.workspace字符串。

## 一个关键测试体：只读，未运行

[thread_manager_tests.rs4004–4108](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager_tests.rs#L4004-L4108) `interrupted_fork_snapshot_preserves_explicit_turn_id`：建立带TurnStarted("turn-explicit")与user/partial assistant的legacy source；实际调用fork_legacy_thread(Interrupted)，读取**新fork rollout**，断言有TurnAborted且turn_id仍是"turn-explicit"。它支撑“fork副本持久化补旧turn的中断边界”，不证明活工具迁移、workspace隔离、child首Turn新ID或权限完整继承。其他测试仅用于名称定位/截取候选，没有作为本轮额外测试结论。

## 对本项目的可选差异与实际价值（尚未选择实现）

当前Engine已有独立task/session与continue_turn，Store.lock既锁DB也锁workspace，并在同workspace进入时检查/轮转execution generation。**它是串行执行栅栏，不是分支文件隔离**。因此有界会话分支可以服务“在同一已完成调查后尝试另一个解释/计划”，但在编码任务上比修复缺失的文件/检索/执行能力更间接；尤其同目录不同分支写入会相互覆盖，不能当并行实验平台。

若后续选择实现，建议第一版只做：

- 仅fork已resolved terminal、canonical call/results完整、无pending/queue/unknown、无pending controls的source；先取得现有Store.lock并记录source revision/hash。**不复制runtime state**、活审批、stop/steer请求或worker/generation。与上游支持mid-turn历史的广范围相比，这是明确更保守的本地选择。
- 新task/session/turn ID与lineage(source_task/session/turn/revision)，复制选定完整历史边界，保留原历史call ID、内容/receipt/provenance；child不能重新执行这些历史calls。canonical前缀、derived summary/archive索引若保留必须重新校验引用，不把旧待执行plan当新任务已获授权。
- child新任务说明与history installation同一事务，source记录不改；使用现有newTurn启动而非复制ready/running状态。child新metrics与ancestor历史usage分别展示，全局付费预算/旧unknown不释放也不重复算作child新请求。
- 默认同workspace必须明确标注“共享当前文件”；继续沿现有workspace锁串行执行。permissions/read-only/toolset身份重新验证并沿现有限制，不继承一次性approve。要文件隔离须另有具体workspace准备方案与文件验证，本轮不做。
- 有判别力的验收是child新增历史不改source、child工具看到的是实际当前文件、错source revision/未知pending拒绝、复制call不会重执行；不能用“两份JSON不同ID”证明代码实验隔离。

**优先级判断**：会话分支是中等价值的交互/实验组织能力，适合在用户确实要比较后续路径时做一个resolved-only版本；它不会直接提高单条coding路径的读改测能力。没有明确分支使用场景时，继续补已观察到的coding工具/运行控制缺口更直接。此判断是本项目取舍建议，不是上游事实，也不是已获准实现或整个项目完成的结论。

## 四个直接来源身份与未核实处

| 源码 | Git blob SHA |
|---|---|
| [codex-rs/core/src/thread_manager.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs) | `8cbc7bc9b233503d303fc97f84c0f1b9fc312e47` |
| [codex-rs/core/src/session/mod.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs) | `c52dd4e9c7b6ca1e834bac631c30d015cff7c557` |
| [codex-rs/thread-store/src/local/mod.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/mod.rs) | `c61c125f7f721d90398372406af720bbe00e4b1d` |
| [codex-rs/core/src/thread_manager_tests.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager_tests.rs) | `c6ca9736e4c6e0e8cf2eec509e50d279febc010b` |

未核实：默认Session::new ID内部全路径、底层Nth-user识别、prepared/referenced store全链、active app-server准入、全部权限/审批继承、真实fork后首Turn、并发父child文件行为。这些已读机制与明确缺口足以作当前工程取舍，本轮不继续扩大并发/分支框架研究。

资料校验：4个直接文件（含1个关键测试来源）、3组摘录逐字比对通过，28条固定源码URL/行范围有效，本地链接存在；没有执行上游或本地行为测试。
