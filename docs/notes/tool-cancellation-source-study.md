# Active tool cancellation：本地所有权链与有限工程建议

固定参照 [`openai/codex@e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`](https://github.com/openai/codex/tree/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9)。复用 managed-command实现、[有限取消研究](cancellation-source-study.md)、fork边界 和已取得的process_manager体；只补3个上游文件：tools/parallel.rs、unified_exec/process.rs、exec-server/local_process.rs。未运行上游或付费API，未读key/ledger/私有checkpoint/V2；只写本文，不改runtime，不扩shell/fork/backend。

本地读取时分支 `codex-reconstruction`，HEAD `4a8b77e3b15275e9ea777bc01cad09f7ebd1ba4b`；large-delete等未提交工作已保留。本文是下一工程方案的依据，不宣称active tool cancel已经实现。

**结论**：当前trusted supervisor持lease，保证worker死亡后项目仍被隔离，但它也使“杀tool worker”不能立即停止native child。可复用owner pipe与_collect清理结构，令owner EOF/取消被**supervisor主执行路径**观察并进入自身killpg+wait的finally，再退出释放lease。不能照搬HTTP worker的watch线程os._exit。作者已收敛为只主动取消run_tests/run_command/git三类native supervisor：父端收到清理后产生的可信tool_cancelled且回收worker，才可报告本地执行域静止；无可信回执仍unknown，既有lease fence继续阻断重入。mutating效果即使静止仍可能未知。

## 本地已读的实际链

| 层 | 当前行为与所有权 |
|---|---|
| Runner→trusted tool worker | tools.py::_dispatch 用subprocess.run发送JSON，timeout后read工具报ToolError，非read变UncertainEffect；尚无取消轮询/owner pipe。worker_main在execute/recover外围进入worker_guard，guard跨完整效果。worker_main |
| worker→lease | process_guard.worker_guard 先取得独立execution flock再核generation，ContextVar暴露lease FD；退出只close，不能LOCK_UN，因为trusted inheritor共享同一open-file description。lease release |
| worker→trusted supervisor | supervisor是 sandbox.py的__main__，不是独立supervisor.py。run_tests/run_command/run_git通过_collect启动相应固定入口，仅pass_fds=(lease,)；supervisor持有lease直到其工作结束。三条路径分别在 299、378、449。 |
| supervisor→native child | supervisor内_run_tests/_run_command/_run_git再次调用_collect；每次Popen都start_new_session=True，native child在另一个process group，不继承lease/control FD，环境由既有固定策略构造。故tool worker的killpg不能跨这些独立group自动回收全部child。collector |
| cleanup | _collect连续排空两个有界管道，finally调用_kill_group(proc)、proc.wait、关闭selector/pipes；这是native kill/reap应继续经过的出口。sandbox.py150–209。worker死亡后supervisor最后打印遇BrokenPipe只是忽略，不会提早停止项目。 |
| 已有owner范式 | model.py::_owner_pipe/_watch_owner 让HTTP worker收到owner EOF后os._exit；模型worker没有独立持lease的native项目child。可复用pipe/host callback/selector轮询的思想，**不可复制立即退出动作到supervisor**。 |

已有本地测试体（本次只读未运行）：test_sandbox.py145–187 与 test_commands.py168–199 杀trusted worker后，先断言generation rotation被ExecutionBusy挡住，随后明确期待项目写出finished。这证明旧契约是“继续到结束但lease不提前放”，不是“owner死就停止”。test_process_guard.py 已覆盖继承lease与generation fence；后续新owner行为不能拿这些旧断言当作新cancel证明，直接无owner的legacy测试也要保持其明确边界。

python_expr的子进程未start_new_session，默认与tool worker同group；这是本地事实，本轮不为其增加主动取消。普通文件工具与MCP同样不属于本次选定的native supervisor取消范围；尤其MCP外部服务器是独立控制域，不能顺带修改其lifecycle或宣称取消任意外部服务。

## 固定 Codex 的真实边界：token、task和process不是同一个join

`ToolCallRuntime.handle_tool_call` 给admission/dispatch传入token；完成future在token取消时区分handler需要自行完成、已达到terminal outcome、或dispatch已经结束。否则abort dispatch handle并await，然后产生aborted工具结果。[parallel.rs178–195](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/parallel.rs#L178-L195)、[300–337](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/parallel.rs#L300-L337)、[374–435](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/parallel.rs#L374-L435)。

[codex-rs/core/src/tools/parallel.rs:374–391](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/parallel.rs#L374-L391)

```rust
374:             let mut result = tokio::select! {
375:                 res = &mut dispatch_handle => res.map_err(join_error)?,
376:                 _ = cancellation_token.cancelled() => {
377:                     // Dynamic handlers observe the token and publish their terminal item themselves.
378:                     if finishes_on_cancellation
379:                         || call_state.terminal_outcome_reached.load(Ordering::Acquire)
380:                         || dispatch_handle.is_finished()
381:                     {
382:                         dispatch_handle.await.map_err(join_error)?
383:                     } else {
384:                         let secs = started.elapsed().as_secs_f32().max(0.1);
385:                         abort_dispatch_span.record("aborted", true);
386:                         dispatch_handle.abort();
387:                         match dispatch_handle.await {
388:                             Ok(result) => result,
389:                             Err(err) if err.is_cancelled() => {
390:                                 let _reservation = call_state.invocation.take();
391:                                 let response = Self::aborted_response(&call, Some(secs));
```

这证明的是handler task的处理边界，**不是**所有native process已reap。已有process_manager源码还表明live process会被Arc存入process_store；initial call guard Drop仅修改active flag与collection_finished token，没有无条件kill进程。[process_manager.rs614–647](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L614-L647)、[292–298](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L292-L298)。因此不能假定turn token取消必然drop掉最后一个process owner；背景终端本来就有独立生命周期。

显式process管理路径是manager.terminate_process→UnifiedExecProcess.terminate_confirmed；前者还有already_exited与process identity检查。[manager1856–1889](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L1856-L1889)。后者名字虽含confirmed，函数体如下：

[codex-rs/core/src/unified_exec/process.rs:257–269](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process.rs#L257-L269)

```rust
257:     pub(super) async fn terminate_confirmed(&self) -> Result<(), UnifiedExecError> {
258:         match &self.process_handle {
259:             ProcessHandle::Local(process_handle) => process_handle.terminate(),
260:             ProcessHandle::ExecServer(process_handle) => {
261:                 process_handle
262:                     .terminate()
263:                     .await
264:                     .map_err(|err| UnifiedExecError::process_failed(err.to_string()))?;
265:             }
266:         }
267:         self.signal_exit(self.exit_code());
268:         self.finish_termination();
269:         Ok(())
```

ExecServer分支await的是terminate RPC；LocalExecProcess→backend.terminate→terminate_process的实际实现先对session发terminate，再返回running字段，并未在该函数等待exit/read-closed。[local_process.rs897–898](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/local_process.rs#L897-L898)、[986–992](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/local_process.rs#L986-L992)。

[codex-rs/exec-server/src/local_process.rs:739–754](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/local_process.rs#L739-L754)

```rust
739:                     if process.exit_code.is_some() {
740:                         return Ok(TerminateResponse { running: false });
741:                     }
742:                     process.termination_requested = true;
743:                     process.session.terminate();
744:                     true
745:                 }
746:                 Some(ProcessEntry::Starting(_)) => {
747:                     process_map.remove(&params.process_id);
748:                     true
749:                 }
750:                 None => false,
751:             }
752:         };
753: 
754:         Ok(TerminateResponse { running })
```

process.rs另有exit_rx await→signal_exit状态路径，[392–405](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process.rs#L392-L405)；Drop只调用terminate，[702–705](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process.rs#L702-L705)。**事实/推断的分界**：本次已读体支持“分开发送终止请求、更新状态、观察退出”；不支持从函数名推论跨平台/远端所有子孙均静止。底层PTY kill/reap已有资料，本轮没有再扩读所有OS实现，也没有执行它们。

已读上游测试体，均未运行：parallel.rs [854–900](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/parallel.rs#L854-L900) 检查admission前取消不报告execution_started；[995–1070](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/parallel.rs#L995-L1070) 检查handler已经完成时稍后的cancel保留真实success及completed lifecycle；local_process.rs [1710–1727](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/local_process.rs#L1710-L1727) 请求terminate之后**测试自己再注入process.exit**并断言统计terminated，不能据此宣称真实OS已被reap。

## 作者已收敛的下一有限工程取舍（本地选择，尚未验收）

1. **只做native三类。** run_tests/run_command/git的Tools父端通信加入host-only scoped stop检查与owner pipe，轮询时只关闭writer示意取消，等待原有deadline；trusted worker仅转传reader给相应supervisor。现有工具schema/模型参数、sandbox权限、fork禁令、deadline与审批不变；pending running仍在调用前持久化。文件/python_expr/MCP不加新watcher，不改稳定model owner模块，也不宣称它们active取消。
2. **owner死亡必须传到supervisor。** parent保留唯一writer；reader只交trusted worker，并显式传给trusted supervisor；native项目child一律不继承reader/writer/lease。supervisor的probe与实际运行两次_collect都观察同一个sticky取消状态，不能probe取消后又启动项目。owner信息只能走host私有控制参数/继承FD，不能接受模型提供的任意FD，也不能让项目环境取得它。
3. **supervisor走正常cleanup，绝不watcher直接exit。** reader EOF只设置Event；native _collect在主路径抛tool_cancelled，先经finally native killpg+wait并收尾输出，再由supervisor生成可信tool_cancelled回执、正常退出关闭lease。当前collector在管道耗尽后还有proc.wait(timeout=remaining)，所以Event不能只在selector循环检查：无管道但进程仍活着的等待也须能观察取消并进入cleanup。watch线程不能os._exit、close lease或绕过finally。Runner可能已死、worker可能被杀，故supervisor须独立处理EOF，不能只依赖tool worker finally。外层worker要等supervisor完整回执，不为抢先响应而杀supervisor。
4. **可信回执才表示此域静止。** 父端收到原有监督链返回的、清理之后生成的tool_cancelled，并确认worker结束，才标local_quiescent；native stdout中恰好出现同名字串不算。没有完整可信回执、worker丢失、清理失败或原deadline到期，仍是unknown，既有execution lease/代际门负责阻止未静止时的新worker，不增加任意PID探测协议。不得unlink/替换lease文件或LOCK_UN共享description，也不能只凭kill成功/worker退出把未知效果改为已解决。
5. **结果优先与未知不混。** 若完整合法结果/receipt已经取得且该执行确实结束，保留其真实结果，再应用stop；不能晚到cancel把confirmed success重标成未执行。反之取消/owner死亡/worker丢失发生在可能mutating效果之后，纵使本地进程已静止，仍标UncertainEffect、保留pending/patch_inflight，不能自动补做recover或用not_started闭合。read工具可报告cancelled，但控制域清理仍需成立。

HTTP worker的os._exit范式只适合它自己的单层模型transport域。supervisor安全的lease释放顺序，是这一增量必需的新机制；其他现有保护保持原样，不引入新后台daemon、shell/fork权限或新预算规则。

## 需要的新验证（尚未执行）

- **实际native child取消**：合成run_tests与run_command写started→等待→late marker；stop发生后worker/supervisor/native退出可查，迟到marker不出现，execution lease仅在native已静止后可重取。Git也要验证其supervisor控制传播，不能因它通常只读就漏清理。
- **worker强杀与Runner强杀分别做**：让真实supervisor持lease、native已启动，再杀自己创建的worker或Runner。owner EOF独立驱动supervisor回收，下一Runner在清理完成前不得取得fence。旧“finished最终出现”样例需要新的owner契约版本，不能直接删旧证据。
- **多层group/descriptor边界**：supervisor与native分别在newsession；验native拿不到control/lease FD和凭据。python_expr/file/MCP保持原范围，不能冒称得到同样主动取消。测试仍使用既有fork-denied backend，不放宽能力以方便验证。
- **竞态/失败**：取消在probe前后、Popen启动附近、完整结果已到/尚未到、supervisor输出BrokenPipe；native关闭两个stdio后仍睡眠，取消仍应进入cleanup。注入kill/wait或cleanup确认失败时保留unknown与lease阻断，不假成功。确认清理期间stop不能重置timeout或触发新模型/工具。
- **副作用语义**：native已写partial后被取消，结果必须未知/partial已知而非rollback；reopen不得自动补写。只读cancel与确认完成结果用负/正对照区分。

这里的quiescence最多是既有trusted supervisor与fork-denied native child域；不扩展为任意OS进程树、MCP远端、原生外部API或外部exactly-once。测试通过也只完成一个运行控制增量。

## 本轮补充来源身份

| 补读源码 | Git blob SHA |
|---|---|
| [codex-rs/core/src/tools/parallel.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/parallel.rs) | `f11e64547bf41325309b38e19f2e91deaecfeecd` |
| [codex-rs/core/src/unified_exec/process.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process.rs) | `f9afaaa41d5349e519ca45585bcd05954655dd4d` |
| [codex-rs/exec-server/src/local_process.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/local_process.rs) | `e0f0a817d2930796024fff129d4b7b7d89fe087d` |

process_manager为前次已取得的固定blob `caa45622a2c826ae7a7c14c1edd2c849e8f81799`，本次仅复读上述短分支。原始摘录只加行号，Apache-2.0/Copyright 2025 OpenAI；许可/NOTICE沿用。证据已足够支持有限方案，余下工作应是实现与合成实际进程验证，不继续扩展源码考古。

资料检查：补读3个/复用1个上游文件，3组摘录逐字比对通过，19条固定源码URL/行范围有效，本地链接存在；没有执行本地/上游测试或模型API。
