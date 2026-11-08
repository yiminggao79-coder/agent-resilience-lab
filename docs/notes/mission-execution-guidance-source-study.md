# Coding 执行指导：固定源码研究

日期：2026-11-09。固定 ref：`e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`。
本次只追踪五个相关上游文件及只读本地 `Engine.SYSTEM`，未运行 Codex、
调用付费模型、读取封存题或私有实验记录。目标是解释通用执行指导机制，不能据此
证明某次停滞的因果或预测新提示一定有效。以下“事实”指读到的源码体；
“推断”和“本地选择”不冒充上游行为。

证据清单 保存准确路径、
Git blob、行范围、提示字段指针和导出文件哈希。路径来自固定 ref 的完整树
（`truncated=false`）及已读调用体，不以文件名猜行为。

| 相关文件 | 固定 Git blob | 本次用途 |
|---|---|---|
| `core/src/session/mod.rs` | `c52dd4e9c7b6ca1e834bac631c30d015cff7c557` | 初始化选取、保存、请求副本 |
| `prompts/src/model_instructions.rs` | `a169e408b65ae96b8e8f70ca806cbe2ba8eb885e` | 模板实际解析入口 |
| `prompts/src/model_messages.rs` | `ab295566fa779d808916ed958be9848dab9c5105` | 从 captured ModelInfo 取字段 |
| `models-manager/models.json` | `489ebe3f2483348641a16160ce8491dd077eabca` | 固定目录中的实际执行指导资产 |
| `core/src/session/turn.rs` | `1fdf62faca3d396b4f1b6b7c64d879f99be7b2e9` | 普通采样请求、继续/停止状态链 |

表中路径统一以 `codex-rs/` 为前缀。许可/NOTICE另列，不计作执行机制文件。

## 资产怎样进入请求

**事实。** [session/mod.rs L914–918](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L914)
按 `config.base_instructions` → 历史保存的 base instructions →
`render_model_instructions(&model_info)` 的顺序选取。L1008–1023 将选中的文本放入
`SessionConfiguration.base_instructions`。所以用户/宿主覆盖和恢复历史可以优先于
当前目录的默认资产，不能认定每次都使用同一静态提示。

