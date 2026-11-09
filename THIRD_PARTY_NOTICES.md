# Third-party notices

本仓库的 Python runtime 仅依赖标准库；显式 Node及通用执行入口使用操作者已安装的外部工具（本轮实际Ruby），不下载或打包它们。没有 vendored 上游运行时代码。研究摘录与链接不表示本项目原创实现了整个上游框架，也不表示上游背书。

| 项目 | 用途 | 读取版本与许可 |
|---|---|---|
| OpenAI Codex | 研究 turn/tool/rollout、执行/权限和配置层机制，保存标明来源的源码研究材料与选段；没有移植到本地runtime | V0于2026-11-07读取main；后续机制研究固定 `e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`，Apache-2.0；[原许可](https://github.com/openai/codex/blob/main/LICENSE)，本地副本。新增配置三blob与实际选段见evidence/codex-config/manifest.json；Skills资源catalog/host/read三blob及明确已读范围见evidence/codex-skill-resources/manifest.json，不将其记作本项目原创实现。安装版参考实验额外provider/auth/macOS配置/FunctionCall区间见evidence/codex-reference-cli/manifest.json；安装二进制未复制进仓库，实际身份与固定源码不等同。新增MCP资源八文件实际链和明确已读区间见evidence/codex-mcp-resources/manifest.json；Git blob由返回文本计算，未独立对tree API校验。 |
| LangGraph | 研究 checkpoint/pending writes 与副作用恢复，少量短摘录 | 2026-11-07 读取 main，MIT；Copyright (c) 2024 LangChain, Inc.；[原许可](https://github.com/langchain-ai/langgraph/blob/main/LICENSE)，副本 |
| OpenHands | 研究 controller/state/budget，少量短摘录 | tag 0.54.0，MIT；Copyright © 2023；[原许可](https://github.com/OpenHands/OpenHands/blob/0.54.0/LICENSE)，副本 |
| Python / SQLite | 标准库及所链接数据库运行时 | 随用户 Python 发行版提供；分别遵循 PSF 及 SQLite 的发行许可；未复制其源码 |
| setuptools | 仅可选 wheel 构建 | 构建依赖 `>=61`；本次临时 build venv 实装82.0.1，metadata MIT / Python>=3.9；未打包进运行时，系统安装未升级 |
| wheel / packaging / pip | 本地隔离构建、安装工具 | 临时 build venv实装wheel0.48.0（MIT）、packaging26.3（Apache-2.0 OR BSD-2-Clause），pip21.2.3（MIT）来自本机venv引导；仅执行外部工具，不复制其源码或打包其distribution。版本/许可按已安装metadata记录，运行时仍零依赖。 |
| pytest / 依赖 | 外部Host venv真实native测试，不是agent_lab依赖 | tmp venv实装pytest8.4.2（MIT）、pluggy1.6.0/iniconfig2.1.0/tomli2.5.0（MIT）、Pygments2.21.0（BSD-2-Clause）、typing_extensions4.16.0（PSF-2.0）、packaging26.3（Apache-2.0 OR BSD-2-Clause），按已安装metadata；exceptiongroup1.3.1的metadata无license，读取其distribution licenses/LICENSE确认MIT。venv自带pip21.2.3/setuptools57.4.0，后者metadata为UNKNOWN，实际LICENSE为MIT文本。未复制/打包实现或distribution；准确版本与runtime SHA见evidence/v2/python-env-real-pytest.json。 |
| Node.js / bundled V8 / bundled Abseil / libuv | 可选外部 Node 执行、内置测试；研究实际启动失败，少量短源码摘录 | Node `v24.22.2` 与逐文件 blob，MIT / BSD / Apache-2.0；具体摘录归属、完整相关许可与上游链接见 研究许可记录、调用链。未复制其生产实现或外部可执行文件。 |
| Apple Libc / XNU | 研究 sysconf/getpagesize/uname/数字 sysctl 与精确读取范围，少量 getpagesize 摘录 | Libc commit `71bbe350ab79eef58113991d817ccc6165061a64`，XNU `d8b80295118ef25ac3a784134bcf95cd8e88109f`；各源文件 BSD/Apple 等原许可依其原文，见同一研究许可记录与既有 fork-containment 记录。无 Apple 实现移植。 |
| Apple libproc / Host SDK ABI | 只读owned-group观测，ctypes调用系统库；研究fork/exit/group顺序 | XNU固定同上，完整必要来源见docs/group-drain-source-study.md；匹配Host公开proc_bsdinfo/sigaction布局，不移植内核或libproc函数实现，不打包系统库。系统发行及各源文件原许可仍适用。 |
| Ruby | 通用Host执行入口的实际native验证，仅调用现有系统工具 | 本机Ruby2.6.10p210；上游2.6.10 [COPYING](https://github.com/ruby/ruby/blob/v2_6_10/COPYING)说明可选2-clause BSDL或Ruby条款，其他文件/系统发行组件仍依各自原许可。不复制/修改/打包Ruby可执行文件或库；owned测试脚本为本项目编写。 |
| Apple clang / macOS SDK / ld及工具链库 | Host编译owned夹具，以及显式授权后真实沙箱内的C frontend/link与产物执行 | 本机Apple clang15.0.0、SDK14.4及现有ld/libtapi/libLTO/libswiftDemangle，遵循各自原发行条款；未复制/打包compiler、SDK、库或生成binary。owned C源码为本项目编写；工具实际执行与Scripted决策分别标注，不计新增真实LLM质量。源码研究只复用已有Codex/Libc/XNU材料和otool依赖metadata，不声称复制ld实现。 |
| 官方MCP filesystem server / SDK及9项临时npm依赖 | 外部第三方stdio互操作，固定public lock、发布实现体研究；没有vendor实现/node_modules | filesystem2.2.0 metadata为SEE LICENSE，12-entry tar未带LICENSE；官方repo现MIT→Apache-2.0迁移、文档CC-BY-4.0，[原文](https://github.com/modelcontextprotocol/servers/blob/main/LICENSE)/blob副本。不虚构包内每段已统一重许可。server/core2.4.0 metadata Apache-2.0；diff8.0.4 BSD-3-Clause，zod4.6.7/balanced-match4.0.4/brace-expansion5.0.12 MIT，minimatch11.0.5/@isaacs/nonrecursive-glob-match1.1.0 BlueOak-1.0.0。准确lock、integrity与已读体SHA见evidence/interop/filesystem-2.2.0。仅临时安装/执行，不将第三方runtime当独立原创。新增resources probe复用同一SDK2.4.0与lock，resource callback为本项目自编，非发布server产品；没有新增依赖或vendor SDK。 |

本项目自有代码与文档采用根目录 `LICENSE` 的 Apache-2.0 条款。第三方研究摘录与许可保持各自原条款，不整体改标 Apache-2.0，见根目录 `NOTICE`。未进行源码相似度专项审计。
# Additional fixed-source research for autonomous context mission

`evidence/mission-context-source` contains bounded unchanged public excerpts and
the pinned Codex LICENSE/NOTICE for OpenAI Codex commit
`e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9` (Apache-2.0; original notices retained).
Manifest records source Git blob IDs, inspected ranges and local evidence hashes.
The research prose/JSON wrappers and Python whole-batch byte-prefix algorithm are
local work; no upstream implementation was translated into runtime. See
`docs/mission-context-source-study.md` for exact mechanism differences.

Additional fixed-Codex bounded instruction/exec feedback excerpts are preserved
in evidence/mission-execution-guidance-source and evidence/mission-tool-feedback-source,
with Apache-2.0 LICENSE/NOTICE and blob/line/hash manifests. They support mechanism
research; runtime code is independently written Python, not a prompt/source translation.

The execution-review study additionally retains bounded verbatim excerpts from
Codex session/token_budget.rs, session/rollout_budget.rs and session/turn.rs at the
same fixed Apache-2.0 commit. Exact blobs/ranges and original LICENSE/NOTICE are in
evidence/mission-execution-review-source/manifest.json. Our ready-boundary model
step/test feedback heuristic is an independent design, not a translation of those
budget or Stop hook implementations.

The Git-availability study adds three bounded fixed Codex excerpts (status.rs,
git_process.rs, errors.rs); exact blobs/ranges plus original Apache-2.0 LICENSE
and NOTICE are retained in evidence/mission-git-availability-source/. The local
Python exception category/trusted worker envelope/error-budget policy is independently
designed and is not a translation of the upstream snapshot error enum.

## Release scope clarification (2026-11-09)

Original Python runtime and original documentation: Apache-2.0, per root LICENSE.
Retained third-party materials remain separately licensed. In particular, public
XNU research quotations in evidence/fork-containment are subject to the retained
Apple Public Source License2.0 (APPLE_LICENSE); WebKit/libuv source quotations
retain their original linked headers/terms. They are not incorporated into runtime.
The MCP package/license transition is not represented as a uniform relicensing: the
repository retains metadata/hash/range observations and lock files, not the installed
filesystem server/SDK implementation or node_modules. Original licenses/NOTICEs
remain with Codex, LangGraph, OpenHands, Node/Libc/XNU and MCP research materials.

The official Apache license and redistribution requirements were rechecked at
https://www.apache.org/licenses/LICENSE-2.0 and
https://www.apache.org/foundation/license-faq.html on2026-11-09. This is a scoped
provenance review, not a source-similarity audit or an assurance of rights in unknown
third-party code. No unresolved-origin implementation was added or called original.
