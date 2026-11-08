# Whole-workspace read-only：上游机制与本地最小选择

2026-11-08。固定 `openai/codex@e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`，只读有界研究；无runtime/策略改动、上游执行、OS探测、模型API、keys/ledger/私有checkpoint/V3或freeze读取/修改、Git操作。先复用scoped read-only设计及既有体级研究、实现计划与已保存command源码；本轮只补2份新文件，不重复fork或OS后端验证。

**结论：read-only是文件系统写权限，不是“禁止运行命令”，也不自动决定审批或读取范围。本地可以将精确的host scope `.`定义为整个项目只读，保留既有较窄读取边界与workspace外私有scratch；无需复制上游全盘读取、额外权限合并或通用shell能力。以下是研究建议，不是已实现功能。**

## 源码范围及实际链

新取得并读体（GitHub连接器，原始归属OpenAI；短引用沿用已保存Apache-2.0许可/NOTICE）：

| 新文件 / blob | 已读函数体 |
|---|---|
| `codex-rs/sandboxing/src/policy_transforms.rs` / `fed7bd15b1d5ce23f56fdeb122e65ab4bef6e4ea` | [transforms:556–601](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/policy_transforms.rs#L556-L601)额外文件权限merge；[transforms:603–666](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/policy_transforms.rs#L603-L666)effective network/profile与should_require_platform_sandbox。 |
| `codex-rs/sandboxing/src/seatbelt_scratch.rs` / `ad17bb5cd9a1d060537d0abb89ef054f98d48b14` | [scratch:15–79](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/seatbelt_scratch.rs#L15-L79)scratch_access_roots完整体。没有继续展开其他临时目录机制或测试文件。 |

复用来源并明确本轮补读范围：

- 已保存protocol定义 [protocol:1250–1266](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/protocol.rs#L1250-L1266)、[protocol:1384–1434](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/protocol.rs#L1384-L1434)，blob `58e3b3cf92cc02e55a1fd61d4aac6e080a9059c7`：ReadOnly及网络开关、legacy只读根列表。
- 既有protocol permissions源码（本轮重新取固定文件）：[permissions:590–608](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/permissions.rs#L590-L608) read_only→Root Read entry；[permissions:2061–2079](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/permissions.rs#L2061-L2079)从SandboxPolicy转换，blob `d9c31241ef90d1033cf9696debe4d4b48744e0af`。不是以旧文标题代替函数体。
- 已保存orchestrator [orchestrator:155–245](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/orchestrator.rs#L155-L245)、manager [manager:316–414](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/manager.rs#L316-L414)及[manager:461–497](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/manager.rs#L461-L497)、Seatbelt [seatbelt:833–978](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/seatbelt.rs#L833-L978)、core adapter [adapter:119–197](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/sandboxing/mod.rs#L119-L197)；固定blob分别见command manifest。这些体已读；没有声称整份文件所有分支被研究。

可审查的短链是：legacy SandboxPolicy::ReadOnly→FileSystemSandboxPolicy::read_only→受限Root Read entry；执行时PermissionProfile→effective_permission_profile→PreparedLaunchPolicy::resolve→SandboxManager的macOS分支→to_runtime_permissions→Seatbelt resolve_for_enforcement→write/read/scratch规则→/usr/bin/sandbox-exec包住实际argv→core保留permission_profile执行。配置如何把所有legacy/profile选项装入PermissionProfile沿用旧配置研究；本轮未重追每个CLI选项。

## 已核实事实：四个独立决策

**1. 文件写权限与读取范围。** ReadOnly带独立network_access，默认false；legacy has_full_disk_read_access返回true，get_writable_roots_with_cwd在ReadOnly时返回空列表（[protocol:1384–1434](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/protocol.rs#L1384-L1434)）。转换后的read_only不是无权限或禁止exec，而是restricted的特殊Root Read entry。Root Read表示这份legacy模式读范围广，不表示本地也应暴露整个宿主文件系统。

[permissions:590–608](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/permissions.rs#L590-L608)

```rust
590: fn read_only_file_system_entries() -> Vec<FileSystemSandboxEntry> {
591:     vec![FileSystemSandboxEntry::new(
592:         FileSystemPath::Special {
593:             value: FileSystemSpecialPath::Root,
594:         },
595:         FileSystemAccessMode::Read,
596:     )]
597: }
598: 
599: impl Default for FileSystemSandboxPolicy {
600:     fn default() -> Self {
601:         Self::read_only()
602:     }
603: }
604: 
605: impl FileSystemSandboxPolicy {
606:     pub fn read_only() -> Self {
607:         Self::restricted(read_only_file_system_entries())
608:     }
```

**2. effective policy可以与额外权限组合。** 未给额外权限时clone基准；受限filesystem存在额外entries时逐个追加未重复entry；network按base或additional enabled合并。effective_permission_profile保留enforcement设置，以组合后的filesystem/network重建profile（[transforms:556–644](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/policy_transforms.rs#L556-L644)）。因此研究“本次实际权限”必须看launch effective profile，不能只看最初ReadOnly枚举。此处不追审批如何授权每个additional entry，也不声称所有显式deny都能被重授予。

[transforms:630–643](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/policy_transforms.rs#L630-L643)

```rust
630: pub fn effective_permission_profile(
631:     permission_profile: &PermissionProfile,
632:     additional_permissions: Option<&AdditionalPermissionProfile>,
633: ) -> PermissionProfile {
634:     let (file_system_policy, network_policy) = permission_profile.to_runtime_permissions();
635:     let effective_file_system_policy =
636:         effective_file_system_sandbox_policy(&file_system_policy, additional_permissions);
637:     let effective_network_policy =
638:         effective_network_sandbox_policy(network_policy, additional_permissions);
639:     PermissionProfile::from_runtime_permissions_with_enforcement(
640:         permission_profile.enforcement(),
641:         &effective_file_system_policy,
642:         effective_network_policy,
643:     )
```

**3. 审批与sandbox是不同边界。** orchestrator先从环境权限得到filesystem policy，计算requirement，分别处理Skip/Forbidden/NeedsApproval；审批通过后继续选择sandbox override与实际执行profile（[orchestrator:155–245](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/orchestrator.rs#L155-L245)）。ReadOnly不在该体被翻译成“所有命令拒绝”或“所有命令自动批准”。默认审批判定的helper内部本轮未展开，不推测其全部AskForApproval组合。

manager的Auto偏好调用should_require_platform_sandbox：managed network要求总是需要sandbox；受限网络下除ExternalSandbox外需要sandbox；网络已放开但filesystem Restricted且非full-write仍需要sandbox（[transforms:646–665](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/policy_transforms.rs#L646-L665)）。Require/Forbid是显式其他偏好；select_initial仍取决于平台可用性。transform在backend None时可直接返回原argv，在macOS Seatbelt分支则包装为sandbox-exec（[manager:316–414](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/manager.rs#L316-L414)、[manager:461–497](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/manager.rs#L461-L497)）。**不能仅凭枚举名保证所有host/override都实际隔离。** 本地当前native后端不可用即拒绝的选择继续保留。

**4. Scratch不是整个项目重新可写。** Seatbelt先resolve_for_enforcement；仅在include_platform_defaults且Process profile时调用scratch_access_roots，把其writes加入native writable roots（[seatbelt:833–856](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/seatbelt.rs#L833-L856)）。scratch helper以物理/private/tmp与/private/var/tmp为候选write roots，并将readonly/unreadable roots、read_only_subpaths、metadata限制带入这些grants；返回reads另带unreadable exclusions（[scratch:15–79](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/seatbelt_scratch.rs#L15-L79)）。这支持“进程scratch有单独lowering且受限制”，不支持“所有ReadOnly执行的任意tmp路径永远可写”。本轮没有逐个profile重算结果或运行测试。

[scratch:36–48](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/seatbelt_scratch.rs#L36-L48)

```rust
36:     // Use the physical paths: /tmp and /var are trusted system aliases, and
37:     // the normal lowerer binds both roots and exclusions in that namespace.
38:     let scratch_paths = ["/private/tmp", "/private/var/tmp"]
39:         .into_iter()
40:         .map(AbsolutePathBuf::from_absolute_path)
41:         .collect::<Result<Vec<_>, _>>()
42:         .map_err(|error| SeatbeltPreparationError::FileSystem(error.to_string()))?;
43:     let mut scratch_policy = policy.clone();
44:     scratch_policy.entries.extend(
45:         scratch_paths.iter().map(|path| {
46:             FileSystemSandboxEntry::new(path.clone().into(), FileSystemAccessMode::Write)
47:         }),
48:     );
```

最终Seatbelt分别生成file-write与file-read规则；full-read分支可allow file-read*，受限read分支用readable roots+scratch reads，写入则用writable roots及exclusions（[seatbelt:884–978](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/seatbelt.rs#L884-L978)）。base profile deny default后允许exec/fork（[base:7–16](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/seatbelt_base_policy.sbpl#L7-L16)），再次说明文件只读与进程创建不是同一能力；本项目继续deny fork，无需复制该profile。

## 本地现状（只读代码，不是新验证）

本轮读permissions.py的_parts、ReadOnlyPolicy构造/validate/check_mutation/check_native/protected_ancestors及sandbox.py的_profile/_command_profile/_run_command：

- _parts拒绝任何“.”component，所以root scope尚未支持；scope归并与check_mutation用“scope + /”比较，**只删除该拒绝还会使'.'无法覆盖普通'a.py'**。根保护必须有明确全域语义。
- 当前file guard在prepare/execute/recover检查host policy；command intent包含manifest/identity，native profile先允许root与scratch写，再为protected scopes加literal/subpath deny、祖先create/unlink、file-link及fcntl约束。这个机制已存在，root模式应复用而不是另建权限框架。
- Native test/command的HOME、TMPDIR指向每次调用的TemporaryDirectory，PYTHONDONTWRITEBYTECODE等既有环境策略仍生效；scratch应与protected root不重叠。当前创建函数没有在这里显式断言该不重叠关系，root-profile增量应明确处理。
- 现有ASCII native path限制、受保护树10000 entries/32深度上限、symlink/hardlink/special-file admission、MCP受保护模式拒绝，均在旧设计中已有；本轮不重复probe或扩大兼容范围。

## 本地最小选择与下一步验收（未实现）

1. 只接受精确scope字符串`.`作为host-only全workspace sentinel；`./x`、`x/.`、`..`等继续无效。若与子scope同时配置，要么归并为单一`.`，要么明确拒绝；确定一种规则并绑定原manifest/identity。空配置和旧checkpoint含义不变；旧scoped策略不能在resume时被悄悄替换成root策略。
2. 根模式下所有可见项目mutation paths都拒绝，包括新建/写/append/patch/temp-replace/delete/mkdir/rename两端。读取仍走现有路径约束；不增加全盘read权限、不借“只读”开放隐藏控制目录、外部路径、网络、fork或MCP。若whole-root admission沿用bounded扫描，超限清楚失败，不为支持'.'转为无限扫描或悄悄放过aliases。
3. Native规则将`.`映射成规范实际workspace root的literal+subpath写拒绝，保留既有保护和command authority再验证。root只读不替代managed command原审批流程；批准run_command只准执行该受限命令，不提供workspace写例外。
4. 私有scratch继续由可信host创建且位于workspace之外；校验不重叠，必要时选择已授权temp位置或fail closed。项目写TMPDIR/HOME可成功，向workspace写仍应失败；不通过为项目子目录加write exception来“修好”只读。scratch内容不成为metadata trace的原文出口，可信清理不算项目被授写。
5. 最小验证沿用合成fixture覆盖：file mutations拒绝但read/search/list可用；native读取/纯计算/既有不写项目的测试成功，创建/改写/删除/原子替换/metadata写在root下被拒绝，而private scratch写成功；'.'+子scope规范化、错误path、command digest/resume策略不匹配、ASCII限制与既有默认/scoped模式不回归。测试尝试向项目写cache/artifact而失败是所选只读语义，不伪报命令可用性等价。无需重跑fork/setpgid/setsid或无关OS审计。

证据已足够选择“host whole-workspace monotone restriction + existing private scratch”，本轮不补新测试片段、默认审批helper、完整profile配置或跨平台等价性研究。工程是否采用及具体规则由Root后续设计决定。