**事实。** [model_instructions.rs L8–16](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/prompts/src/model_instructions.rs#L8)
调用 `ResolvedModelMessages::from_model(...).instructions_template()`，然后直接返回
该字面文本；缺字段时警告并返回空文本，显式空文本仍保留。
[model_messages.rs L75–98](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/prompts/src/model_messages.rs#L75)
实际借用 `model_info.model_messages`，从其 `instructions_template` 取值。
这条链未把一个任意命名的 Markdown 文件当作必然执行的提示。

**事实。** [session/mod.rs L1696–1722](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/mod.rs#L1696)
从会话状态取文本与 provenance，再制作请求副本。在
`update_plan_enabled=false`、没有 model_catalog、且 provenance 是 Model 时，
调用 `without_update_plan_instructions`；其余情况保留原值。该辅助函数体不在五文件
范围内，本研究不推断它具体匹配哪些段落。代码注释明确请求副本不改变已持久化或
供 fork 继承的原 instructions。

**事实。** [turn.rs L1909–1965](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L1909)
取得请求用 base instructions，按历史投影生成输入并附带实际工具元数据，再调用
`build_prompt`。[L1842–1879](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L1842)
普通分支把这些 instructions 和当前可见工具规格放入 `Prompt`。
增量工具分支将顶层 base instructions 置空，并仅在顶层保留 hosted web 工具；
本研究没有追踪该分支的历史内 base/tool 声明注入，不能用普通分支结论覆盖它。
[L2007–2022、L2994–3011](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L2994)
沿参数把该 `Prompt` 送入 `client_session.stream`，同时给出 captured model、
reasoning/service-tier 和响应元数据。这证明真实采样入口消费该对象；HTTP字段
序列化以及 catalog 的磁盘/远端装载器在本次范围之外。

**边界。** `models.json` 证明固定快照存在相应模型字段值；若该值成为 captured
ModelInfo，以上 resolver 会选中它。未研究装载器，不能声称每种启动方式都从这份
文件加载，或远端目录永远具有相同内容。

## 实际执行指导说了什么

**事实。** 完整读到 `gpt-6.1-sol` 和 `gpt-5.5` 两个
`model_messages.instructions_template`，没有拼接它们成为一套上游提示。
[models.json L250](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/models-manager/models.json#L250)
是 sol 的单个 JSON 字符串；证据以 JSON pointer 和解码后模板行精确定位，避免把
解码行冒充源文件行：

- sol 解码 L19–21：变更/构建请求要继续推进工作至目标实现；仅回应能力、提出计划或
  提供一部分结果不足以交付。
- sol 解码 L71–73：保留原任务和接受的修正；压缩不结束任务，从已有状态继续，
  避免重做已完成的工作。
- sol 解码 L128：按改动完成适当验证；新增失败或未解问题才支持扩大/重复验证。
- [gpt-5.5 源 L1584](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/models-manager/models.json#L1584)，解码
  L145–146：有足够上下文且工作较大时给计划，检查清单随完成状态逐项更新。
  这是该模型模板的指导，不证明每个模型都带相同计划段。

导出仅含这些必要的有限片段，保留原始 JSON 子串、字符范围、解码文本；本报告用
概述说明，没有复制/翻译整份提示供本地运行。

**推断。** 计划、进度说明和完成条件在这类指导中服务于实现与验证，而不是新的任务
完成定义。上下文足够后仍只读文件可能违反这种行为意图，但模板本身没有证明
“多次读取必然错误”：新的证据、变更后的文件或失败定位仍可能需要再读。
在完整读到的这两个模板和选定代码状态链中，没有观察到按相同路径/相同参数强制
拒绝读取的调度规则；这不是对整个 Codex 仓库不存在其他机制的断言。

## 运行时怎样继续和结束

**事实。** [turn.rs L3035–3037、L3331–3337](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L3331)
将工具 future 加入队列，累积输出处理返回的 `needs_follow_up`，记录最后 agent
message；L3604–3606 的 `end_turn=false` 也要求后续采样。
L3064–3074 在响应完成且重采样输出队列清空时返回采样结果。

**事实。** [L660–690](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L660)
将模型 follow-up 与 pending input 合并。只有不需继续时才进入
[L805–855](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/session/turn.rs#L805) 的 Stop hook 路径；
hook 若提供 continuation prompt 可继续循环，否则按相应停止分支处理。
这些状态与模板相互配合，但读到的代码没有从“写文件成功”推导为“任务已完成”，也
没有把“模型不再要工具”证明为功能正确。

**推断。** 执行提示影响模型选择工具/何时结束；运行时负责调用、结果入历史与继续
条件。静态代码路径能证明提示被交给模型，不能证明它被正确遵循或解决语义停滞。

## 与本地通用 host 指导的差距

**本地事实。** 只读 `agent_lab/engine.py:23–30`，观察 HEAD 为
`73d00692d3a277fb93729266848e6185b1984d6d`。现有 SYSTEM 要求检查实际数据、依工具
结果决定动作、结束前验证、串行消费一批最多八个工具、遵守安全边界、修复可修的
参数，并禁止无证据成功声明。PROJECT_SYSTEM 处理项目/skill上下文权限。
这些已有要求有效；文字中没有明确区分“已足够理解后推进变更”和“继续无新增信息
的检查”，也没有显式描述跨 summary 继续原任务的执行目标。

**本地选择候选，未实施。** 可以在 coding profile 中独立撰写很短的通用执行约定：
识别尚未满足的要求；只读取下一步决定所需的信息；足够定位后实施已授权变更；
结果返回后更新当前进度（已有 update_plan 暴露时复用它）；无新增疑问/文件变化时
复用已有证据；完成相关验证后报告
实际结果或具体阻塞。计划不替代实现，summary不取消未完成任务。保留安全和未知
效果暂停，不硬编码读取次数、工具顺序、题意、答案或预期代码差异。

**局限。** 这是基于源机制的候选方向，不是停滞根因结论；现有条件对比仍不能证明
通知是唯一原因。必须由主线程用公开开发任务与独立新验收衡量通用行为，保留原
失败和评分。提示存在、请求哈希和工具调用数量均不能替代功能正确性。

## 许可

OpenAI Codex，Copyright 2025 OpenAI，Apache License 2.0。固定
[LICENSE](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/LICENSE) blob
`4606e72e042564097e8780d66c1d4dcb611869bd`；
[NOTICE](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/NOTICE) blob
`2805899d56d0332d175cfc613c67d45d6f006db7`。
两者原文放在本次证据目录，包括上游 Ratatui 声明。选择、JSON包装、清单和研究报告
是本地新增；本次没有将上游实现或整段提示移植到 runtime。
