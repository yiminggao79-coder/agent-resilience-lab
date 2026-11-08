# 终结时未完成工具调用：history 闭合源码补证

参照固定 [`openai/codex@e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`](https://github.com/openai/codex/tree/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9)。本次复用 semantic-context-design 对 normalize/history 的既有定位，补读4个直接文件的函数体与下列测试体，未重复取消全栈。GitHub连接器为源码来源，未运行上游或模型API，不读key/账本/私有checkpoint/V2，不改runtime。

本地读取时分支 `codex-reconstruction`，HEAD `8e6803283eba97ec447a19a8e1e686e7eb7eddcc`；模型取消等未提交工作与正在进行的fullsuite均保留，不启动重复整体验证。

**结论**：固定上游在构造模型请求时，对history副本规范化：缺少的function输出补 `aborted`，放在原call之后；随后处理孤儿output。它能在“旧call→下一turn用户消息”之间补齐协议项，但这首先是**prompt投影合法性修复**，不等于终结时已持久确认工具未执行。我们已有持久副作用状态和严格continue门禁，应只闭合可证明未启动的planned/queued，不能用合成aborted把running未知效果解锁。

## 已读真实调用链

1. `run_sampling_request` 使用初次input快照，或retry时clone_history；接着调用 `history.for_prompt`。[turn.rs1938–1953](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L1938-L1953)。
2. `ContextManager::for_prompt(self)`→`for_prompt_annotated(mut self)`→`normalize_history`；ContextManager为Clone，items是共享Arc向量，修改时Arc::make_mut。这里消耗的是请求视图，不要求把合成项追加回canonical rollout。[history.rs103–110](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history.rs#L103-L110)、[708–735](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history.rs#L708-L735)。
3. `normalize_history` 先 `ensure_call_outputs_present`，后 `remove_orphan_outputs`，再做不支持媒体处理。[history.rs1170–1186](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history.rs#L1170-L1186)。
4. `ensure_call_outputs_present` 先收集全history已有output call_id；逐call检查缺项，构造合成output并逆序插在call后。普通FunctionCall补FunctionCallOutput文本aborted；CustomToolCall/LocalShellCall也有补项，但非inherited缺项会经过error_or_panic；ToolSearchCall是特定空tools完成项。[normalize.rs21–154](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/normalize.rs#L21-L154)。
5. `synthetic_output_id` 由source item ID与固定namespace/prefix派生UUIDv5；源码明确它是prompt-only output，可重复规范化而不持久化，用于retry/resume稳定性。[normalize.rs156–168](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/normalize.rs#L156-L168)。若原call无item ID，保留legacy None行为。

原始行摘录（仅加行号，Apache-2.0；LICENSE/NOTICE已保留）：

[codex-rs/core/src/context_manager/history.rs:721–735](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history.rs#L721-L735)

```rust
721:     pub(crate) async fn for_prompt_annotated(
722:         mut self,
723:         input_modalities: &[InputModality],
724:         image_store: &dyn AttachmentStore,
725:     ) -> Vec<ResponseItemEnvelope> {
726:         self.normalize_history(input_modalities);
727:         replace_unresolvable_image_files(
728:             Arc::make_mut(&mut self.items)
729:                 .iter_mut()
730:                 .map(|envelope| &mut envelope.item),
731:             image_store,
732:         )
733:         .await;
734:         self.project_file_audio_for_prompt();
735:         Arc::unwrap_or_clone(self.items)
```

[codex-rs/core/src/context_manager/normalize.rs:53–67](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/normalize.rs#L53-L67)

```rust
53:             ResponseItem::FunctionCall { id, call_id, .. }
54:                 if !function_output_ids.contains(call_id.as_str()) =>
55:             {
56:                 info!("Function call output is missing for call id: {call_id}");
57:                 missing_outputs_to_insert.push((
58:                     idx,
59:                     ResponseItemEnvelope::new(ResponseItem::FunctionCallOutput {
60:                         id: synthetic_output_id("fco", id.as_deref()),
61:                         call_id: Some(call_id.clone()),
62:                         name: None,
63:                         namespace: None,
64:                         output: FunctionCallOutputPayload::from_text("aborted".to_string()),
65:                         internal_chat_message_metadata_passthrough: None,
66:                     }),
67:                 ));
```

[codex-rs/core/src/context_manager/normalize.rs:150–168](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/normalize.rs#L150-L168)

```rust
150:     // Insert synthetic outputs in reverse index order to avoid re-indexing.
151:     for (idx, output_item) in missing_outputs_to_insert.into_iter().rev() {
152:         items.insert(idx + 1, output_item);
153:     }
154: }
155: 
156: /// Derives a stable ID for a prompt-only output from its source call's item ID.
157: ///
158: /// Prompt normalization can run repeatedly without persisting its synthetic
159: /// outputs, so the namespace and name format must remain stable across retries
160: /// and resumes to preserve prompt-cache reuse. Returning `None` when the source
161: /// call has no ID preserves the legacy behavior for older history items.
162: fn synthetic_output_id(prefix: &str, item_id: Option<&str>) -> Option<ResponseItemId> {
163:     let source_id = item_id.filter(|id| !id.is_empty())?;
164:     let name = format!("{prefix}:{source_id}");
165:     Some(ResponseItemId::with_suffix(
166:         prefix,
167:         Uuid::new_v5(&SYNTHETIC_OUTPUT_ID_NAMESPACE, name.as_bytes()),
168:     ))
```

### 请求配对不等于执行确认

**上游事实**：这个normalizer没有查询worker、lease、进程、文件hash或外部服务。它凭缺少output生成协议占位。remove_orphan_outputs对有call_id却无对应call的函数/custom输出标记错误并移除；有name而无call_id的外部输出可独立存在，server tool-search输出有特例。[normalize.rs171–241](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/normalize.rs#L171-L241)。因此不能把“所有孤儿项都应删除”当作跨协议通用规则，也不能将其debug断言路径概括为任何场景均静默修复。

**推断**：下一请求可以有合法的call/output形状，同时原始归档仍有缺项；canonical副作用状态是否resolved是另一层。此补证没有证明上游每一种time/error/abort终结都会先持久合成结果。已有 [取消研究](cancellation-source-study.md) 中的interrupted marker只描述中断语境与终结事件，也不能替代匹配每个tool_call_id的output。

## 关键测试体（只读，未运行）

- [history_tests.rs2998–3035](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history_tests.rs#L2998-L3035) `normalize_adds_missing_output_for_function_call_inserts_output`：只给一个FunctionCall，规范化后保留call并补同call_id的aborted输出。
- [L3059–3107](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history_tests.rs#L3059-L3107) `for_prompt_assigns_stable_id_to_synthetic_output_without_reordering_history`：输入call后直接接“later turn”用户消息；两次新快照投影相等，输出排列为call→synthetic output→user，且合成ID有fco前缀。这正是下一Turn合法性所需的形状证据，测试不证明canonical被写回。
- [L2742–2784](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history_tests.rs#L2742-L2784) custom缺项覆盖inherited情况；[L3149–3165](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history_tests.rs#L3149-L3165) 非inherited custom在debug期望panic。不能概括为所有call类型无条件补齐。
- [L2843–2873](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history_tests.rs#L2843-L2873) 孤儿output移除测试；[L3039–3055](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history_tests.rs#L3039-L3055) 保留named/no-call_id外部输出。它们限定修复范围，不认证任意坏历史可继续。

本研究只确认这些函数/测试体，不报告上游测试通过，也不声称因此我们的Chat/Responses/Anthropic线格式已获验证。

## 当前本地原因与最小独立选择

只读观察 engine.py：stop路径已用 `_outstanding_calls` 校验canonical未闭合call顺序，再给已知未启动pending/queue添加stopped_before_execution；但通用 `_stop` 只设置phase/reason并保存。time_limit/error_limit/普通abort等路径可能把planned/queued留在终结snapshot；continue_turn先拒绝pending/tool_queue，再拒绝未配对call。**门禁拒绝是正确防护，缺的是安全终结路径的真实“未执行”反馈**，不是应该放宽continue校验。

建议复用stop已有配对/候选快照逻辑，统一到所有**已解决终结**，不新造prompt-only修复层：

| 可证状态 | 本地闭合选择 | 仍保留的约束 |
|---|---|---|
| queue里已被模型接受但未启动call，或pending.status=planned且无执行/未知标记 | 验未闭合ID严格等于pending+queue顺序，给每个剩余call持久追加明确not_executed结果，error/reason反映time_limit、error_limit或user_abort等真实终结原因；清这些已闭合pending/queue。 | 不执行工具，不生成success，不消费model step/工具attempt，不删除已确认结果；call ID仍consumed。不能根据“内容未变化”反推未启动。 |
| 完成了一部分批次，后续尚未启动 | 已有结果保持原样，只闭合未启动后缀；整批按canonical顺序完整配对。 | 不向真实成功结果补第二份输出，不改旧结果/事件，不为了完成批次运行余项。 |
| apply_patch_text已有confirmed prefix且patch_inflight=false | 复用已确认patch receipts与cursor给一个明确partial/remaining_steps结果，再闭合剩余未启动calls。 | 不宣称未执行整个patch；不丢掉部分效果，不补做剩余写。 |
| running、patch_inflight、未知效果/未确认模型结果、身份不明或历史顺序不一致 | 保留pending/unknown/原始history，needs_confirmation或现有保守失败；continue仍拒绝。 | 不能合成“未执行”或清unknown来通过API门禁；不调用可能补写的recover作为normalization。 |

**事务与边界**：先建立candidate并严格验证ID/状态；tool反馈追加、清已知未启动项、终结phase/reason、turn记录与事件同一个Store.save提交。提交失败仍保留之前的可恢复状态；重复终结不能再追加同call结果。保留原终结原因与usage/cost未知，不把所有原因改成user_stop，不重置预算。新增helper应是现有stop闭合逻辑的复用，不要复制两套分支。

**Next Turn**：只在canonical确实完整配对、没有未决效果、pending/queue已清且终结resolved之后允许现有continue_turn；将新user追加到这些明确failure输出之后。上游prompt合成并不授权我们绕过这些门禁。旧checkpoint若要修复，只能在同样可证未启动且身份/历史校验通过时有明确迁移记录；不要泛化修复未知旧任务或改写历史验收证据。

**Provider一致性**：保留原assistant的native/reasoning字段；新增canonical tool failure必须由现有adapter映射成正确wire项。需要本地HTTP契约确认Chat/Responses/Anthropic下一请求形状；不能只看Python messages配对就宣称所有provider接受。摘要/上下文投影仍保留完整批次边界，不能把新failure再解释成已发生效果。

## 足够的小验收建议（未执行）

1. time_limit发生在模型batch已接受但首次工具未启动；error_limit发生在首个工具失败且还有队列；普通abort发生在planned阶段。每次验证工具无新效果、history有同ID/正确原因的失败后缀、原事件/结果不变，continue_turn成功且新请求配对完整。
2. 一部分批次/patch prefix已确认的终结：前缀回执保留、剩余not_executed，不重复执行；重复abort/stop/terminal-save不重复反馈。
3. running/patch_inflight/unknown/坏ID顺序作为负对照：不得清pending、不得用synthetic反馈解锁continue；不补写。Store提交失败后重开核验闭合与终结没有半提交。
4. 用现有本地HTTP契约检查下一Turn的实际wire请求（尤其Responses native call/output与Anthropic tool_result），不是付费模型实验。

## 四个直接来源身份

| 源码 | Git blob SHA |
|---|---|
| [codex-rs/core/src/session/turn.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs) | `1fdf62faca3d396b4f1b6b7c64d879f99be7b2e9` |
| [codex-rs/core/src/context_manager/history.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history.rs) | `3e8221ba33b70690fb1641fdae6d7a7dbcbf35c0` |
| [codex-rs/core/src/context_manager/normalize.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/normalize.rs) | `387bc8f75a2249be67efc44584b92585b6de57bb` |
| [codex-rs/core/src/context_manager/history_tests.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/context_manager/history_tests.rs) | `334a764d00d3ff11861df258e68393910e72ae21` |

足够支持本地下一小修复：区分“请求形状修复”和“已知未执行的持久终结反馈”，复用后者闭合已解决失败；未决副作用继续保守。未扩大为取消/恢复全栈研究。

资料校验：4个直接来源、3组摘录与原始行逐字一致；20条固定源码URL/行范围有效，本地链接存在。没有执行上游或本地行为测试。
