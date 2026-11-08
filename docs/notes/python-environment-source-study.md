# Python依赖环境与pytest：可选host环境的源码依据

2026-11-08。固定官方Codex `e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`，复用managed command研究与Node runtime机制，只补环境构造与相关测试体。只读本地源码和官方文档，未执行上游/本地项目测试、Python环境探测、pip/安装或模型API，未读key/实际ledger/V3/frozen/private checkpoint，未改runtime或使用Git。

**结论：下一个小增量可以是“host明确选择一个已有依赖环境，仍经当前批准的bounded command执行”。** 不自动发现/激活workspace venv，不继承宿主PATH/Home，不提供任意pip安装。需要把venv launcher、base binary、pyvenv.cfg与有限依赖读取权限当作不同对象；当前resolve(sys.executable)到base的测试路径不等于可使用controller环境的依赖。

## 固定源码与真实环境链

以下范围为实际读取体；不是全文件或上游测试执行。OpenAI归属及已有Apache-2.0文本见LICENSE/NOTICE，本文不复制实现体。

| 新读来源 / blob | 实际机制 |
|---|---|
| `codex-rs/core/src/exec_env.rs` / `a119fa33a22fc3432a569256d7ade1c5bfc61d93` | [env:22–38](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec_env.rs#L22-L38)create_env代理shared policy；[env:40–81](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec_env.rs#L40-L81)runtime注入session/version/permission profile/patch设置。profile env本身是信息，源码18–19明确不能当enforcement证明。 |
| `codex-rs/protocol/src/shell_environment.rs` / `0c6c515c416d3829122e8037a3b9c49c1a581608` | [policy:69–174](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/shell_environment.rs#L69-L174)process vars→populate_env完整规则；[policy:14–27](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/shell_environment.rs#L14-L27)hard non-inheritable集合；[policy:177–181](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/shell_environment.rs#L177-L181)Unix Core名单。 |
| `codex-rs/core/src/exec_env_tests.rs` / `f17d7746140b7416366c5cfd0fa5d8db7b488f01` | [tests:82–104](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec_env_tests.rs#L82-L104)default保留合成敏感变量；[tests:106–129](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec_env_tests.rs#L106-L129)显式开启默认排除后移除。额外复核shared policy [policy:313–336](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/protocol/src/shell_environment.rs#L313-L336)Core大小写匹配测试。均为**测试体已读，未执行**。 |

复用已保存process manager（blob `caa45622a2c826ae7a7c14c1edd2c849e8f81799`）：[manager:1488–1541](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L1488-L1541)从所选环境shell policy创建env并交orchestrator，[manager:129–172](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L129-L172)runtime overrides与远端policy投影；[manager:1464–1480](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/unified_exec/process_manager.rs#L1464-L1480)把prepared request.command/cwd/env交spawn_process并显式无inherited FDs。详见原源码manifest。

短链：
`open_session_with_sandbox`取shell_environment_policy→core create_env→protocol create_env(std::env::vars)→populate_env→注入当前thread/session/profile/patch runtime字段→apply_unified_exec_env/strip_output_env→UnifiedExecToolRequest→orchestrator→[UnifiedExecRuntime616–653](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/tools/runtimes/unified_exec.rs#L616-L653) prepared command/cwd/env→[SandboxManager359–414](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/manager.rs#L359-L414)及[macOS461–497](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/manager.rs#L461-L497)生成真实sandbox-exec请求→local spawn。

另一个已读exec adapter [exec:927–976](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/core/src/exec.rs#L927-L976)将argv拆成program/args，保留cwd/env/network policy交spawn_child_async再收集输出。它说明env与权限真正到执行边界，不证明上游所有shell snapshot/远端backend有同样env结果。

### 核心事实：继承/筛选≠自动venv管理

populate_env顺序是：inherit All/None/Core→若未ignore则排除*KEY*/*SECRET*/*TOKEN*→custom exclude→host set overrides→include_only→注入thread_id→hard non-inheritable过滤。set在一般exclude之后，故可重新引入一般已排除变量；最后hard集合不能如此恢复。Core保留PATH/HOME等，但不含VIRTUAL_ENV；All可保留已有环境变量。**本链没有发现、创建或激活venv的实现**，不能把“PATH可能已有激活环境”称自动项目依赖管理；也不据此否定其他未追层存在相关能力。

default测试实际用ShellEnvironmentPolicy::default并断言合成API_KEY/SECRET_TOKEN仍在；另一个测试只设ignore_default_excludes=false就移除它们。因此不能将上游环境功能描述成“默认全部去敏”。这两条体级事实支持本地继续采用更窄clean env，不能为支持pytest复制上游All继承。当前hard集合也不是对所有服务凭据的通用识别器。

未追：ShellEnvironmentPolicy所有配置来源/default impl、shell startup/login/snapshot PATH改写、strip_output_env helper全部体、下层spawn env_clear实现、远端环境读取与包发现。上述测试是环境map contract，不是venv/pytest或native isolation测试，不能夸大证明范围。

## 本地实际边界与官方Python/pytest事实

只读commands.py、sandbox.py、command_runtime.py：

- python alias是当前sys.executable的canonical target，env policy记录base prefix/stdlib/controller Python版本；可选Node只新增显式descriptor。prepare/validate已绑定executable/workspace/cwd/env/policy/digest，但未表达可选Python dependency environment。
- _run_tests当前刻意用resolve(sys.executable)的base target，-I与固定unittest bootstrap，clean PATH/LANG、scratch HOME/TMPDIR、PYTHONDONTWRITEBYTECODE；它不将controller venv site-packages授予项目。run_command的固定env另设置PYTEST_DISABLE_PLUGIN_AUTOLOAD=1。不能仅在prepare换alias而忘记trustedworker/supervisor/profile或原固定测试入口。
- 现有read roots围绕base stdlib/runtime/system resources和workspace/scratch；加入另一个解释器并不自动让它的pyvenv.cfg、依赖、native shared objects可读。现有NoFork/network/hidden/whole-root read-only、join/unknown边界都仍适用，依赖可用性必须以后用合成fixture实测。

[Python3.9官方venv文档](https://docs.python.org/3.9/library/venv.html)说明环境有pyvenv.cfg、自己的binary或symlink及site-packages；无需activate才能使用，activate主要改变PATH。venv运行时sys.prefix与sys.base_prefix分开，include-system-site-packages决定是否纳入基础环境包。**因此执行路径语义与binary真实target身份应分别保存**，不能只留下resolve后的base路径而期待选中的venv仍生效；本轮没有对本机launcher行为做新实测。

[pytest官方版本支持表](https://docs.pytest.org/en/stable/backwards-compatibility.html#python-version-support)：pytest9.0+最低Python3.10，pytest8.4最低Python3.9，8.0–8.3最低3.8。Python3.9 controller不阻止以后选较新guest解释器，但如果guest也为3.9，则不能声明当前pytest9可用。[官方调用文档](https://docs.pytest.org/en/stable/how-to/usage.html#calling-pytest-through-python-m-pytest)支持所选Python以-m pytest调用；其普通形式会将cwd加入sys.path，但**不可把这条描述直接套到加-I后的调用**。本轮未检测installed pytest版本或导入成功，也未安装任何版本。

## 只收敛3个本地选择（未来prototype，未实现）

**1. 显式host选择已有环境，保持默认兼容。** 建议一个host-only配置，初期仅支持受约束、已有的普通venv，不自动source activate、不扫描workspace/.venv或PATH，也不把model参数当环境位置。默认python/run_tests仍按旧语义；选中的依赖解释器可用独立alias或明确runner选项，具体由Root选。保留requested launcher路径/合法有限symlink链与resolved base executable descriptor；pyvenv.cfg内容/身份、version/实际prefix/stdlib/受准依赖roots进入同一runtime manifest与approval/session匹配。不要将“同一Python二进制hash”冒称“同一venv包集合”。

**2. 依赖是有限read authority，仍用clean env与OS限制。** 第一版可只支持host选择、workspace外的venv及纯Python依赖，明确include-system-site-packages=false；grant必要launcher/config、base stdlib、指定site-packages读取，仍禁止对依赖环境写入。不是grant整个Home、.cache、全局packages或任意用户目录。处理.pth/sitecustomize、editable外链或native extension需要的外部文件应保守unsupported/精确扩展，不从依赖错误自动加目录。所有代码包括启动时site处理都在原native sandbox内执行。manifest若只绑定目录/配置而未冻结每个包byte，要如实说明并非依赖snapshot；若承诺审批内容不变则还需有界指纹/revalidation，不能伪造该保证。clean HOME/TMPDIR、no credentials、NoFork/network/readonly不变。

**3. pytest作为受限的明确测试调用，不带来pip或全生态承诺。** 首版可复用approved run_command执行所选interpreter与preinstalled pytest，配置不完整/pytest缺失/版本不兼容要在无模型或明确错误边界确认。不要隐式重写任意argv或把pytest console脚本shebang当正确解释器；-I、project sys.path、pytest import mode/插件策略要明确。若增加固定wrapper，须先从admitted环境加载真实pytest，再按既定项目导入策略暴露workspace，避免让同名project pytest.py抢占框架；现有unittest入口不必一并改变。禁用entry-point autoload不等于禁止conftest/全部插件或使项目测试无副作用。NoFork意味着xdist/multiprocessing/某些build步骤仍可能失败；这是明确范围，不据此放开进程创建。不新增pip工具、不安装依赖，也不承诺可阻止通用Python代码模拟任意包操作；原OS读写/network限制才是真边界。

下一有限验证只需新synthetic venv/小dependency/pytest contract：有选环境才导入、base与venv身份可区分、launcher/config/包身份变化不能复用旧批准、runtime/read scopes贯穿worker+supervisor、环境不可写/宿主私有不可读、readonly项目与scratch、插件/CWD策略、非零退出、timeout/cancel保持原语义。之后再谈native wheels/环境创建/依赖解析；本研究没有建立或运行这些fixture。

**实施含义已足够明确：单独承认依赖环境，而不是扩大PATH或让test runner继承controller依赖。** 上游shell env与本地host-selected venv是相关但不同机制；本轮到此停止。

