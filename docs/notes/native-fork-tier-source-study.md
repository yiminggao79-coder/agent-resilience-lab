# Native entry / ENOSYS：受限fork tier的三项源码事实

2026-11-08。已先读`fork-containment-research.md`与`macos-fork-control-study.md`，不重做setcontrol、setsid/setpgid或spawn属性绕过。只读官方源码、既有证据和一个系统自带Apple profile；本作者没有native/Node/Python项目probe、runtime/profile改动、安装、模型API、key/实际ledger/V3/frozen/private checkpoint或Git操作。下述最多3个候选probe归Root选择/执行，不是production放宽。

**决定：native Python.app入口有直接源码依据；244回ENOSYS不是可靠libuv/npm兼容保证。** Root随后报告保留NoFork的有限probe已确认native入口能启动，默认244拒绝返回errno1、临时ENOSYS override返回78，fork在两者均返回1。本作者未执行/重跑该probe；其证据只推进入口与errno问题，不能被解释成允许fork、进程树静止或npm通过。

## 1. CPython framework bin/python不是native interpreter本体

官方CPython `v3.9.7`，三个文件仅追这些相关目标/函数，没有编译或检查本机二进制来源一致性：

| 源码 / blob | 实际体 |
|---|---|
| Mac/Tools/pythonw.c / `78813e818e7dace8f744113f7a587e64b0454dfd` | [pythonw:58–88](https://github.com/python/cpython/blob/v3.9.7/Mac/Tools/pythonw.c#L58-L88) get_python_path；[pythonw:139–151](https://github.com/python/cpython/blob/v3.9.7/Mac/Tools/pythonw.c#L139-L151) SETEXEC；[pythonw:154–239](https://github.com/python/cpython/blob/v3.9.7/Mac/Tools/pythonw.c#L154-L239)main。setup_spawnattr只追初始化、flag和调用相关段，不展开所有arch-preference分支。 |
| Mac/Makefile.in / `f9691288414538343b6aedb33e8a9382449baf42` | [pymake:178–181](https://github.com/python/cpython/blob/v3.9.7/Mac/Makefile.in#L178-L181)把Tools/pythonw.c构建成pythonw；[pymake:229–229](https://github.com/python/cpython/blob/v3.9.7/Mac/Makefile.in#L229-L229)另把BUILDPYTHON装进app bundle。 |
| Makefile.pre.in / `6ce7a614dce23825d5a175102295023ebe452297` | [pymainmake:1300–1318](https://github.com/python/cpython/blob/v3.9.7/Makefile.pre.in#L1300-L1318)framework安装bin/python版本名的文件是Mac/pythonw；[pymainmake:1762–1763](https://github.com/python/cpython/blob/v3.9.7/Makefile.pre.in#L1762-L1763)frameworkpythonw build目标。 |

实际链是framework bin/python→pythonw main→get_python_path用dladdr(Py_Initialize)定位framework library→同目录Resources/Python.app/Contents/MacOS/PYTHONFRAMEWORK→posix_spawn，attrs含POSIX_SPAWN_SETEXEC。这个flag让spawn按exec替换当前映像，并不表示wrapper想创建另一个常驻子进程；但它仍经过244，所以该syscall整体拒绝会阻断wrapper。

**失败不走execve fallback。** main在posix_spawn symbol存在时调用它，返回便err(1)退出；后面的execve只在weak-linked symbol不可用时到达，注释针对旧macOS。不是“EPERM/ENOSYS后自动fallback”。这解释了既有实测wrapper bootstrap failure。

Native app entry绕过这层wrapper有机制依据；**不保证它与wrapper完全相同**。wrapper还保存未完全realpath的原始入口到__PYVENV_LAUNCHER__，供真实interpreter恢复原调用位置/pyvenv语义（[pythonw:162–211](https://github.com/python/cpython/blob/v3.9.7/Mac/Tools/pythonw.c#L162-L211)）。所以本地临时直入native Python.app可回答“244是否仅卡wrapper”，不能直接用它替换host-selected venv语义或批准identity，也不能从源码宣称当前host该binary逐字为3.9.7。

归属/许可：[CPython v3.9.7 LICENSE](https://github.com/python/cpython/blob/v3.9.7/LICENSE)；本文只述机制，不复制实现体。

## 2. 当前Node捆绑libuv 1.52.1：ENOSYS fallback有重要前提

官方Node `v24.22.2`实际bundled版本由 [uvversion:33–36](https://github.com/nodejs/node/blob/v24.22.2/deps/uv/include/uv/version.h#L33-L36) 确认为**1.52.1**，version.h blob `70f071a478f420fe4752d9a38581fb0980ef684d`；process.c blob `539e7d9417d3bf9b8e73bc416e1165b0d6d59d65`。不能把先前独立libuv1.48.0摘录当当前Node构建的完整证据。

已读的Darwin链：

- [uvprocess:860–900](https://github.com/nodejs/node/blob/v24.22.2/deps/uv/src/unix/process.c#L860-L900)先尝试uv__spawn_and_init_child_posix_spawn；仅UV_ENOSYS继续fork/exec，其余error直接return。NoFork未变时，即使到fork fallback，也没有获得fork权限。
- [uvprocess:778–815](https://github.com/nodejs/node/blob/v24.22.2/deps/uv/src/unix/process.c#L778-L815)设置attrs→file actions→resolve_and_spawn；[uvprocess:704–712](https://github.com/nodejs/node/blob/v24.22.2/deps/uv/src/unix/process.c#L704-L712)对含slash的file直接调用posix_spawn并返回其error。
- **关键约束**：file actions会改pipes；[uvprocess:558–570](https://github.com/nodejs/node/blob/v24.22.2/deps/uv/src/unix/process.c#L558-L570)明确ENOSYS需在可能改pipes之前返回。实际spawn返回后 [uvprocess:803–806](https://github.com/nodejs/node/blob/v24.22.2/deps/uv/src/unix/process.c#L803-L806)还有assert(err != ENOSYS)。所以“让kernel拒绝244返回ENOSYS”并非这份实现原本支持的所有fallback场景；它最初为缺少所需非标准spawn helper而设。
- [uvprocess:819–857](https://github.com/nodejs/node/blob/v24.22.2/deps/uv/src/unix/process.c#L819-L857)fallback确实调用fork，child走uv__process_child_init；本轮没有继续追child_init全部exec/setsid/file-actions实现。

**推断而非执行结果**：assert启用的构建会因被强制返回ENOSYS而abort；禁用assert的构建可能把该error变成UV_ENOSYS并进入fallback，但之前pipe变动使其不能被当成已验证安全/兼容路径。本研究未确定当前binary该assert的编译状态，也未运行child_process。libuv原来的JIT/fork性能理由不能证明受限fallback对任意pipe/stdio/detached选项正确。

与旧证据比较：已保存libuv1.48.0外层摘录只追862–889的选择/fallback，没有追内层assert/pipe前提。当前1.52.1保留外层选择，但本轮补出的内层使“只改errno便兼容”的结论不足；不是声称这些内层约束一定是1.52新加。**不据此推导npm、shell pipeline或build工具兼容。**

归属/许可：[Node bundled libuv LICENSE](https://github.com/nodejs/node/blob/v24.22.2/deps/uv/LICENSE)；不复制实现体。

## 3. 真实errno modifier语法，与Root随后测得的244结果

在本机Apple系统文件 /System/Library/Sandbox/Profiles/cryptex-session-ruby-secure.sb 第16行，实际存在`deny file-read* file-test-existence`与`(with errno EACCES)`组合。其Apple2017版权头与“System Private Interface、可变”说明都在文件中。此处只读18行系统profile，没有读用户/第三方app配置；文件SHA-256：

`dacbd7ccdc8235973410d64cc042ce5beae71c4473a732828e64c285bbff857e`

这是一条实际语法证据，**单靠该文件只证明file-read类操作的写法，不能推导syscall-unix/244的效果**。本作者没有因此声称Apple公开文档保证该组合，也没有尝试20种SBPL参数。现有XNU公开MAC入口只说明按syscall number调用policy，闭源modifier的逐项语义不从源码猜出。

Darwin ENOSYS常量由固定XNU [errno:211–211](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/sys/errno.h#L211-L211)确认是78，errno.h blob `10b7c20166ce7ad8696dea569664742c6a136e33`；不要移用其他OS的数字。本作者未验证ENOSYS符号是否在SBPL上下文解析，也不猜其他modifier。**Root随后提供实测**：在既有grant profile与NoFork下，direct Python.app可启动；默认spawn拒绝errno1，临时ENOSYS变体spawn拒绝errno78，fork在两者均errno1。报告位置为evidence/v2/native-entry-nofork-probe.json，作者未读/改其原始内容；以Root该次报告为观测来源。这使具体临时规则的244 errno问题不再只是猜测，不使其他主机/版本或任意operation获得保证。

## 最多3个owned probe的实施含义与停止点（本作者不执行）

1. **入口隔离**：在既有grant profile、NoFork不变、额外82/147/244 deny下，仅对比wrapper与确切native Python.app入口的短-lived bootstrap。输出仅合成标记/必要身份；固定deadline与可信wait。它可以证明wrapper层是否被绕过，不能证明Python child tests或venv支持。
2. **errno本身**：只有入口有效后，在Root自建fixture的临时profile里验证244实际返回值；errno modifier只作为单个有依据候选。目标进程若意外创建，只能是owned已知短寿命fixture，并按既有cleanup/quarantine契约收敛；没有可信静止回执即保留unknown。不是为了让未界定进程树启动。
3. **libuv实际构建反应**：仅在244被确认返回ENOSYS后，以现有被授予Node和固定owned child target观察abort/正常error/fallback迹象。NoFork仍拒绝，故源码选择fallback不等于child获准；实际error不能无依据反推出fork一定发生。最多验证这个版本的一个stdio配置，不称npm compatibility，也不因失败移除82/147/244或放开fork。

上列前两个问题已由Root随后报告的有限nofork probe推进，不建议重复；第三项当前libuv构建反应仍未验证。这不是要求重跑既有setcontrol/spawn属性反例。production当前deny继续保持。若native入口或errno组合不成立，停止本路线、记录具体阻塞；没有全局生命周期保证与另一个明确隔离backend之前，不能把killpg升级成通用树静止证明。本轮源码证据已经足够，不再扩展。

## 后续Root实测（与上面研究者未执行范围分开）

Root保存native-entry-nofork-probe.json：直接Python.app成功启动，fork仍errno1，
244分别实际errno1/78，production不改。另libuv-enosys-nofork-probe.json记录当前
binary启动信息Node24.22.2/libuv1.52.1、exit-6与精确ENOSYS assertion匹配；没有
成功child响应。此前“当前二进制assert行为未核实”现由该单一pipe-stdio实测
补上，不概括全部选项/版本。该errno替代路线停止，不移除assert/边界或宣称npm
兼容；下一个问题是有界可信生命周期协议，不继续猜语法。

