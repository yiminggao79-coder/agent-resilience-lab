# Session discovery：列表元数据、分页与恢复边界

2026-11-08，只读研究官方 `openai/codex@e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`。先复用[session/fork研究](session-fork-source-study.md)、session/turn设计及rollout既有证据，再补下列3份新源码；只选1个关键测试体作证据。无上游执行、runtime改动、模型API、keys/ledger/私有session或checkpoint/V2读取、Git操作或host probe。此文服务未来实现，**不声称本地已有session list功能**。

**研究结论：上游将“发现摘要”与“恢复执行历史”分开，但普通列表可能主动修补元数据，并非纯只读。本地已有单一SQLite任务快照，可先做只读、有界页长、稳定cursor的标量投影；不需要复制rollout扫描/兼容索引/修补系统。**

## 来源与真实读取范围

新源码均经GitHub连接器取得固定commit；原始归属/许可见已保存Apache-2.0 LICENSE与NOTICE。只引用短摘录，不移植实现。

| 新文件 / blob SHA | 本轮实际读取定义与函数体 |
|---|---|
| `codex-rs/thread-store/src/local/list_threads.rs` / `fea544636d92e2620e2675ea985bacdf1de36597` | [list:24–120](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/list_threads.rs#L24-L120) list_threads；[list:122–233](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/list_threads.rs#L122-L233) list_section_threads；[list:235–345](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/list_threads.rs#L235-L345)路由；唯一关键测试 [section_listing_uses_sqlite_metadata_without_reading_rollouts](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/list_threads.rs#L689-L780)。其他测试不作为本轮覆盖结论。 |
| `codex-rs/rollout/src/list.rs` / `38f5174ffbe796b35d84a89883cd5ab9feffaa06` | [rolloutlist:47–85](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/list.rs#L47-L85)摘要/页；[rolloutlist:145–234](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/list.rs#L145-L234)sort/cursor/AnchorState；[rolloutlist:303–343](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/list.rs#L303-L343)cursor serde；[rolloutlist:499–543](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/list.rs#L499-L543)created scan页构造；[rolloutlist:745–797](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/list.rs#L745-L797)parse与filesystem cursor。其他扫描路径仅定位，未宣称全量读完。 |
| `codex-rs/rollout/src/state_db.rs` / `439a1e62ba26cd7e87d802eba57e9919303e0e32` | [state:279–288](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/state_db.rs#L279-L288) cursor_to_anchor；[state:352–483](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/state_db.rs#L352-L483) list_threads_db完整体；[state:485–499](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/state_db.rs#L485-L499)按ID查rollout path。止于StateRuntime.list_threads边界，未展开底层SQL。 |

复用并补读已研究文件：LocalThreadStore说明/trait转发 [local:135–147](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/mod.rs#L135-L147)、[local:673–675](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/mod.rs#L673-L675)，blob `c61c125f7f721d90398372406af720bbe00e4b1d`；RolloutRecorder的 [recorder:536–802](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/recorder.rs#L536-L802) list_threads_with_db_fallback，blob `8faee09898de60d63f7873ae866ba767ac7da6eb`；ThreadManager legacy resume [manager:1479–1503](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L1479-L1503)→[manager:1767–1783](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L1767-L1783)，blob `8cbc7bc9b233503d303fc97f84c0f1b9fc312e47`。这些是已有文件的新读取范围，不是重复通读整份源码。没有继续追CLI picker/app-server入口或恢复全部runtime。

## 原版事实：list→metadata→cursor

1. **LocalThreadStore是存储入口，不是活线程列表。** trait list_threads转发local/list_threads。普通sort先parse cursor，无效即InvalidRequest；CreatedAt/UpdatedAt/RecencyAt与Asc/Desc被明确映射。查询后转换摘要、补名称/section metadata并返回ThreadPage(items,next_cursor)。列表项有thread_id、preview/first user message、cwd等，**上游摘要本身可能包含用户正文**，不能直接当本地metadata trace公开字段。[list:24–119](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/list_threads.rs#L24-L119)、[rolloutlist:60–85](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/list.rs#L60-L85)。

[list:31–39](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/list_threads.rs#L31-L39)

```rust
31:     let cursor = params
32:         .cursor
33:         .as_deref()
34:         .map(|cursor| {
35:             parse_cursor(cursor).ok_or_else(|| ThreadStoreError::InvalidRequest {
36:                 message: format!("invalid cursor: {cursor}"),
37:             })
38:         })
39:         .transpose()?;
```

2. **分页不是offset。** Cursor保存timestamp与可选ThreadID，序列化RFC3339 timestamp或timestamp|id；parse兼容旧时间格式。descending文件扫描严格跳过已见(ts,id)，同timestamp继续较小ID；旧无ID cursor跳过该timestamp全部项。page的next cursor从最后返回项取得，另含num_scanned_files/reached_scan_cap；文件扫描上限10,000，页长与扫描量不是同一边界。CreatedAt的filesystem cursor来自rollout文件名，UpdatedAt/RecencyAt来自对应metadata，recency可fallback updated/created。[rolloutlist:145–198](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/list.rs#L145-L198)、[rolloutlist:303–343](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/list.rs#L303-L343)、[rolloutlist:499–543](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/list.rs#L499-L543)、[rolloutlist:745–797](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/list.rs#L745-L797)。稳定比较不等于整个多页查询持有数据库快照；本轮不将其推断为所有更新并发下无重复/无遗漏。

[rolloutlist:223–233](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/list.rs#L223-L233)

```rust
223:     fn should_skip(&mut self, ts: OffsetDateTime, id: Uuid) -> bool {
224:         if self.passed {
225:             return false;
226:         }
227:         if ts < self.ts || (ts == self.ts && self.id.is_some_and(|anchor| id < anchor)) {
228:             self.passed = true;
229:             false
230:         } else {
231:             true
232:         }
233:     }
```

3. **筛选在明确的存储分支生效。** relation/section/project要求state DB；section-position排序也要求section，采用position-millis|thread_id cursor，直接调用StateRuntime列表后转换metadata。其余分支按archived与use_state_db_only选RolloutRecorder入口。允许的source/provider/cwd/search_term参数都传到底层；state_db包装器规范化cwd并将cursor转millisecond Anchor。[list:122–233](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/list_threads.rs#L122-L233)、[list:235–331](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/list_threads.rs#L235-L331)、[state:279–288](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/state_db.rs#L279-L288)、[state:378–445](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/state_db.rs#L378-L445)。本轮未展开StateRuntime SQL，故不声称具体LIKE、索引、NULL排序、搜索字段或query plan已验证。

4. **普通state-DB列表仍核对rollout路径。** list_threads_db核对SQLite home，循环请求剩余页长，检测压缩/普通rollout文件是否存在，缺失路径不加入items，继续next_anchor补足；错误返回None供上层选择fallback。relation路径直接返回其StateRuntime结果，section-position也有独立直接路径，不能把普通路径的文件检查推广到所有列表。[state:352–483](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/state_db.rs#L352-L483)、[list:201–232](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/list_threads.rs#L201-L232)。

## 原版事实：rollout与state的选择不是统一只读

LocalThreadStore声明：JSONL是durable replay/旧格式兼容面，SQLite是可查询metadata index；live append历史与metadata更新分开。[local:135–147](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/mod.rs#L135-L147)。本轮进一步读到函数体：

- StateDbOnly直接请求state_db::list_threads_db，失败报错（[recorder:557–576](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/recorder.rs#L557-L576)）；它省去常规filesystem-first修补流程，但仍有上述路径存在性读取，不应称完全不碰filesystem。
- 普通列表先扫描filesystem；Desc会2×overfetch。没有DB时返回filesystem页；有DB时对命中rollout做read_repair，search分支做reconcile，再查询SQLite（[recorder:582–688](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/recorder.rs#L582-L688)）。这会修补索引，**普通“list”不是纯只读API**。
- 查询后是否返回修补后的DB页或filesystem页取决于search、metadata filters与RecencyAt；DB失败可fallback filesystem。不是简单“DB总是权威”或“FS永远权威”。[recorder:689–802](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/recorder.rs#L689-L802)。本轮不继续追read-repair写入实现，也不把注释当作事务/崩溃一致性证明。

[recorder:649–660](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rollout/src/recorder.rs#L649-L660)

```rust
649:         for item in &fs_page.items {
650:             if search_term.is_some() {
651:                 state_db::reconcile_rollout(
652:                     state_db_ctx.as_deref(),
653:                     item.path.as_path(),
654:                     default_provider,
655:                     /*builder*/ None,
656:                     &[],
657:                     Some(archived),
658:                     /*new_thread_memory_mode*/ None,
659:                 )
660:                 .await;
```

唯一关键测试 [list:689–780](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/thread-store/src/local/list_threads.rs#L689-L780)：建立3个rollout并填SQLite，将其移到section，再删除第1个rollout。按SectionPosition、Asc、page_size=2、DB-only查询，仍返回前2个thread ID，next_cursor为第二项position|ID；用cursor再得第3个、next=None。**这证明该section分支可以仅凭metadata分页，不证明条目可resume，也不证明普通列表都保留缺失rollout。测试体已读，未运行上游。**

## 原版事实：列表不是恢复授权或历史副本

所读legacy resume链：resume_legacy_thread_from_rollout→legacy_initial_history_from_rollout_path→ThreadStore.read_thread_by_rollout_path(include_archived=true, include_history=true)→stored_thread_to_initial_history→resume_thread_with_history。[manager:1479–1503](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L1479-L1503)、[manager:1767–1783](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/thread_manager.rs#L1767-L1783)。新paginated历史另有load_latest_model_context路径（这里只引用该入口注释，不展开新机制）。摘要preview不是完整history，SQLite索引行不是运行进程/approval/效果确认；列表操作不能隐式触发resume。恢复仍需要实际历史读取及原有配置/工作区/运行所有权校验。后一句是**本地应保留的工程选择**，不是声称已逐层验证上游全部恢复检查。

尚未核实：CLI picker具体默认sort/filter、resume --last入口、state层SQL/索引/limit+1实现、ThreadItem到StoredThread全部字段映射、名称兼容索引、完整history/turn分页读取。本轮证据已经足以选只读本地列表，不以补全这些支线为前提。

## 本地独立建议（未来任务，未实现）

本轮只读Store代码的构造器/save schema/load/turns、public_status及CLI已知ID入口，没有读取实际私有数据库。当前tasks表仅id+state JSON，events有task_id/seq/at，turns记录按task ID读取；缺少list查询。Store.__init__会mkdir/create/chmod/建表迁移，**仅增加一个不写SQL的Store.list方法，还不能使CLI入口成为真正只读**。

建议第一步范围：

- `sessions`或`list`明确指定已有state DB，以独立readonly连接路径打开，缺失DB明确报错/空结果（由Root选定），不创建目录/数据库、不chmod、不迁移、不获取workspace execution lock、不启动模型/MCP/tools。sqlite mode=ro/query_only与URI转义、符号链接策略沿现有路径约束做小范围实现验证；不要复用会写的构造器后称只读。
- 返回有界条目（例如默认20、最大100）：task_id/session_id/current turn_id、phase/reason、created_at及可用的workspace标识。默认不返回目标正文、preview、messages、reasoning、tool output、完整model配置；用户要查看的私有history另走明确入口。公开metadata trace契约不因列表而放宽。workspace路径是否默认输出应由CLI隐私契约决定，不能照搬上游cwd。
- 首版只支持明确命名的稳定sort（建议created_at DESC + task_id DESC）与同一排序的keyset cursor；phase/workspace用参数化筛选。created_at通常不可变，避免把每次event活动强装成“会话最后更新时间”；若后续需recency，定义并使用已提交event时间/seq，而不是filesystem mtime或虚构completed时间。timestamp相同时必须比较ID。cursor携带版本、sort/filter绑定和最后键，严格校验；不声称跨页快照一致性。
- 在同一SQLite read transaction内查询页及next cursor，用limit+1确定是否有下一页；不要先LIMIT再在Python滤phase而造成空页/漏项。可以先做现有JSON标量投影，无需维护第二套索引或扫描rollout；这限制返回大小，不自动限制全表排序/JSON解析成本。若真实数据规模证明需要，之后再加小的持久metadata列/索引，不能先称已实现O(page_size)。老记录时间/字段缺失或损坏须有明确行为，不能在列表中修复历史、补造时间或吞错后误称结束。
- 列表输出只帮助复制task_id到既有status/turns/resume；不增加“直接运行第一项/最后一项”隐式执行，不根据phase推断native已静止或可安全重放。既有resume的model/toolset/workspace/unknown/quarantine门保持原路径。

验收只需有限synthetic SQLite：多项/同时间戳分页、筛选与cursor绑定、非法cursor/limit、旧字段/损坏JSON、缺失DB无创建、只读查询不改schema/权限/行或触发tools，以及结果无private markers；并发新项/更新只按选定分页契约测试，不复制上游repair体系。上述均为研究者建议，未作为Root已选设计或已实现功能。到此停止源码扩展。

