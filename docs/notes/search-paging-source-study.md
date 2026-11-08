# 大型项目内容检索分页：固定 Codex 源码研究

参照 [`openai/codex@e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`](https://github.com/openai/codex/tree/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9)，2026-11-07 通过 GitHub 连接器取得真实源码，读取下列函数体和指定测试体；未编译/运行上游。只写本文，不触 runtime、密钥、账本、模型 API 或 V2 题目。

本地只读检查：分支 `codex-reconstruction`，HEAD `e6c1201af4a287aaa67156b9c554254ab1ae2fbc`，存在正在开发的 steering 等未提交变更，已保留。下面“当前实现”指此次读取的 CodingTools/discovery，不把未来增量算作已经完成。

**结论：固定版本的 file-search 是文件/目录路径的 fuzzy top-N，不是文件正文检索分页。** 已读 Core 注册路径提供通用 exec_command/write_stdin；默认提示明确优先 rg/rg --files。以 rg 命令做正文检索时，结果经过通用进程收集和字节/token 截断；没有在这条链中发现语义级 content next_cursor。我们要做的正文分页应明确标为独立实现，不宣称复刻了上游专用 grep 分页。

## 两条实际机制，不能混用

| 机制 | 已读调用链与效果 | 限制/续读语义 |
|---|---|---|
| Core 模型工具的正文搜索途径 | add_core_tool_sources→add_shell_tools→注册 ExecCommandHandler/WriteStdinHandler→exec_command schema→handler parse/prepare→get_command 将 cmd 转为 shell argv→process manager→收集输出→ExecCommandToolOutput→模型回复项。默认提示的 [L264](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/prompts/base_instructions/default.md#L264) 推荐 rg/rg --files，搜索字符串/目录/flags 由模型写在通用 cmd 中。 | exec_command 有 cmd/workdir/yield_time_ms/max_output_tokens，没有内容搜索 query/cursor schema。session_id 代表运行中进程；write_stdin 轮询它产生的新输出。输出被截掉的中间部分不会因同 session 下次 poll 自动恢复。这不是可枚举完整命中的搜索页。 |
| app-server 文件选择/补全 | SearchRequestProcessor.fuzzy_file_search→run_fuzzy_file_search→file_search::run→create_session→walker 将 relative_path 注入 Nucleo→update_query→matcher_worker→Ranking→FileSearchSnapshot。session start/update/stop 管理交互更新。 | app-server MATCH_LIMIT=50，库默认20；返回路径、score、indices、type。limit 是 top-N；query update 是新查询/重排名，不是下一页。Snapshot 内有 total_match_count/scanned_file_count/walk_complete；app-server 更新通知映射后只发送当前 files 等字段，没有 continuation cursor。 |

Core 工具注册的已读来源：[spec_plan.rs1218–1237](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/spec_plan.rs#L1218-L1237)、[1275–1305](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/spec_plan.rs#L1275-L1305)、[shell_spec.rs22–107](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/handlers/shell_spec.rs#L22-L107)。注册路径并非 app-server fuzzy search API；MCP/扩展可以额外注册工具，本文没有证明任何用户扩展都不存在搜索能力。固定 Core 的所读 handlers/registry source 中未发现名为 grep_files/search_files 的专用正文工具；源码树名称扫描只用于定位，结论还依赖下面函数体。

### A. 通用命令到输出截断

[codex-rs/core/src/tools/spec_plan.rs:1291–1305](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/spec_plan.rs#L1291-L1305)

```rust
1291:     let options = ExecCommandHandlerOptions {
1292:         allow_tty: features.enabled(Feature::UnifiedExecTty),
1293:         exec_permission_approvals_enabled,
1294:         include_environment_id,
1295:         include_windows_shell_guidance: should_include_windows_shell_guidance(context.environments),
1296:     };
1297:     if features.enabled(Feature::UnifiedExec) {
1298:         registry.add(ExecCommandHandler::new(options));
1299:         registry.add(WriteStdinHandler);
1300:     } else {
1301:         // Managed requirements are the only configuration path that can keep
1302:         // unified exec disabled. Preserve command execution without exposing a
1303:         // resumable process or write_stdin authority prohibited by policy.
1304:         registry.add(ExecCommandHandler::one_shot(options));
1305:     }
```

exec_command 的 schema 定义 shell cmd、workdir、max_output_tokens；默认描述为10000 token，且可被 policy 限制。[shell_spec.rs32–59](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/handlers/shell_spec.rs#L32-L59)。handler 在 [exec_command.rs149–178](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs#L149-L178) 解析参数与 prepare，在 [L223–240](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs#L223-L240) 分配进程 ID 并调用 manager。prepare 在 [preparation.rs242–244](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/handlers/unified_exec/preparation.rs#L242-L244) 调用 get_command，并在 [L304–315](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/handlers/unified_exec/preparation.rs#L304-L315) 把 output budget 等带入 PreparedExecCommand。

[codex-rs/core/src/tools/handlers/unified_exec.rs:166–176](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/handlers/unified_exec.rs#L166-L176)

```rust
166:     let shell = args
167:         .shell
168:         .as_ref()
169:         .map(|shell_str| get_shell_by_model_provided_path(&PathBuf::from(shell_str)))
170:         .unwrap_or_else(|| session_shell.as_ref().clone());
171:     Ok(ResolvedCommand {
172:         command: shell.derive_exec_args(&args.cmd, use_login_shell),
173:         shell: ShellInvocation {
174:             shell,
175:             use_login_shell,
176:         },
```

**事实**：这里没有替模型解释 rg regex、路径过滤或分页；cmd 是通用 shell 输入。manager 等待至 yield/completion deadline，收集 output buffer，计算包括 omitted bytes 的原 token 估计，将 captured stream 与 process_id/exit_code 组成结果。[process_manager.rs650–683](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L650-L683)、[854–871](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L854-L871)。

[codex-rs/core/src/unified_exec/process_manager.rs:678–685](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L678-L685)

```rust
678:         let original_token_count = usize::try_from(approx_tokens_from_byte_count(
679:             collected_output.total_bytes(),
680:         ))
681:         .unwrap_or(usize::MAX);
682:         let output_omitted_bytes = NonZeroUsize::new(collected_output.omitted_bytes());
683:         let collected = CapturedStream::from(&collected_output);
684:         let text = collected.text.clone();
685:         let chunk_id = generate_chunk_id();
```

收集器用 `mem::take(&mut guard.pending)` 取得当前待消费输出，累加到有界 HeadTailBuffer；这解释了 poll 是继续消费进程输出，而非再次查询已省略数据。[process_manager.rs1579–1617](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L1579-L1617)、[L1667–1690](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L1667-L1690)。

[codex-rs/core/src/unified_exec/output.rs:40–61](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/output.rs#L40-L61)

```rust
40:         let truncated = match policy {
41:             Some(policy) if self.omitted_bytes == 0 => {
42:                 return formatted_truncate_text(&self.text, policy);
43:             }
44:             Some(policy) => truncate_text(&self.text, policy),
45:             None if self.omitted_bytes == 0 => return self.text.clone(),
46:             None => self.text.clone(),
47:         };
48:         let marker = format_output_omission_marker(self.omitted_bytes);
49:         let notice = if truncated.contains(&marker) {
50:             String::new()
51:         } else {
52:             format!("{marker}\n")
53:         };
54:         if policy.is_none_or(|policy| self.text.len() <= policy.byte_budget()) {
55:             if notice.is_empty() {
56:                 return truncated;
57:             }
58:             return format!("{notice}{truncated}");
59:         }
60:         let tokens = original_tokens.unwrap_or_else(|| approx_token_count(&self.text));
61:         format!("Warning: truncated output (original token count: {tokens})\n{notice}\n{truncated}")
```

[codex-rs/core/src/tools/context.rs:526–542](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/context.rs#L526-L542)

```rust
526: impl ExecCommandToolOutput {
527:     fn model_output_policy(&self) -> TruncationPolicy {
528:         let requested_policy = TruncationPolicy::Tokens(resolve_max_tokens(self.max_output_tokens));
529:         if requested_policy.byte_budget() < self.truncation_policy.byte_budget() {
530:             requested_policy
531:         } else {
532:             self.truncation_policy
533:         }
534:     }
535: 
536:     pub(crate) fn truncated_output(&self, max_tokens: usize) -> String {
537:         self.truncated_output_with_policy(TruncationPolicy::Tokens(max_tokens))
538:     }
539: 
540:     fn truncated_output_with_policy(&self, policy: TruncationPolicy) -> String {
541:         self.raw_output
542:             .render(Some(policy), self.original_token_count)
```

模型面的 response_text 还为 header、warning 和序列化预留空间，必要时继续减小 policy；普通 tool result 与 code-mode 的输出格式路径不同，不概括为每个输出模式完全同一 cap。[context.rs571–595](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/context.rs#L571-L595)、[code-mode L508–517](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/context.rs#L508-L517)。底层 formatted_truncate_text 输出原 token 估计、原行数和中间省略标记，字节策略调用 truncate_middle_chars，token 策略调用 plan_token_truncation。[output-truncation/lib.rs24–65](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/utils/output-truncation/src/lib.rs#L24-L65)。

**推论**：模型可自行缩小 rg scope、选固定 literal、改变查询或运行另一条命令获取遗漏区间，但这没有稳定的结果集身份、树版本、位置游标，也没有本工具替它保证无遗漏/无重复。增加 max_output_tokens 或拿着 chunk_id，不等于正文分页 API。

### B. file-search 的路径索引、top-N 与 query 更新

SearchRequestProcessor 的 one-shot 输入是 query/roots/cancellation_token；同 token 新请求可取消旧请求。它返回 FuzzyFileSearchResponse{files}；session update 仅根据 session_id 找会话后 update_query，未找到返回错误。[search.rs39–78](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/src/request_processors/search.rs#L39-L78)、[81–132](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/src/request_processors/search.rs#L81-L132)。

run_fuzzy_file_search 使用固定50上限、最多12线程、compute_indices=true。[fuzzy_file_search.rs18–52](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/src/fuzzy_file_search.rs#L18-L52)。file_search::create_session 配置 Nucleo.match_paths、启动 walker/matcher 两线程；walker 注入的是相对路径列，不读取文件正文。[lib.rs175–225](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-search/src/lib.rs#L175-L225)、[walker L473–501](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-search/src/lib.rs#L473-L501)。这里的文件遍历允许 hidden/follow_links，并遵循其 ignore 配置；**不能因此放宽本项目的 visible/no-links 隔离规则**。[walker L438–457](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-search/src/lib.rs#L438-L457)。

[codex-rs/file-search/src/lib.rs:102–114](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-search/src/lib.rs#L102-L114)

```rust
102: pub struct FileSearchResults {
103:     pub matches: Vec<FileMatch>,
104:     pub total_match_count: usize,
105: }
106: 
107: #[derive(Debug, Clone, Serialize, PartialEq, Eq, Default)]
108: pub struct FileSearchSnapshot {
109:     pub query: String,
110:     pub matches: Vec<FileMatch>,
111:     pub total_match_count: usize,
112:     pub scanned_file_count: usize,
113:     pub walk_complete: bool,
114: }
```

[codex-rs/file-search/src/snapshot.rs:62–75](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-search/src/snapshot.rs#L62-L75)

```rust
62:         let ranked = snapshot.matches();
63:         if !self.rank_names {
64:             return Some(collect(
65:                 inner,
66:                 nucleo,
67:                 query,
68:                 matcher,
69:                 self.walk_complete,
70:                 ranked
71:                     .iter()
72:                     .copied()
73:                     .take(inner.limit)
74:                     .map(|matched| (matched, false)),
75:             ));
```

普通模式直接 take(limit)；优先文件名的模式用容量受 limit 限制的 heap，时间片用于让新 query/取消抢占。[snapshot.rs77–144](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-search/src/snapshot.rs#L77-L144)。Ranking.next 是内部排名迭代进度，不是对外可用的结果 cursor。matcher 收到 QueryUpdated 后清空 ranking，reparse query；不接收外部 offset。[lib.rs540–550](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-search/src/lib.rs#L540-L550)。

[codex-rs/app-server/src/fuzzy_file_search.rs:178–197](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/src/fuzzy_file_search.rs#L178-L197)

```rust
178:         let query = {
179:             #[expect(clippy::unwrap_used)]
180:             self.shared.latest_query.lock().unwrap().clone()
181:         };
182:         if snapshot.query != query {
183:             return;
184:         }
185: 
186:         let files = if query.is_empty() {
187:             Vec::new()
188:         } else {
189:             collect_files(snapshot)
190:         };
191: 
192:         let notification = ServerNotification::FuzzyFileSearchSessionUpdated(
193:             FuzzyFileSearchSessionUpdatedNotification {
194:                 session_id: self.shared.session_id.clone(),
195:                 query,
196:                 files,
197:             },
```

**事实**：它是逐步更新 top-N 的实时路径搜索；旧 query 的 snapshot 会被过滤。total_match_count 是库 snapshot 统计，不代表 app-server 提供剩余页。本次没有发现“file-search 正文分页”调用链，不能据路径 crate 名称臆造它。

## 已读上游测试（全部未运行）

- [file-search/lib_tests.rs51–91](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-search/src/lib_tests.rs#L51-L91) `matching_filenames_survive_the_path_result_limit`：大量同名目录不应挤掉匹配文件，limit1/2 仍选正确路径；证明 top-N 排序取舍，不证明完整分页。
- [lib_tests.rs225–273](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-search/src/lib_tests.rs#L225-L273)：扫描完成前可发送 snapshot；walk 完成后 query alpha→beta 可继续更新。这是异步新查询，不是 page2。
- [truncate_tests.rs119–165](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/utils/output-truncation/src/truncate_tests.rs#L119-L165)：字节/token 超限输出中间截断和 warning、保留原行数，UTF-8 不损坏。
- [head_tail_buffer_tests.rs6–22](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/utils/head-tail-buffer/src/head_tail_buffer_tests.rs#L6-L22)：12 bytes 装入10-byte buffer 只保留头尾，中间2 bytes 省略；[L60–77](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/utils/head-tail-buffer/src/head_tail_buffer_tests.rs#L60-L77) 检查跨 chunk 的头尾策略。

这些是源码测试体证据，不是本地实现通过，也不是上游在本机执行结果。process_manager_tests 仅取得并定位了文件，未把其无关测试计为本轮的截断测试证据。

## 本地真实差距与可复用机制

当前 CodingTools 的旧 search_files：

- 先调用 `_visible_files`，大于200文件或文件名总字节超限即整体失败；depth32/entries10000 有硬边界。
- 每个文件通过原 `_bytes` 读取最多64KiB；过大或无法 UTF-8 解码的文件进入 skipped_files；成功读取的累计内容上限1MiB。
- literal 匹配，返回1-based line；结果超过200或响应超限即 search_too_large；没有下一页。一次文件名枚举失败会在找正文之前结束。

已有 discovery.py 的 `_scan` 可安全遍历更多文件、规范排序、metadata tree fingerprint、root device/inode、目录 fd/O_NOFOLLOW 检查；execute_discovery 的 cursor 绑定 root/prefix/limit 与 tree，再按文件 offset 返回页，并把完整 JSON worker envelope 和换行算进64KiB。上限仍为10000 entries/depth32/8MiB metadata，不是无限大项目索引。源码头也明确 metadata fingerprint 检测观察到的变化，不构成敌对文件系统快照。

本地测试已读未运行：test_discovery_pages.py 包含242文件全页重建、stale/参数绑定/跨workspace、链接与特殊文件、实际worker roundtrip和序列化换行边界；test_coding_tools.py 保留旧 literal/limit 契约。下一项增量应复用这些机制，不重写稳定文件扫描，也不悄悄改变旧 search_files 的返回契约。

## 当前主执行Codex已选择的实现（独立本地设计，研究时尚未验收）

主执行Codex已在 `search-paging-design.md` 选择更简单的一致性取舍：每页在既有上限内重扫，hash 全部 eligible 内容并结合前后 tree fingerprint；按匹配行 offset 续页，只保留 offset..limit+1 的有界窗口。单文件2MiB、总成功读取8MiB，超限明确失败；每页成本 O(total eligible bytes)，不持久化全文索引。该选择提供 total_matches，正文以最多512 codepoint 的匹配周边 preview 返回，明确标识截断，避免巨大单行吞掉整页。

query/prefix/limit/root 与 content+tree fingerprint 绑定游标；重复扫描的有界代价换取较简单的完整范围一致性。这里是**已选设计**，不是本文研究证明已实现或通过验收的能力，也不是 Codex 自带 API。下节文件/行进度方案是研究者的替代建议，没有替换主执行Codex设计；实现与验收以已选设计为准。

## 研究者的替代建议：文件/行进度（未采纳，不是上游事实）

建议新增 `search_files_page(query, prefix="", limit=100, cursor=None)`，继续 literal、区分大小写、只搜索 visible UTF-8 regular single-link 文件。保持旧 search_files；只注册一个新有界 read 工具。不增加全文索引、regex DSL、shell/fork 权限或新的后台服务。

| 决策 | 最小语义 |
|---|---|
| 枚举复用 | 调用 discovery 的安全 scan，取得 canonical paths、tree fingerprint、root identity；不经过旧 _visible_files 的200文件门限。只对确实需要的 scanner 导出/参数做最小复用，不新造树索引抽象。 |
| 文件读取 | 复用现有 fd-relative `_parent_directory/_read_at` 的 no-links、inode/mtime/ctime 前后核验。可沿用 read_lines 的2MiB单文件上限，提高旧64KiB可搜索范围；binary/invalid UTF-8/超大文件明确带原因跳过。文件在读取中变化则失败/restart，不能混入旧游标结果。 |
| 排序和 cursor | 版本化 cursor 绑定 root/path identity、query 原文或其 hash、prefix、limit、搜索策略版本、tree fingerprint，以及 next_file_index/next_line。cursor 是继续位置，不是 shell argv、任意路径或授权。跨query/prefix/limit/workspace拒绝；树内容/metadata观测变化 cursor_stale。 |
| 按进度扫描 | 逐 canonical 文件/行前进，到匹配数、响应大小或本页内容扫描预算就停，next_cursor 指向**尚未处理的下一行/文件**。不使用“匹配结果 offset”反复扫描所有旧正文，不为拿总命中数先搜完整个项目。单页可读 bytes 与 worker deadline 均固定有界；例如2MiB/文件、8MiB/页是可选起点，属于本地配置选择。 |
| 空页 | 没有命中但本页已用完扫描预算，允许 matches=[]、eof=false、next_cursor 非空，且游标必须严格前进。模型要继续直到 eof；不能把空页等同全项目无匹配。 |
| 命中页 | 返回 path、1-based line、text、next_cursor、eof；页内 scan/skipped 统计只代表本页，不伪造 total_matches。同一行出现多次 literal 仍只返回该行一次，与旧工具一致。 |
| 响应 cap | 将 `{'result': result}` 的实际 JSON UTF-8 + worker newline、cursor、skipped metadata 一起计入64KiB。下一命中放不下时在该行之前分页，不能先推进 cursor 再丢它。单个匹配行连 envelope 都放不下则明确 line_too_large/response_too_large；第一版不需要全文长行分块。 |
| coverage | EOF 表示候选扫描结束，不表示跳过文件也被搜过；每页 skipped_files 带原因且自身有界，必要时在 skip 记录之前停页，不能用无限 skipped 列表撑破输出。报告 coverage_limited 或明确 skipped 数；不隐瞒过大/二进制文件。 |
| stale 的边界 | metadata cursor 是项目常规变化检测，不是原子跨页内容快照、恶意 mutation 防护或全文语义完整性保证。每页读之前/之后做现有 identity 检查；不为了证明分页而对所有正文建持久 hash/全文缓存。 |

每次实际读取不能超过单文件 cap；内容扫描预算不足以读下一个文件时可在文件前返回下一页，但必须保证新一页容得下至少一个允许尺寸的文件，否则会形成相同 cursor 无限空页。扫描超时、树扫描超限和权限错误保留明确失败；不要在尚未遍历完时返回 eof=true。返回分页是正常有界结果，真正的错误不冒充“没有命中”。

## 高价值验收建议（未执行，以已选设计调整）

1. 合成300+文件、多个页、命中在第201文件之后；按 canonical path/line 拼接所有页等于独立 literal oracle，页间无重复无遗漏。加一个>64KiB但<2MiB文件，证明旧 skipped 差距确实补上。
2. 同一文件跨页、多行、最后一页精确满额、无最终换行。若未来改用文件/行进度备选，另验多个无命中文件的扫描预算空页后仍能找到命中，cursor 前进并最终 eof；当前已选完整重扫方案不需要这种空页协议。
3. 修改query/prefix/limit、换workspace、篡改/非法/超长cursor、文件变更/删除/重命名，分别验证 invalid_cursor/cursor_stale；不让 cursor 绕过 visible/links/hardlinks/special-file 规则。
4. JSON 转义/Unicode路径、近64KiB单行和大量 skipped entries；测真实 envelope+换行上限、不吞掉下一匹配。已选方案核验 preview 列位置/512 codepoint cap/明确截断；仅文件/行备选中保留整行无法装下时明确失败的选项。
5. 文件读取中变化的实际/注入检查，以及真实 worker prepare/execute roundtrip。只新增与分页风险相关的测试，不重跑整套上游。

ScriptedModel 可测试“跟 cursor 直到 eof”的工具契约；真实模型是否会缩小 scope、读取匹配上下文并据此正确编辑，应在另一个公开开发任务验证。不要把分页 oracle 测试称为模型成功率。

## 来源与边界

下表为直接引用文件的 Git blob；文中短摘录只加行号，Apache-2.0，Copyright 2025 OpenAI；完整许可 与 上游 NOTICE 保留。

| 源码 | blob SHA |
|---|---|
| [codex-rs/core/src/tools/spec_plan.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/spec_plan.rs) | `6274bc6fb737152977881f490823782f0666c566` |
| [codex-rs/core/src/tools/handlers/shell_spec.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/handlers/shell_spec.rs) | `264a214f620c43edb1e26f13a8837c711339f25d` |
| [codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs) | `2b746b85d13e06b3a52f6f13a20d02ffc9ab373b` |
| [codex-rs/core/src/tools/handlers/unified_exec/preparation.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/handlers/unified_exec/preparation.rs) | `f9e93c9273c66e97e6c37472cf3d843f143f6653` |
| [codex-rs/core/src/tools/handlers/unified_exec.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/handlers/unified_exec.rs) | `ec7a7d24aa87a8b28d9380f2aa8338b541c18057` |
| [codex-rs/core/src/unified_exec/process_manager.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs) | `caa45622a2c826ae7a7c14c1edd2c849e8f81799` |
| [codex-rs/core/src/unified_exec/output.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/output.rs) | `d035ca0ba99b95288fb4fd2a7db2b7864219b444` |
| [codex-rs/core/src/tools/context.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/context.rs) | `252ca66b742346085517448eb00f38e26dce72c9` |
| [codex-rs/utils/output-truncation/src/lib.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/utils/output-truncation/src/lib.rs) | `c0a9fbdc58f0cb7884ae6a69fd8385a1a1b404ef` |
| [codex-rs/utils/output-truncation/src/truncate_tests.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/utils/output-truncation/src/truncate_tests.rs) | `f3e1244b82db397d57f09ecf3bcb1d7201d0da4f` |
| [codex-rs/file-search/src/lib.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-search/src/lib.rs) | `6b750d09c3d190f23e1bed45368c913177aa43e1` |
| [codex-rs/file-search/src/snapshot.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-search/src/snapshot.rs) | `a012c0eea756bc26c067c271203ee673451e3b50` |
| [codex-rs/app-server/src/fuzzy_file_search.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/src/fuzzy_file_search.rs) | `f8cd61e3ad079d51616c7901b66dba0ae3ee002d` |
| [codex-rs/app-server/src/request_processors/search.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/app-server/src/request_processors/search.rs) | `d683c6f10a87f2d3f599c710f6262fceb9a7628a` |
| [codex-rs/file-search/src/lib_tests.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-search/src/lib_tests.rs) | `d2b78b4e575dac1e6b723a3f23f73892cc1a2336` |
| [codex-rs/utils/head-tail-buffer/src/head_tail_buffer_tests.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/utils/head-tail-buffer/src/head_tail_buffer_tests.rs) | `db1defb3c439b7b91d258dc336631a1c01a31416` |
| [codex-rs/protocol/src/prompts/base_instructions/default.md](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/prompts/base_instructions/default.md) | `907ff8b877026871b088f01f4366cea36e1f02cd` |

本次追踪的是所读 Core shell 注册、命令准备/收集/格式化与 app-server fuzzy search processor→library→ranking 的短链。app-server最外层JSON-RPC dispatch、所有扩展工具、所有平台/远端executor和底层rg实现没有逐层研究；库依赖的 Nucleo/ignore 内部算法没读。不能用源码文件名查找证明全仓任何配置均无 dedicated search；可以明确说**在上述实际默认链中没有正文分页，已观察到的是通用rg命令输出截断与路径top-N**。

资料检查（2026-11-07）：8个带行号摘录块与连接器取得的源代码逐字一致，54个固定源码URL及行范围有效，本地链接存在。只是资料完整性检查；未执行本地实现、上游测试或模型任务。
