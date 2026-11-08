# 工具输出增量流：源码边界与最小本地接入

2026-11-08。固定官方 `openai/codex@e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`；只读研究，未执行上游、未运行本地进程探测、未修改runtime/策略，未读keys/ledger/私有checkpoint/V2，未调用模型API或使用Git。复用managed command研究、现有文本增量流与[工具取消研究](tool-cancellation-source-study.md)。本轮仅补3份实现/协议源码与1份关键测试文件，止于支持显式private CLI展示的最小链；不扩PTY、stdin或后台调度。

**结论：可以独立增加private tool-output展示，但每个delta仅是执行期间的未完成输出。最终工具反馈/退出码/静止确认必须沿原可信结果通道处理。上游事件直接携带原文，不是metadata-only trace协议；本地不能把它直接接进既有Store/Engine observer。**

## 证据与读取深度

通过GitHub连接器取得固定commit的完整公开文件，读取下列函数体/定义；不是仅定位名字。源码归属OpenAI，沿用已保存Apache-2.0 LICENSE、NOTICE，此处只作短摘录，不移植Rust代码。

| 新读文件 / blob SHA | 实际读取体 |
|---|---|
| `codex-rs/protocol/src/protocol.rs` / `58e3b3cf92cc02e55a1fd61d4aac6e080a9059c7` | [protocol:3947–4024](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/protocol.rs#L3947-L4024) Begin/End字段；[protocol:4037–4056](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/protocol.rs#L4037-L4056) stream与raw bytes delta。 |
| `codex-rs/core/src/exec.rs` / `0d82a8d55d8ad33bfe2bb3ea7b3b6e048a76e3f3` | [exec:71–92](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec.rs#L71-L92)限制；[exec:272–297](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec.rs#L272-L297)capture policy/StdoutStream；[exec:855–907](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec.rs#L855-L907)append/aggregate；[exec:981–1191](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec.rs#L981-L1191)consume_output；[exec:1193–1247](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec.rs#L1193-L1247)read_output。 |
| `codex-rs/core/src/unified_exec/async_watcher.rs` / `49ae8b2cf27c7da1e8c9654e80c4ccaa5726361f` | [watcher:34–153](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher.rs#L34-L153)start_streaming_output；[watcher:158–243](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher.rs#L158-L243)spawn_exit_watcher；[watcher:245–325](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher.rs#L245-L325)Buffer/Emitter；[watcher:331–380](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher.rs#L331-L380)success output→ToolEmitter；[watcher:439–469](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher.rs#L439-L469)UTF-8边界与retained transcript。 |
| `codex-rs/core/src/unified_exec/async_watcher_tests.rs` / `5e9973c75e80cec190ba92cf15cb4d02f2d03178` | 关键fixture [test:35–68](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher_tests.rs#L35-L68) 与 [completed_output_preserves_bytes_before_subscription](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher_tests.rs#L70-L138) 的参数化测试体：early bytes在stream订阅前已被模型snapshot消费；最终ItemCompleted仍包含early+late并带exit/status。**测试体已读，上游未执行。** |

复用已保存process.rs，blob `f9afaaa41d5349e519ca45585bcd05954655dd4d`，本轮实际补读 [process:43–57](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process.rs#L43-L57) OutputBuffers、[process:129–145](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process.rs#L129-L145)初始化、[process:236–273](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process.rs#L236-L273)termination、[process:657–704](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process.rs#L657-L704)local output task/signal_exit。复用process_manager.rs，blob `caa45622a2c826ae7a7c14c1edd2c849e8f81799`，本轮复核 [manager:570–676](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L570-L676) Begin→start_streaming_output→snapshot collector。此前manifest的“完整文件已保存”不等于这些体此前已读，本轮范围在此明确补记。

## 已核实的真实链与状态/效果边界

**协议。** Begin/End按call_id关联且有turn_id；delta只有call_id、stream、chunk，chunk是任意bytes（不保证UTF-8），JSON通过base64编码。End单独携带aggregated_output、exit_code、duration、status；delta没有“完成”字段。见上述protocol定义：

[protocol:4046–4055](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/protocol.rs#L4046-L4055)

```rust
4046: pub struct ExecCommandOutputDeltaEvent {
4047:     /// Identifier for the ExecCommandBegin that produced this chunk.
4048:     pub call_id: String,
4049:     /// Which stream produced this chunk.
4050:     pub stream: ExecOutputStream,
4051:     /// Raw bytes from the stream (may not be valid UTF-8).
4052:     #[serde_as(as = "serde_with::base64::Base64")]
4053:     #[schemars(with = "String")]
4054:     #[ts(type = "string")]
4055:     pub chunk: Vec<u8>,
```

**传统exec链。** spawn后 [exec:973–976](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec.rs#L973-L976) 调用consume_output；它take stdout/stderr，各起read_output task（[exec:991–1014](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec.rs#L991-L1014)）。每次read最多8192 bytes，若提供StdoutStream，构造有正确stdout/stderr标签的ExecCommandOutputDelta→包装Event(sub_id)→await tx_event.send，然后才写入各自有界保留buf；超过展示配额仍继续read至EOF（[exec:1193–1246](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec.rs#L1193-L1246)）。retained cap与live delta quota相互独立：ShellTool有cap，FullBuffer类返回None，不能声称所有capture policy都是固定内存上限；常量EXEC_OUTPUT_MAX_BYTES指向DEFAULT_OUTPUT_BYTES_CAP，本轮未追其定义值。aggregate不是实时合并顺序：它拼stdout再stderr，争用总cap时先保留1/3 stdout、其余stderr，再回补未用容量（[exec:865–905](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec.rs#L865-L905)）。

[exec:1217–1232](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec.rs#L1217-L1232)

```rust
1217:             let msg = EventMsg::ExecCommandOutputDelta(ExecCommandOutputDeltaEvent {
1218:                 call_id: stream.call_id.clone(),
1219:                 stream: if is_stderr {
1220:                     ExecOutputStream::Stderr
1221:                 } else {
1222:                     ExecOutputStream::Stdout
1223:                 },
1224:                 chunk,
1225:             });
1226:             let event = Event {
1227:                 id: stream.sub_id.clone(),
1228:                 msg,
1229:             };
1230:             #[allow(clippy::let_unit_value)]
1231:             let _ = stream.tx_event.send(event).await;
1232:             emitted_deltas += 1;
```

**unified exec链。** manager发Begin→start_streaming_output(process,context)订阅→另行collect snapshot供模型tool response（[manager:603–676](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L603-L676)）。local output task select stdout/stderr→同一lock下push pending与transcript→broadcast→notify（[process:657–694](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process.rs#L657-L694)）；OutputBuffers.push_chunk忽略stream，两个buffer都保存混合bytes。pending可被轮询消费，transcript独立保留，避免已取snapshot使最终事件丢early bytes。broadcast容量64（[process:129–145](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process.rs#L129-L145)），订阅不会补发订阅前bytes；关键测试证明最终completion从transcript补齐，而不是证明live delta完整。

watcher将producer chunks切成最多8192-byte事件，暂留最多3-byte不完整UTF-8后缀；malformed bytes本身仍允许原样经过，最后finish冲刷后缀。Emitter固定标Stdout（[watcher:245–325](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher.rs#L245-L325)），所以**unified路径不能保留stdout/stderr来源，不能把协议支持双流误写为每条执行路径都保真**。最终resolve_aggregated_output用transcript head/tail omission marker并lossy UTF-8解码；emit_exec_end_for_unified_exec将其放stdout/aggregate，stderr为空（[watcher:347–355](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher.rs#L347-L355)、[watcher:459–469](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher.rs#L459-L469)）。

**final gate与取消。** unified的exit_token来自process输出句柄，signal_exit也会cancel它；它不只表示用户取消。watcher在token后给100ms trailing grace，若output_closed则安全drain剩余broadcast；最后finish并notify output_drained。spawn_exit_watcher等exit_token、output_drained、可能的network monitor、interaction lock，才取exit_code/failure并交ToolEmitter（[watcher:87–152](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher.rs#L87-L152)、[watcher:179–240](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher.rs#L179-L240)）。这一层发出output-drained不代表所有历史OS后代已静止；finish_termination会abort output_task，可能丢未采集bytes（[process:236–273](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process.rs#L236-L273)）。该事实不能替代本地native lease/quarantine的可信静止回执。

[watcher:179–188](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher.rs#L179-L188)

```rust
179:     tokio::spawn(async move {
180:         exit_token.cancelled().await;
181:         output_drained.notified().await;
182:         // Deferred network denial deliberately remains observable for a short
183:         // window after process exit. Do not classify the terminal event until
184:         // that monitor has settled, even when output closes immediately.
185:         if let Some(network_denial_monitor) = network_denial_monitor {
186:             let _ = network_denial_monitor.await;
187:         }
188:         let _interaction_guard = interaction_lock.lock_owned().await;
```

传统exec单独select child.wait/expiration/Ctrl-C，取消TERM grace后kill原进程组；随后等reader drain，在部分policy以2秒drain界限abort reader并返回空stream，其他full-buffer路径可能报错或返回空stream（[exec:1024–1188](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec.rs#L1024-L1188)）。因此已显示partial与最终反馈可能不一致/不完整，不能从退出或delta推导“已完整收齐”。本轮未继续追ToolEmitter内部的item/history转换、Session.send_event持久化、channel实际容量、CLI/TUI消费；关键测试观察到ItemCompleted，不能将未读中间层伪称完整追踪。

## 大小、丢失与背压的实际含义

- **传统exec**：10,000配额在每个read_output调用的局部计数器中；stdout/stderr各有计数，不是严格两流合计10,000。每帧最多8192 bytes。达到配额不发delta，仍drain/保留cap内输出；quota耗尽没有在该体生成显式live-omission事件。await send在读取任务内，通道拥塞可能延后继续读pipe；本轮不声称其端到端无背压。
- **unified**：一个Emitter总计10,000帧、每帧≤8192 bytes，和retained head/tail cap是独立限制。broadcast subscriber落后时Lagged直接continue，无逐帧重放/缺口通知（[watcher:114–127](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/async_watcher.rs#L114-L127)）；subscribe前输出、quota后的输出均可能不展示。Emitter await session.send_event；producer通过有限broadcast与retained buffer解耦，慢subscriber可丢delta。本轮未查更底层PTY channel，不扩大为全链路背压证明。
- **边界**：delta原文字节是私有内容；base64只是编码，不是去敏。上游这些定义没有提供本地metadata trace隐私契约。progress、exit、result、静止、外部副作用确认是不同事件；stdout中出现任何“success/cancelled/JSON”字符串都不能替代可信terminal envelope。

## 本地现状与独立建议（尚未实现）

本轮只读tools.py `_dispatch/_native_worker` 与 sandbox.py `_collect` 的当前函数体：native _collect用selector每次最多8192 bytes，连续drain两流并各保留64KiB/独立truncation；owner检查和finally killpg+wait/native_joined维持原可信完成边界。外层trustedworker通过communicate聚合stdout并json.loads一次最终响应，supervisor自己的stdout也是控制JSON；**不可把native原文直接混写进去**。CLI现有 `--stream-text` 的private stderr callback与 `--events` metadata stdout是可复用展示边界（现有设计），不是已经支持tool chunks。

建议下一有限工程只为run_tests/run_command/git原生执行链加可选private回调，保持同步工具结果与既有取消/quarantine协议：

1. **独立可信通道。** 为trusted supervisor→trustedworker→parent传有界chunk记录，单独event FD或严格分帧IPC均可；复用原最终JSON结果通道最省改动的方式须先证明不会混淆。native/project仅产生stdout/stderr，绝不继承控制/event FD。chunk关联parent已知task/turn/operation/call身份与stream/sequence，parent核验身份；不从子进程原文解析控制指令。
2. **只做private展示。** 显式CLI选项（例如 `--stream-tools`）将带工具/流标签的经安全显示处理文本写stderr；callback不接Engine observer/Store metadata，不把原文复制到trace。终端控制字符须转义或过滤，UTF-8按每流增量解码，EOF才flush replacement；两流间不声称精确全局顺序。metadata若需记录，仅固定类别、计数、截断标志，禁止动态原文字段。
3. **独立有界预算。** 展示cap、frame cap、IPC cap分别限制；可以复用64KiB/stream保留值作为首版展示上限。达到展示上限或消费者落后后继续drain native、丢展示chunk并给明确“display incomplete/omitted”标记，不能使native因私有UI堵塞而停止drain。不要引入无界queue或全量raw output artifact；可信收集/owner检查/timeout必须继续运行。callback需迅速返回，已有模型callback隔离经验可复用；任意同步callback不可强制抢占的限制照实说明。
4. **最终回执仍唯一有权威。** partial只刷新屏幕，不生成tool_response、不落completed状态、不释放lease。final envelope+worker退出+native quiescence检查继续原流程；cancel/timeout/unknown须显示明确未完成，保留已有mutating unknown/quarantine。仅展示过bytes不提供副作用确认或重放许可；callback失败最多关闭展示，不能改工具执行结果或吞取消。
5. **最小验证应是真实有屏障的合成进程。** child先写/flush，只有private callback释放测试屏障后才允许child退出，以证明输出早于终结而非仅测速度；同时断言未提前commit tool result。覆盖两流、跨chunk Unicode/invalid bytes、flood cap后仍退出、stdout伪造控制JSON、callback异常/慢消费者、owner取消后join/unknown保持，以及默认无展示与metadata无原文。无需PTY/stdin/background，也不把新展示测试当取消隔离全套的重复验证。

这些是研究者建议，**未被当成Root已选设计或已实现功能**。最关键工程选择是有界trusted IPC如何在现有communicate轮询旁持续drain，不能仅在native _collect加print/回调就宣称CLI实时可见。证据已足以作该有限设计，本轮到此停止。

