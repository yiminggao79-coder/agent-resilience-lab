# Workspace freshness：外部编辑与模型上下文的边界

2026-11-08。固定官方 `openai/codex@e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`，本地研究基线 `5fd0567`（后续实现/验证另见EVALUATION）。只读研究，3个candidate文件与1个必要AGENTS loader callee；复用已读Session。没有runtime/test改动、模型API、keys/ledger/私有trace/checkpoint/V3/frozen bodies读取、Git操作或full suite。源码的存在/路径定位与函数体追踪分开，不把文件名或注释当全系统保证。

**结论：本轮没有验证到“普通workspace文件外部编辑→自动更新模型历史/summary”的通用机制。** 已验证turn diff只消费提交patch delta，watcher只交付路径通知，AGENTS指令另有缓存/加载链。这足以选择一个独立、较小的本地增量：在turn/resume边界有界重查已知读取文件，明确告知变化/不可验证并让模型重读；不重写旧history，不宣称完整workspace实时新鲜。

## 读取范围与来源身份

GitHub连接器按固定commit取得；OpenAI源码归属/短摘录沿用既有Apache-2.0许可及NOTICE。上游未执行，未读/新增测试体。

| 角色 | 文件 / blob | 真正读过的体 |
|---|---|---|
| Candidate 1 | `codex-rs/core/src/turn_diff_tracker.rs` / `06a2cd022e9c71daad1e3d3973ac2fb538255a0f` | [diff:47–115](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/turn_diff_tracker.rs#L47-L115)状态/track_delta/invalidate；[diff:184–210](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/turn_diff_tracker.rs#L184-L210)apply_change分派；[diff:212–277](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/turn_diff_tracker.rs#L212-L277)add/delete/update的baseline/current维护。其余diff渲染未全面审查。 |
| Candidate 2 | `codex-rs/core/src/agents_md_manager.rs` / `ff38ca573e3ae1f1c4a2289d1a5fc86f0866cd09` | [agents:22–50](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/agents_md_manager.rs#L22-L50)cache状态；[agents:65–161](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/agents_md_manager.rs#L65-L161)refresh完整体。 |
| Candidate 3 | `codex-rs/file-watcher/src/lib.rs` / `1e2e22f08ddfade897a35d2bdfe4aa6278d53277` | [watcher:28–42](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-watcher/src/lib.rs#L28-L42)事件/订阅定义；[watcher:113–147](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-watcher/src/lib.rs#L113-L147)recv/coalesce；[watcher:678–705](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-watcher/src/lib.rs#L678-L705)backend loop；[watcher:707–766](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-watcher/src/lib.rs#L707-L766)按subscriber交付。注册成功判断及watch匹配helpers仅定位，未沿全部调用者展开。 |
| 必要callee | `codex-rs/core/src/agents_md.rs` / `08983d72ffd1e2a17fefd2740c68164c73f79486` | [loader:59–123](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/agents_md.rs#L59-L123)load_project_instructions；[loader:132–194](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/agents_md.rs#L132-L194)read_agents_md。只追manager实际调用的读取体，不展开所有路径发现规则。 |

已读Session（blob `c52dd4e9c7b6ca1e834bac631c30d015cff7c557`）本轮复核 [session:4341–4349](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L4341-L4349)→[session:4534–4561](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L4534-L4561)，确认refresh结果进入StepContext.loaded_agents_md。止于该槽位，未把后续所有prompt fragments/history构造伪称已追踪。

## 已核实1：turn diff不是磁盘变化检测或read-version记录

TurnDiffTracker持有baseline/current内容、origin映射与内部revision/cache；track_delta只接收AppliedPatchDelta。delta非exact会invalidate，否则按patch change更新内容再refresh_unified_diff。add/delete/update的old/new内容来自delta参数，没有在这些体read filesystem。文件头的“without rereading”说明与函数体一致，但结论只覆盖该tracker。

[diff:92–105](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/turn_diff_tracker.rs#L92-L105)

```rust
92:     pub fn track_delta(&mut self, environment_id: &str, delta: &AppliedPatchDelta) {
93:         if !self.valid {
94:             return;
95:         }
96: 
97:         if !delta.is_exact() {
98:             self.invalidate();
99:             return;
100:         }
101: 
102:         for change in delta.changes() {
103:             self.apply_change(environment_id, change);
104:         }
105:         self.refresh_unified_diff();
```

其revision服务DiffCacheKey和已追踪patch内容，不是模型读过文件的SHA或磁盘mtime。外部编辑不会仅因这个对象存在就被发现；run_command修改若不提供exact delta也不能由此自动纳入。**不能把turn diff输出/存在当模型文件内容freshness证明。** 本轮未研究所有delta生产者与invalidate调用者，因此也不声称任何特定命令后的diff必然如何呈现。

## 已核实2：watcher是通知primitive，不是模型history更新器

FileWatcherEvent只含排序去重后的paths：

[watcher:28–33](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-watcher/src/lib.rs#L28-L33)

```rust
28: #[derive(Debug, Clone, PartialEq, Eq)]
29: /// Coalesced file change notification for a subscriber.
30: pub struct FileWatcherEvent {
31:     /// Changed paths delivered in sorted order with duplicates removed.
32:     pub paths: Vec<PathBuf>,
33: }
```

backend loop只接受mutating events，分发到匹配subscriber；WatchSender累积BTreeSet路径并Notify，Receiver取走路径batch。该层没有旧/新文件内容、版本hash、model messages或history写入。registration还是可失败的best-effort路径（本轮只定位其状态接口，未作丢事件保证）。

因此它可以作为某consumer的cache dirty hint，**不能因存在watcher就推导模型prompt已刷新**。本轮没有逐个consumer追踪，也没有证明普通source file read会登记watcher、通知一定被处理或重新读取结果会送模型。没有以目录中未出现read_file handler名字来证明整个Codex不存在读取/版本机制。

## 已核实3：AGENTS刷新是专门指令路径，并有明确cache条件

Session step preparation调用AgentsMdManager.refresh，再把结果装入StepContext。manager序列化refresh、可调用user/thread providers，但repository reload的本地判断是：

[agents:88–96](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/agents_md_manager.rs#L88-L96)

```rust
88:         let (mut instructions, cached, refresh_repository) = {
89:             let mut state = self.state.lock().await;
90:             let refresh_repository = state.cache.selections.as_ref() != Some(&selections)
91:                 || state.cache.active_project_trust_level != active_project_trust_level
92:                 || state.cache.project_doc_max_bytes != config.project_doc_max_bytes;
93:             if refresh_repository {
94:                 // Tightened read permissions must not leave inaccessible instructions visible,
95:                 // even if discovery fails or the caller cancels the refresh.
96:                 state.cache = AgentsMdCache::default();
```

在refresh_repository为false且user/thread instructions没变化时直接返回cached；即使provider变了，也可以复用cached repository部分再组合user/thread。project_snapshot显式存在时跳过discovery。[agents:104–137](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/agents_md_manager.rs#L104-L137)。**所读cache key没有AGENTS内容hash/mtime；不能把每次调用refresh描述成每步无条件磁盘重读。** Environment selection或provider外部可能有其他版本/失效机制，本轮未展开，不能反向宣称AGENTS永不刷新。

真的reload时：load_project_instructions先看project trust/byte budget，按已选环境取ExecutorFileSystem及sandbox context→read_agents_md→发现路径→fs.read_file→预算截断/UTF-8 conversion→携source_path/environment/cwd的InstructionEntry。[loader:59–123](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/agents_md.rs#L59-L123)、[loader:132–194](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/agents_md.rs#L132-L194)。这条链明确有disk/executor bytes到指令snapshot的过程；它不覆盖普通source文件历史，也不等于让旧summary里的文件描述自动更新。本文未追StepContext之后所有模型输入拼装，保留这个证据终点。

## 本地现状：写入冲突检查与模型认知新鲜度不同

本轮只读tools.py的read_file返回体、coding_tools.py的_read_lines、patching.py的guard检查，以及engine.py的_refresh_project_context。没有读实际任务记录：

- read_lines返回精确whole-file SHA，即使只显示一段；当前base read_file体返回ok/content，**不含SHA**。因此“所有file reads已经带版本”不能作为实现前提；一个host-owned observation hook必须知道哪些操作真正获得了稳定bytes/hash。搜索/命令自由文本结果也不能被当成可靠版本凭据。
- 已有prepared file intent和patch guards检查源bytes/mode/写入前identity等，能发现其覆盖范围内准备之后的变化。这不同于比较模型几轮前读过的版本；若模型从旧描述重新准备一个写入，不能因为本次prepare与execute一致，就称模型依据的是最新内容。
- 本地项目指导已有capture_project_context+fingerprint；变化时追加带_project_context provenance的新消息/撤销旧生成指导，保留history。这个专门指令机制与普通文件stale notice应分开，不能扫描完整archive或把模型summary当文件状态表。保留旧工具输出/语义summary是证据历史，不是最新文件缓存。

## 本地独立选择：已知读取集合的有界边界检查（未来功能）

建议只覆盖普通可见、受现有路径/读取权限约束的文件，不加全仓watcher或“所有文件已同步”承诺：

1. 在**成功的可信文件读取结果**处记录有界path/hash/观察来源与消息边界；read_lines可复用whole-file hash，read_file需要从同次稳定bytes补足，不能另一次hash读造成内容/hash不对应。不要解析任意tool stdout或模型文本来猜读取事实。未跟踪legacy session保持未跟踪，不能补造过去版本。
2. 在resolved follow-up或安全resume、下一模型请求之前，仅重新检查这份小集合，分别报告changed/deleted/unavailable/coverage_limited；限制文件数、单文件/总read bytes和输出。hash相同只证明此检查时同bytes，不证明没经历改后改回或采样之后不再变；错误/预算用完不伪装为unchanged。不因此读取hidden/private/external路径或自动扩大权限。
3. 给模型追加**private、带明确观察时点的结构化数据通知**，提示旧读取/summary可能过时，应按需重读；默认不把新整文件、diff或路径原文写入metadata Trace。保留原messages/summary和其source hash，不能在旧tool result里替换内容。路径本身也是不可信数据，通知不赋予文件名任何指令权威。
4. 通知与observation baseline更新要有清楚的persist/dedupe边界，避免restart重复通知或已消费通知丢失；模型真正重读后才更新“最近读到的版本”。**不能仅检查发现新hash就把它记成模型已读**。自身工具成功修改的结果可另记“本进程已知写入”，但不能把变化后未读bytes称observed_read；不从unknown mutating operation推断新baseline。
5. 它是认知提醒，不授写入、不解决unknown effects、不绕过edit guards/审批或恢复旧workspace。暂不覆盖Shell/MCP/搜索命中里隐含读过的文件；遗漏范围明确表述。若将来要更强“读取后才允许编辑”契约，应作为独立策略，不能将本次notice假称已强制CAS。

最小验收方向：跨turn同文件改变/删除、同size与mtime变化但bytes不同、read_lines分段但hash全文件、未改文件无重复notice、范围耗尽/权限失效明确、重启dedupe、通知先于采样、旧archive不改、metadata不含private markers；只需新合成fixture，不调用模型。Root可据现有guard/Store机制选更小具体实现，本研究不写测试或runtime。

剩余不确定性是上游watcher consumers、完整AGENTS cache失效与普通read provenance是否在其他层实现；本轮没有足够证据作全项目否定。**可执行决定已经成立：不要复用TurnDiffTracker或通用watcher当freshness；若做下一增量，先实现有限known-read boundary notice。** 到此停止源码扩展。

