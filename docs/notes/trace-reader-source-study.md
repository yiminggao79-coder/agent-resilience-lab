# Committed metadata trace reader：源码边界与最小本地分页

2026-11-08。固定 `openai/codex@e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`；复用已读Session、protocol、rollout与TUI证据，只补1个新文件。无runtime/CLI改动、模型API、key/ledger/实际私有trace或checkpoint/V3读取、Git操作、上游执行或新OS探测。本轮无需补测试文件：函数体足以判断“通知交付”和“已提交记录”不能等同，未做上游测试通过声明。

**结论：本地新能力应读取SQLite已提交metadata行，用task过滤与全局seq seek分页。不要将上游实时Event通道类比为可重放的durable事件日志，也不要借metadata trace重建完整Session状态。现有trace默认输出可保留，新分页入口独立接入。**

## 来源与一个有边界的真实调用链

新读：`codex-rs/thread-store/src/live_thread.rs`，blob `0c45c2ea6db3a2805d62215872a419baaffdcc6c`，[live:229–301](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/live_thread.rs#L229-L301) append_items/persist_appended_items/flush函数体。GitHub连接器按固定commit获取；源码归属与短引用沿用已保存Apache-2.0 LICENSE/NOTICE。

复用并补读：Session `c52dd4e9c7b6ca1e834bac631c30d015cff7c557`，[session:2688–2769](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L2688-L2769)、[session:2924–3025](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L2924-L3025)、[session:3046–3059](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L3046-L3059)、[session:5310–5318](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L5310-L5318)；protocol blob `58e3b3cf92cc02e55a1fd61d4aac6e080a9059c7` 的 [protocol:1521–1527](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/protocol.rs#L1521-L1527)。RolloutRecorder沿用已保存体级摘录，blob `8faee09898de60d63f7873ae866ba767ac7da6eb`；LocalThreadStore沿用此前固定源码的 [local:578–580](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/mod.rs#L578-L580)。仅列出的分支/体被读，不声称全文件研究。

本轮追到的短链：

1. emit_turn_item_started构造ItemStarted(thread_id、turn_id、item、started_at_ms)→send_event（[session:3046–3059](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L3046-L3059)）。
2. send_event把Event.id设为turn_context.sub_id，先记录若干trace辅助信号，再调用send_event_raw；还可能将同一源事件派生legacy事件再次send（[session:2722–2769](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L2722-L2769)）。
3. send_event_raw→send_event_raw_with_persistence。若persist=true，先把EventMsg包装RolloutItem并调用persist_rollout_items；之后record_protocol_event、某些realtime附加效果，再deliver_event_raw（[session:2924–2991](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L2924-L2991)）。
4. persist_rollout_items→LiveThread.append_items；失败日志后返回false（[session:5310–5318](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L5310-L5318)）。LiveThread先调用persist_appended_items，再用筛选后的items观察/同步metadata；persist_appended_items计算过滤/测量结果，却将raw_items传ThreadStore.append_items（[live:229–280](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/live_thread.rs#L229-L280)）。LocalThreadStore trait再转发live_writer::append_items（[local:578–580](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/mod.rs#L578-L580)）。**按新增文件预算止于live_writer内部；未伪称这一层已连接到每个writer实现和持久化过滤规则。**
5. deliver_event_raw更新in-memory agent_status，再await tx_event.send；channel已关闭仅debug并丢事件（[session:3018–3024](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L3018-L3024)）。本轮止于该channel，不展开app-server通知协议/重连或TUI整个dispatcher。

[session:2729–2732](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L2729-L2732)

```rust
2729:         let event = Event {
2730:             id: turn_context.sub_id.clone(),
2731:             msg,
2732:         };
```

## 已核实事实：record、flush、delivery、ID不相同

**交付不是通用commit barrier。** send_event_raw_with_persistence确实按调用顺序先append再deliver，但忽略persist_rollout_items返回的bool；在此体中，即使append记录失败，后续delivery仍可发生。常规事件路径没有无条件flush；特定private-review completion有单独flush分支，并且失败也只warn。源码只支持这些具体语义，不支持“前端收到=durably committed”的一般保证。

[session:2972–2979](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L2972-L2979)

```rust
2972:         // Persist the event into rollout storage; the store applies its persistence policy.
2973:         if persist {
2974:             let rollout_items = vec![RolloutItem::EventMsg(event.msg.clone())];
2975:             self.persist_rollout_items(&rollout_items).await;
2976:         }
2977:         self.services
2978:             .rollout_thread_trace
2979:             .record_protocol_event(&event.msg);
```

[session:3022–3024](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L3022-L3024)

```rust
3022:         if let Err(e) = self.tx_event.send(event).await {
3023:             debug!("dropping event because channel is closed: {e}");
3024:         }
```

**rollout writer有独立排队/flush与ordinal。** 已读record_canonical_items [recorder:1073–1085](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/recorder.rs#L1073-L1085)返回的是将AddItems放入队列的结果；flush [recorder:1108–1126](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/recorder.rs#L1108-L1126)另发Flush并等ack。writer [recorder:1909–1937](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/recorder.rs#L1909-L1937)在写成一项后advance ordinal，[recorder:2085–2109](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/recorder.rs#L2085-L2109)写timestamp/optional ordinal/item的JSONL并file.flush。这里未证明掉电fsync、每个通知都被保留或跨thread全局ordinal；也未将未追的live_writer路径自动等同该Recorder调用。可复用的机制思想仅是：**写入排队、明确flush、通知交付分开命名。**

**相关ID不是分页序号。** protocol Event.id注释是submission correlation ID，Session实际给同一turn的多条事件使用相同sub_id；ItemStarted中的thread_id/turn_id/item identity描述对象归属，不能拿Event.id当唯一事件ID或单调seek cursor。Rollout ordinal属于另一持久化表示，不能当本地SQLite全局seq。上游这几层也包含用户/工具原文；本地metadata-only协议不能直接复用其payload。

**前端展示状态不等于事件重放状态。** 既有TUI摘录中 [tui:88–117](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/tui/src/chatwidget/turn_runtime.rs#L88-L117)重置当轮UI，[tui:343–381](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/tui/src/chatwidget/turn_runtime.rs#L343-L381)清预览尾部、终结活跃cell/command状态。这是in-memory presentation lifecycle，不是本地metadata reader的重建算法。本轮没有研究其replay游标或历史加载，不宣称wire/TUI parity。

未核实：tx_event通道容量与完整消费者顺序、app-server sequence/notification订阅或恢复协议、live_writer过滤全部规则、所有rollout读者与ordinal兼容、observer丢失后的服务端补发。本地分页不依赖补齐这些范围。

## 本地事实：SQLite commit在observer之前

只读store.py与engine.py当前函数体，未打开任何实际数据库：

- events表的seq是SQLite INTEGER PRIMARY KEY AUTOINCREMENT，跨task共享；task_id/at/kind/meta/event_id另列。Store.save在同一SQLite事务中保存task snapshot、turn记录、event行；event_id有唯一约束用于同次snapshot/event保存重试。事务提交不是工具外部副作用事务。
- Engine._save调用Store.save返回后才触发observer，observer异常被忽略；observer payload含task/kind/phase与metadata，没有数据库seq。重试/呈现失败不能被当成完整、不重复的可靠通知流。
- Store.events(task_id)当前按task过滤ORDER BY seq，将全部行转列表；无LIMIT。meta由json.loads展开，旧接口没有reader级形状/大小验证。全局seq在某task视图有间隔是正常现象，不能把不连续误报为丢失。
- Store.open_readonly已有mode=ro/query_only连接，绕过创建/chmod/迁移，并记录连接时database file identity。新入口可复用它，不初始化Engine/model/tools，不拿workspace执行锁。它不是完整历史重建器；metadata缺用户消息、工具原文和完整checkpoint状态。

## 最小本地建议（未来设计，未实现）

1. 新events分页入口独立于原trace兼容行为，语义明确为某task的committed metadata rows。用参数化task_id与`seq > after_seq ORDER BY seq LIMIT limit+1`，返回有限items与最后已返回seq/has_more。seq来自独立列，不用at或turn_id排序。limit与after_seq严格类型/范围检查，拒绝bool/负数/超大limit。task不存在与已知task无event要有明确不同结果，可只查task存在性，避免load完整private snapshot。
2. 同一页用短SQLite read transaction；只返回提交可见的行。没有has_more仅表示该页查询时暂时无更多，不能宣告task终结或以后不会append。若要固定前缀导出，可由Root另选high-water语义；不默认声称多页共用snapshot。不同task使用global seq seek没有“漏seq必须补齐”的假设。
3. 有界不只LIMIT：对kind/meta原始字节、JSON深度/字段/标量形状与总输出设限，复用现有metadata allowlist，未知/损坏内容明确报错而非原文echo；meta不能覆盖seq/at/kind/task等独立列。不要从messages、private tool deltas、model reasoning或原始错误文本拼trace。新reader不能把当前Store.events不加验证的dict展开照搬为安全协议。
4. 初版不做follow也有实用价值。若后续加follow，需显式启用、有限等待时长/条数/输出预算、间隔poll和Ctrl-C退出；每轮只续读已发seq后的committed行，空页时不盲进cursor，不持长读事务妨碍writer。数据库替换、busy/损坏或cursor不再可信时明确停止；同一SQLite数据源内续读，不默默切库并承诺exactly-once。不要用observer通知充当唯一可靠数据源。
5. 输出是诊断/界面进度metadata，不推导pending工具可重放、OS静止或session已恢复，也不执行任何recovery。read-only连接、path/inode检查沿现有reader边界，不能创建missing DB来凑空页。

对应有限synthetic验证：task交错与seq空洞、turn边界、limit+1续页、最后页与后来append、无event/不存在task、非法参数、metadata损坏/超大及保留字段覆盖、只读无schema/权限/行变化、CLI不初始化模型/tools、旧trace输出不变。follow只有实际加入时才加有限deadline/中断/DB替换测试，不提前建设订阅框架。

以上是研究者建议，Root尚需选择具体CLI/输出边界。证据足以支持task+global seq只读分页；本轮到此收尾，不扩完整event sourcing或app-server兼容层。

