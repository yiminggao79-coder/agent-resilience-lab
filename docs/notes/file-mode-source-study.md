# 文件编辑后的权限与执行位：固定 Codex 源码研究

固定参照 [`openai/codex@e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`](https://github.com/openai/codex/tree/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9)。2026-11-07 通过 GitHub 连接器取得并阅读本文函数体及指定测试体。未运行上游、未做 OS 实验；本地实际复现由主执行Codex执行。只写此文，不修改 runtime、不读取凭据/账本/V2、不调用模型 API。

本地读取时分支 `codex-reconstruction`、HEAD `dc25ec255df0fd94a98de16bfacc32a227433c9a`；已有 search/interactive 等未提交变更，均保留。

**结论**：所读上游 update 使用原路径的 write/truncate，不是 tempfile+rename replacement，代码没有显式复制 mode。Unix no-follow 分支打开既有文件后 set_len/write，普通 rwx 执行位通常因沿用同一文件而保留，这是由具体写入机制推断的行为；没有读到专门的755保留断言。上游 Move-to 是写目的文件后删除源文件，没有 source mode 的传递或 rename，因此**不能宣称它保证新目的继承源执行位**。Add 同样没有 executable 参数。

## 实际调用链与四种操作

既有 Codex 参考矩阵 已追到 Core ApplyPatchRuntime 调用 apply_patch_with_options。本次补读底层：`apply_patch_with_options` parse_patch→`apply_hunks_with_options`→`apply_hunks_to_files`→抽象 ExecutorFileSystem。

| 操作 | 已读函数体的真实动作 | 对普通 mode 的结论 |
|---|---|---|
| Update，原路径不变 | 读原内容、derive_new_contents_from_chunks；`fs.write_file(path_uri,new_contents,WriteFileOptions{follow_symlinks},sandbox)`。 | 上游没有主动取原 mode 或 chmod；在所读本地 Unix 实现中修改同一文件，普通权限位通常沿用。不是基于 atomic replace 后恢复 mode 的机制。 |
| Add | `write_file_with_missing_parent_retry` 写目标；NotFound 才尝试递归创建父目录并重试。函数允许记录 overwritten_content，不能泛称 Add 永远只创建不存在文件。 | 新文件 mode 取决于 executor 的创建规则/umask；WriteFileOptions 不含mode。已有目标则由写入实现决定，不同于显式创建一个 executable 文件。 |
| Update + Move-to | derive 内容→写 dest（并记录已提交 delta）→remove source；失败时可能目的已写、源仍在。 | 不是 rename，也没有 copy_permissions/source-mode 参数。新 dest 不存在时走默认创建；如果已存在则修改目的文件本身。没有源执行位继承依据。 |
| Delete | 读取可得的旧文本、确认非目录，然后 `fs.remove(recursive=false,force=false,...)`。 | 删除操作无目标 mode 保留问题；delta 保存文本，不是权限元数据备份或完整 undo。 |

证据：[parse/dispatch lib.rs329–365](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L329-L365)、[apply_hunks391–416](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L391-L416)、[Add473–499](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L473-L499)、[Delete510–552](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L510-L552)、[Move583–655](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L583-L655)、[Update657–680](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L657-L680)、[parent retry765–813](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L765-L813)。

## 关键原始行摘录

以下未修改官方摘录只加行号；Apache-2.0，Copyright 2025 OpenAI。完整许可、上游 NOTICE 已保留。

### Update 与 Move 没有共用 rename

[codex-rs/apply-patch/src/lib.rs:657–670](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L657-L670)

```rust
657:                 } else {
658:                     try_write!(
659:                         fs.write_file(
660:                             &path_uri,
661:                             new_contents.clone().into_bytes(),
662:                             WriteFileOptions { follow_symlinks },
663:                             sandbox,
664:                         )
665:                         .await
666:                         .with_context(|| format!(
667:                             "Failed to write file {}",
668:                             path_uri.inferred_native_path_string()
669:                         ))
670:                     );
```

Move 的目的写入：

[codex-rs/apply-patch/src/lib.rs:593–610](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L593-L610)

```rust
593:                     try_write!(
594:                         write_file_with_missing_parent_retry(
595:                             fs,
596:                             &dest_uri,
597:                             new_contents.clone().into_bytes(),
598:                             follow_symlinks,
599:                             sandbox,
600:                         )
601:                         .await
602:                     );
603:                     let dest_write_change_index = delta.changes.len();
604:                     delta.changes.push(AppliedPatchChange {
605:                         path: dest_uri.clone(),
606:                         change: AppliedPatchFileChange::Add {
607:                             content: new_contents.clone(),
608:                             overwritten_content: overwritten_move_content.clone(),
609:                         },
610:                     });
```

随后删除源文件，且不是 rename：

[codex-rs/apply-patch/src/lib.rs:619–629](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L619-L629)

```rust
619:                     if let Err(error) = fs
620:                         .remove(
621:                             &path_uri,
622:                             RemoveOptions {
623:                                 recursive: false,
624:                                 force: false,
625:                                 follow_symlinks,
626:                             },
627:                             sandbox,
628:                         )
629:                         .await
```

### 文件写入接口没有 mode 字段

[codex-rs/file-system/src/lib.rs:71–81](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-system/src/lib.rs#L71-L81)

```rust
71: #[derive(Clone, Copy, Debug, Eq, PartialEq)]
72: pub struct WriteFileOptions {
73:     pub follow_symlinks: bool,
74: }
75: 
76: impl Default for WriteFileOptions {
77:     fn default() -> Self {
78:         Self {
79:             follow_symlinks: true,
80:         }
81:     }
```

LocalFileSystem::write_file 先按 sandbox context 路由到 sandboxed/unsandboxed executor；UnsandboxedFileSystem 再委托其底层 filesystem。所读 DirectFileSystem 分支如下。[route108–122](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/local_file_system.rs#L108-L122)、[write179–189](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/local_file_system.rs#L179-L189)、[unsandboxed423–433](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/local_file_system.rs#L423-L433)。

[codex-rs/exec-server/src/local_file_system.rs:656–669](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/local_file_system.rs#L656-L669)

```rust
656:     async fn write_file(
657:         &self,
658:         path: &PathUri,
659:         contents: Vec<u8>,
660:         options: WriteFileOptions,
661:         sandbox: Option<&FileSystemSandboxContext>,
662:     ) -> FileSystemResult<()> {
663:         reject_sandbox_context(sandbox)?;
664:         let path = path.to_abs_path()?;
665:         if options.follow_symlinks {
666:             tokio::fs::write(path.as_path(), contents).await
667:         } else {
668:             no_follow::write_file(path.as_path(), contents).await
669:         }
```

no_follow::write_file 的包装只是转发 imp::write_file：[no_follow/mod.rs96–98](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/no_follow/mod.rs#L96-L98)。Unix 的 CREATE mode=0666 只指定创建行为，没有把 source executable mode 带进来：

[codex-rs/exec-server/src/no_follow/unix.rs:195–208](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/no_follow/unix.rs#L195-L208)

```rust
195: // Opening the leaf must not truncate it or hang on a FIFO without a reader.
196: fn open_writable(directory: &OwnedFd, leaf: &std::ffi::OsStr) -> io::Result<std::fs::File> {
197:     let file = openat(
198:         directory,
199:         leaf,
200:         OFlags::WRONLY
201:             | OFlags::CREATE
202:             | OFlags::NOFOLLOW
203:             | OFlags::CLOEXEC
204:             | OFlags::NOCTTY
205:             | OFlags::NONBLOCK,
206:         Mode::from_raw_mode(0o666),
207:     )?;
208:     Ok(std::fs::File::from(file))
```

[codex-rs/exec-server/src/no_follow/unix.rs:234–248](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/no_follow/unix.rs#L234-L248)

```rust
234: pub(super) async fn write_file(path: PathBuf, contents: Vec<u8>) -> io::Result<()> {
235:     tokio::task::spawn_blocking(move || {
236:         let (directory, leaf) = parent(&path)?;
237:         let mut file = open_writable(&directory, &leaf)?;
238:         if !file.metadata()?.is_file() {
239:             return Err(io::Error::new(
240:                 io::ErrorKind::InvalidInput,
241:                 "path is not a regular file",
242:             ));
243:         }
244:         file.set_len(0)?;
245:         file.write_all(&contents)
246:     })
247:     .await
248:     .map_err(|error| io::Error::other(format!("filesystem task failed: {error}")))?
```

**事实**：此分支没有 tempfile、rename、fchmod/set_permissions；既有文件被打开并截断重写，新文件按0666创建请求与进程umask处理。**推断**：对普通既有0755/0644文件，沿用该文件的机制通常保留普通 rwx 位；这里没有给出跨OS/远端权限保证。写入特权位（setuid/setgid）、ACL、owner/group、xattrs 等行为没有被本次代码和测试核验，不能并入“完整元数据保留”。

辅助查看的 fs-helper WriteFile handler 只是转发 bytes/follow_symlinks，没有权限字段：[fs_helper.rs314–331](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/fs_helper.rs#L314-L331)。本次没有继续扩展研究 sandbox/Windows/remote backend；不能把所读本地 Unix 行为外推到所有 executor。

## 测试证据的实际范围

以下**读了测试体，没有执行**：

- [Add lib.rs857–888](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L857-L888) 断言文件内容/stdout；[Update993–1027](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L993-L1027) 断言新内容/stdout；[Move1031–1068](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L1031-L1068) 断言源不存在与目的内容。均未设置源0755或检查目标mode。
- [failed_move1073–1123](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L1073-L1123) 把**源父目录**chmod0555制造删除失败，恢复为0755；验证已提交的目的delta、源/目的内容。这是失败/部分提交测试，不是文件执行位保留测试。
- [write_error1326–1351](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs#L1326-L1351) 同样对**父目录**设置0555制造Add失败，再恢复0755；没有断言文件mode。
- [no_follow/mod.rs143–151](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/no_follow/mod.rs#L143-L151) 检查 BoundOutput 到真正 write 时才截断，断言内容而不检查mode；它也不是 apply-patch mode 测试。

另外取得 file_update/file_update_tests/text_file 与 regular_file 源码用于定位和确认内容变换/普通文件打开，未把无关测试计为 mode 证据。上述已读体中没有“update/add/move 全部保留mode”测试依据；这不等于声称全仓绝不可能另有测试。

## 本地小修复建议：保留现有原子写入机制

当前 Tools.write_file 的 prepare 记录 before/desired 内容hash；execute 用 mkstemp→写临时文件→fsync→os.replace→目录fsync。临时文件没有复制既有目标mode。单文件 apply_patch 别名以及 patching.py 的 Update/Move目的步骤也构造 write_file intent；当前回执/恢复仅检查内容hash。这个代码观察需要和主执行Codex实际0755→0600复现一起作为修复依据，不能用上游成熟度取代复现。

建议保持现有 atomic replace、hash对账、symlink/单链接/只读策略；**不要为了模仿上游原地truncate而撤掉已有原子内容写入保证**。最小变更围绕同一 write intent 扩充普通权限元数据即可：

1. 对既有regular单链接目标，从与读取原内容一致的安全文件句柄取得普通permission bits；prepare 冻结 before_mode 与 desired_mode，明确定义仅0777普通rwx位。当前新建文件继续私有0600，不增加通用chmod/任意mode工具，不复制setuid/setgid/sticky等特殊位。
2. 写入临时文件内容后、replace之前，对**临时文件fd**设置目标普通mode，再fsync；最后replace和目录fsync沿用当前机制。不在replace后做一个容易留下错误最终mode的独立路径chmod。执行前如果目标mode在prepare后改变，不能直接覆盖刚发生的用户权限变动；结合内容身份检查给冲突/未知结果。
3. 将mode纳入成功回执和恢复对账：内容与desired_mode都满足才报reconciled。仅内容相同、mode不同不能宣称本次目标效果已确认；按已定义的before/desired边界处理，不静默“修正”外部改变。内容本来相同而mode原本正确的no-op不得重写文件。
4. 单文件patch别名和patch_text Update通过共同的准备逻辑传递mode；避免只修Tools.prepare而绕过手工构造intent的两个入口。先明确旧持久intent没有mode字段的兼容处理，不回写/伪造旧私有checkpoint。
5. **Move需要单独作出本地选择。** 本项目Move目的当前必须不存在，写目的再删源。若本轮包含“Move保留源执行位”，应把source_mode显式传入dest write desired_mode，并在删源前核对目的内容+mode；这比本次上游证据更强，须标为独立选择并测跨进程恢复。若暂不做，明确剩余缺口；不能由修好Update就声称Move也保留。Add维持0600、Delete逻辑保持原规则。

建议验证只针对此次原因：实际0755/0640文件经直接write、patch别名、patch_text Update后的内容与mode；新建仍0600；prepare后mode改变拒绝/暂停；no-op不重写；实际效果后checkpoint前kill/reopen既校验内容也校验mode；若包含Move，再验0755源到新目的及目的效果后恢复。不扩展到完整ACL/xattr/owner或多平台安全研究。

## 来源身份与收尾边界

| 直接引用文件 | Git blob SHA |
|---|---|
| [codex-rs/apply-patch/src/lib.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/apply-patch/src/lib.rs) | `315a6425d0913117e95c28f9b527f24c3b23d0ed` |
| [codex-rs/file-system/src/lib.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/file-system/src/lib.rs) | `4756de18de64f8459d01dd619841db6e69d0be90` |
| [codex-rs/exec-server/src/local_file_system.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/local_file_system.rs) | `abe2a0a6f13e715541eafc11b4ba6a37b6bbf467` |
| [codex-rs/exec-server/src/no_follow/unix.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/no_follow/unix.rs) | `24d5feb901ecbf9b49c793d22341f0263fd2ae0a` |
| [codex-rs/exec-server/src/no_follow/mod.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/no_follow/mod.rs) | `8e701f9eea529545ae79b5720b4ca5a0592bc029` |
| [codex-rs/exec-server/src/fs_helper.rs](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/exec-server/src/fs_helper.rs) | `d30766370c5962861ea2ae538a82d310cef63890` |

本次证据已足够支持“本地atomic replacement需显式保留普通rwx位”的小修复，并明确上游Update沿用文件、Move不传source-mode的不同机制。没有证明上游跨平台完整mode保证，也没有执行上游或本地复现；运行时修复与验证由后续增量负责。

资料检查：7组原始行摘录与连接器取得源码逐字一致，31个固定源码URL/行范围通过校验，本地链接存在。此检查不替代行为测试。
