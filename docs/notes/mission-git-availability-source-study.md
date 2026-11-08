# 非Git工作区：固定源码窄研究

日期：2026-11-09；固定 ref：`e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`。
**结论：**固定源码中的辅助Git状态查询允许“结果不可用”，Git进程收集层区分正常
退出和运行失败；worktree snapshot错误类型另有NotAGitRepository。它们没有证明
原版具有本地 `git_repository_not_supported` 分类、相同ToolError子类或相同错误预算。
本次限三个实际源码文件，来源足够后停止；没有读取私有记录/验收题、调用付费模型、
执行上游、修改runtime或根文件。

证据清单 保存准确路径、
blob、已读范围、有限原文、许可及导出哈希。路径由固定完整Git树确认。

| 文件（前缀 `codex-rs/git-utils/src/`） | 固定 Git blob |
|---|---|
| `status.rs` | `ba24d52e37dcb5044646367842f61df8c34e0f89` |
| `git_process.rs` | `80c3d1859921a1824cf3480a6ab6618b43f21b37` |
| `errors.rs` | `41f7b6df495c4d3b316539ef8eef88713e92156f` |

**事实：状态辅助能力可以不可用。**
[status.rs L29–41](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/git-utils/src/status.rs#L29) 的 `get_has_changes_in_repo` 返回
`Option<bool>`。它调用 `git status --porcelain`；获取不到output时通过 `?`
返回None，拿到output后仅在 `output.status.success()` 时返回
Some(stdout是否非空)，非零退出返回None。因此None不是“仓库无改动”的Some(false)。
这层并未从stderr辨别“非仓库”“Git缺失”或其他原因，也没有任务终止/counter更新。

导入明确指向 `info::run_git_command_with_timeout_from` 和fsmonitor helper。
该中间函数体和调用此status helper的UI/宿主消费者不在三文件范围内，不能冒称
完整UI路径已经追通，或非仓库必定以某个精确提示显示给用户/模型。

**事实：Git进程收集与退出码判定分层。**
[git_process.rs L32–58](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/git-utils/src/git_process.rs#L32) 以受管理进程组/kill-on-drop启动
Git命令并收集stdout/stderr，启动失败走None。
[L60–93](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/git-utils/src/git_process.rs#L60) 在限定时间内 `wait_with_output`：
正常完成返回Some(Output)，没有按退出码success与否再次过滤；等待/超时失败
走None。Windows还有保留descendants失败时的None路径。
所以这层能保留普通非零进程结果，再由上层决定语义。它与status helper的Option
接口相容，但未读info中间体，本报告不把两者画成已经验证的完整直接调用链。
进程清理代码被读到，不等于本次执行验证了其OS行为。

**事实：非仓库是一个已声明的类型分支。**
[errors.rs L8–35](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/git-utils/src/errors.rs#L8) 注释限定为管理git worktree snapshots时的错误，
`GitToolingError` 分别声明GitCommand（含ExitStatus与stderr）、GitOutputUtf8、
NotAGitRepository（含PathBuf）、路径越界/相对路径及IO等变体。
声明本身不能证明哪个检测函数产生NotAGitRepository、哪些调用者消化它，或模型
执行Git命令时会得到该变体。本研究没有这些调用者证据。

**辅助Git状态/UI与模型Shell的边界。** 上面是git-utils宿主辅助API的可选状态及
snapshot错误类型，不是已证明的模型工具handler。具体状态UI消费方式未追踪。
[先前模型Shell研究](mission-tool-feedback-source-study.md)已依据固定
`exec_command` handler/process manager说明：普通完成进程的退出码与输出保留在
ToolOutput，执行异常和沙箱拒绝另有路径。若模型经Shell调用Git，不能把辅助状态的
None或snapshot的NotAGitRepository自动套到该Shell响应，也不能说两者共享本地
Python异常类。普通Git非零退出和宿主内建Git能力不可用可以是不同反馈类别。

**推断与本地选择（未实施）。** 已知内建只读Git工具在非Git synthetic工作区不可用，
可以由可信宿主分支给出独立的能力不可用分类，保留失败反馈；它不意味着坏JSON/
参数调用已被修复，也不意味着Git操作成功或代码质量通过。Typed ToolError子类是
本地防止仅靠外部error字符串授予例外的一种选择，不是上述Rust枚举的机械移植。
保留有限调用/时间边界、安全暂停及原评分；是否消费调用错误预算由本地语义和
真实复现决定。上游有限证据不能替本地修复证明效果或改变既有失败grade。

OpenAI Codex，Copyright 2025 OpenAI，Apache License 2.0。固定
[LICENSE](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/LICENSE) blob
`4606e72e042564097e8780d66c1d4dcb611869bd`；
[NOTICE](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/NOTICE) blob
`2805899d56d0332d175cfc613c67d45d6f006db7`。完整许可及NOTICE随有限摘录保存；
选择、包装和报告为本地新增。
