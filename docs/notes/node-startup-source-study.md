# Node启动Abseil overflow：页大小查询的窄假设

2026-11-08。仅源码诊断，复用Node可行性、本地计划。未执行Node、native探测、项目脚本或模型API，未改runtime/profile，未读keys/ledger/V3任务、私有checkpoint或Git。以下官方文件按指定版本/commit获取，读列出的实际体/分支；不是对Node/Libc/XNU全仓审计。

**源码已解释三处顺序暴露的具体失败：页大小拒绝导致Abseil overflow、V8读取kern.osrelease失败、node:os导入在uname失败时触发参数断言。** Root报告compat-only已恢复页大小并越过Abseil，随后加精确kernel-release项后普通JS及限制fixture通过，但node:test none仍卡node:os导入（23/24 focused通过）。这些是主执行Agent提供的实验进展，本研究Agent未重跑或打开诊断日志；**不能据此宣称整个Node/node:test能力已通过**。后两次补证沿实际新错误追函数体，没有增加无证据的内存/JIT猜测。此记录保留研究时点，后续实现与实际验证见[实验登记](../EVALUATION.md)。

## 版本与源码身份

| 来源 | 精确版本 / blob | 已读范围 |
|---|---|---|
| Node bundled Abseil low_level_alloc.cc | official nodejs/node release `v24.22.2`；`a563f7b9f9bda269e9feabe5c19d6a9da52aa097` | [alloc:327–363](https://github.com/nodejs/node/blob/v24.22.2/deps/v8/third_party/abseil-cpp/absl/base/internal/low_level_alloc.cc#L327-L363) GetPageSize/Arena ctor；[alloc:433–445](https://github.com/nodejs/node/blob/v24.22.2/deps/v8/third_party/abseil-cpp/absl/base/internal/low_level_alloc.cc#L433-L445) CheckedAdd/RoundUp；[alloc:523–570](https://github.com/nodejs/node/blob/v24.22.2/deps/v8/third_party/abseil-cpp/absl/base/internal/low_level_alloc.cc#L523-L570) allocation/mmap分支。Tag按版本引用并保存内容blob，不伪称已解析完整Node release commit。 |
| Apple Libc sysconf.c | commit `71bbe350ab79eef58113991d817ccc6165061a64`（公开提交2026-04-17）；`d6d7883c7e6de035b1d47a1882f46f594c39f0bd` | [sysconf:318–326](https://github.com/apple-oss-distributions/Libc/blob/71bbe350ab79eef58113991d817ccc6165061a64/gen/FreeBSD/sysconf.c#L318-L326) PAGESIZE、[sysconf:369–375](https://github.com/apple-oss-distributions/Libc/blob/71bbe350ab79eef58113991d817ccc6165061a64/gen/FreeBSD/sysconf.c#L369-L375)yesno、[sysconf:699–706](https://github.com/apple-oss-distributions/Libc/blob/71bbe350ab79eef58113991d817ccc6165061a64/gen/FreeBSD/sysconf.c#L699-L706)通用sysctl错误分支。 |
| Apple Libc getpagesize.c | 同commit；`27d753371401d1de2a5261dbe5122300c7d744b6` | [getpagesize:41–64](https://github.com/apple-oss-distributions/Libc/blob/71bbe350ab79eef58113991d817ccc6165061a64/gen/FreeBSD/getpagesize.c#L41-L64)缓存及数字MIB/sysctl实际函数体。 |
| Apple XNU kern_mib.c | 已用公开base `d8b80295118ef25ac3a784134bcf95cd8e88109f`（xnu-10063.141.1）；`864c4d2296a0fd514229581283f5fa960293b30a` | [mib:459–464](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_mib.c#L459-L464) HW_PAGESIZE处理；[mib:636–644](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_mib.c#L636-L644)命名handler；[mib:889–890](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_mib.c#L889-L890)及[mib:985–994](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_mib.c#L985-L994) OID注册/compat语义。 |
| Apple XNU kern_newsysctl.c | 同XNU commit；`e4b17c6874698df327c7647493544a3cbdc8f0d6` | [sysctl:1593–1641](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_newsysctl.c#L1593-L1641)数字OID→namestring分支；[sysctl:1772–1785](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_newsysctl.c#L1772-L1785)MAC授权失败早返；[sysctl:1926–1934](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_newsysctl.c#L1926-L1934)数字userland入口。只追这些窄分支，未读整份大文件。 |
| Node V8 assembler-x64.cc | official `v24.22.2`；`a9f9c2dd447b36718ff48cf7eabc4cb84871a923` | [assembler:52–73](https://github.com/nodejs/node/blob/v24.22.2/deps/v8/src/codegen/x64/assembler-x64.cc#L52-L73) OSHasAVXSupport；[assembler:87–107](https://github.com/nodejs/node/blob/v24.22.2/deps/v8/src/codegen/x64/assembler-x64.cc#L87-L107) ProbeImpl调用条件。不是cpu-x64.cc；该文件只有icache代码，本轮定位后未拿它作证据。 |
| Apple Libc uname.c | 同Libc commit；`f6fe488fbc45a1f942ef732cba776f3dfc3b1a05` | [uname:40–86](https://github.com/apple-oss-distributions/Libc/blob/71bbe350ab79eef58113991d817ccc6165061a64/gen/uname.c#L40-L86) uname实际5次sysctl函数体。与本次V8直接sysctl路径分开。 |
| Node lib/os.js | `v24.22.2`；`5e53879bd6d5aa939dbf4a70ad7dad67bfe88b65` | [jsos:46–79](https://github.com/nodejs/node/blob/v24.22.2/lib/os.js#L46-L79)native binding绑定与import-time调用。 |
| Node src/node_os.cc | 同版本；`4c7ebde4644fb2c675c5a1533f9fcf1c52b393ee` | [nodeos:82–107](https://github.com/nodejs/node/blob/v24.22.2/src/node_os.cc#L82-L107) GetOSInformation完整体。 |
| Node bundled libuv unix/core.c | 同版本；`73027cbc52ab0f10eee1efd603c071d8ba4239f4` | [uvcore:1850–1860](https://github.com/nodejs/node/blob/v24.22.2/deps/uv/src/unix/core.c#L1850-L1860) uv_os_uname→uname/errno分支，[uvcore:1862–1894](https://github.com/nodejs/node/blob/v24.22.2/deps/uv/src/unix/core.c#L1862-L1894)输出字段复制。未读整份core.c。 |

Apple公开Libc版本**未与本机libSystem逐字匹配**；XNU公开base也不等于host `10063.141.1.707.11~1`补丁构建。其调用机制/名字提供有依据的候选，不能替代同机最小验证。原始源码header各自保留上游归属/许可，此处是短研究引用，不移植实现。

## 事实1：Abseil出错点正是unsigned checked addition

在Windows与特殊wasm/asmjs/hexagon分支之外，GetPageSize直接调用sysconf并转size_t，Apple常规构建进入此分支。Arena构造将返回值存入const pagesize；这一链没有检查negative或errno。

[alloc:327–337](https://github.com/nodejs/node/blob/v24.22.2/deps/v8/third_party/abseil-cpp/absl/base/internal/low_level_alloc.cc#L327-L337)

```c
327: size_t GetPageSize() {
328: #ifdef _WIN32
329:   SYSTEM_INFO system_info;
330:   GetSystemInfo(&system_info);
331:   return std::max(system_info.dwPageSize, system_info.dwAllocationGranularity);
332: #elif defined(__wasm__) || defined(__asmjs__) || defined(__hexagon__)
333:   return getpagesize();
334: #else
335:   return static_cast<size_t>(sysconf(_SC_PAGESIZE));
336: #endif
337: }
```

后续DoAllocWithArena先加header并按小block size取整；freelist不足时，第二次RoundUp使用`arena->pagesize * 16`，**在mmap之前**执行。CheckedAdd的检查正位于报告的437行：

[alloc:435–444](https://github.com/nodejs/node/blob/v24.22.2/deps/v8/third_party/abseil-cpp/absl/base/internal/low_level_alloc.cc#L435-L444)

```c
435: static inline uintptr_t CheckedAdd(uintptr_t a, uintptr_t b) {
436:   uintptr_t sum = a + b;
437:   ABSL_RAW_CHECK(sum >= a, "LowLevelAlloc arithmetic overflow");
438:   return sum;
439: }
440: 
441: // Return value rounded up to next multiple of align.
442: // align must be a power of two.
443: static inline uintptr_t RoundUp(uintptr_t addr, uintptr_t align) {
444:   return CheckedAdd(addr, align - 1) & ~(align - 1);
```

在unsigned宽度W、sysconf返回-1的情况下，pagesize成为2^W−1，pagesize*16按unsigned模运算成为2^W−16，align−1成为2^W−17；普通req_rnd大于17便足以让CheckedAdd加法wrap，触发sum>=a失败。这是**从已读代码推导**，不是采到Abseil现场pagesize/请求值。相同CheckedAdd还用于request+header，故仅凭437行字符串不能唯一定位caller；当前小JS启动也失败及sysconf errno1使页大小假设更有针对性。没有证据把错误描述成内存耗尽或_collect设置resource limit，也不建议加大内存限额来处理。

## 事实2：Apple页大小查询可到数字sysctl，名字需注意compat

已读Libc sysconf的_SC_PAGESIZE先取getpagesize作为defaultresult；某些构建若定义P1003宏还会走额外yesno查询，错误也返回-1。故不能在未匹配本机build时保证只有一次sysctl。getpagesize实际体如下：

[getpagesize:56–63](https://github.com/apple-oss-distributions/Libc/blob/71bbe350ab79eef58113991d817ccc6165061a64/gen/FreeBSD/getpagesize.c#L56-L63)

```c
56: 	if (!value) {
57: 		mib[0] = CTL_HW;
58: 		mib[1] = HW_PAGESIZE;
59: 		size = sizeof value;
60: 		if (sysctl(mib, 2, &value, &size, NULL, 0) == -1)
61: 			return (-1);
62: 	}
63: 	return (value);
```

这里是CTL_HW/HW_PAGESIZE数字MIB，失败原样-1。不是凭名字猜“可能调用sysctl”。

固定XNU将数字HW_PAGESIZE放在**pagesize_compat**的int-sized masked节点，按名hw.pagesize则是另一个quad-sized节点；两者都读取当前task map的page size。上游注释明确compat节点通常按数字查、quad节点按名字查。[mib:985–994](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_mib.c#L985-L994)、[mib:459–464](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_mib.c#L459-L464)、[mib:636–644](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_mib.c#L636-L644)、[mib:889–890](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_mib.c#L889-L890)。

数字sysctl入口调用userland_sysctl(FALSE,...)；sysctl_root匹配oid_number时用oid_name拼namestring，再将namestring、数字name/长度、读写参数交mac_system_check_sysctlbyname，非零error在handler前goto dropref。[sysctl:1620–1635](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_newsysctl.c#L1620-L1635)、[sysctl:1772–1785](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_newsysctl.c#L1772-L1785)。这解释了为何数字HW_PAGESIZE也有可用于MAC检查的名字；**没有逆向或证明闭源Seatbelt内部每条映射**。

补强现有证据：官方Codex固定 `e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9` 已保存base SBPL的[48–49行](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/seatbelt_base_policy.sbpl#L48-L49)明确分别列出`hw.pagesize_compat`和`hw.pagesize`。这是已有真实规则，不是新编operation名；但“上游允许”不等于本机本项目已经通过。

## 事实3：新的V8 kernel-version错误直接请求kern.osrelease，不经uname

Root的compat-only临时实验已让_SC_PAGESIZE恢复4096，原Abseil错误消失并到达新的V8错误。它支持前述页大小因果链，但不证明此后Node已可执行JS。本轮不重复alloc猜测或实验。

已读同版本V8 [assembler:87–107](https://github.com/nodejs/node/blob/v24.22.2/deps/v8/src/codegen/x64/assembler-x64.cc#L87-L107)：CpuFeatures::ProbeImpl在host x64/ia32分支构造base::CPU，若has_avx与has_osxsave则调用OSHasAVXSupport。其Darwin分支直接查询CTL_KERN/KERN_OSRELEASE；非零返回立即FATAL，字串与Root提供的新错误一致。**这条路径不调用uname。**

[assembler:52–61](https://github.com/nodejs/node/blob/v24.22.2/deps/v8/src/codegen/x64/assembler-x64.cc#L52-L61)

```c
52: bool OSHasAVXSupport() {
53: #if V8_OS_DARWIN
54:   // Mac OS X up to 10.9 has a bug where AVX transitions were indeed being
55:   // caused by ISRs, so we detect that here and disable AVX in that case.
56:   char buffer[128];
57:   size_t buffer_size = arraysize(buffer);
58:   int ctl_name[] = {CTL_KERN, KERN_OSRELEASE};
59:   if (sysctl(ctl_name, 2, buffer, &buffer_size, nullptr, 0) != 0) {
60:     FATAL("V8 failed to get kernel version");
61:   }
```

该函数只需要kernel release的major component来处理旧macOS AVX问题，并非在此读取hostname/完整uname。对应精确只读候选为`(allow sysctl-read (sysctl-name "kern.osrelease"))`，已有[固定Codex base SBPL62行](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/sandboxing/src/seatbelt_base_policy.sbpl#L62-L62)也使用此名字。相同数字OID→MAC namestring机制沿前述XNU体，具体本机闭源Seatbelt效果仍需最小实验。

为避免混淆，另读Apple Libc uname [uname:40–86](https://github.com/apple-oss-distributions/Libc/blob/71bbe350ab79eef58113991d817ccc6165061a64/gen/uname.c#L40-L86)：它依次读取KERN_OSTYPE、KERN_HOSTNAME、KERN_OSRELEASE、KERN_VERSION、HW_MACHINE，记录任一失败返回-1。因此**Python os.uname失败只说明其中至少一项被拒绝，不能证明此V8路径需要五项全开**。它们对应kern.ostype/kern.hostname/kern.osrelease/kern.version/hw.machine的已知精确名字亦见既有Codex SBPL，但本次不建议为让诊断helper成功而授予其他四项。这里没有把Python ctypes导入需求当Node需求。

## 事实4：node:test的新node:os断言确实需要完整uname

Root提供的新stack由test runner导入node:os（runner入口仅按stack报告，本轮未额外读runner.js）。实际同版本lib/os.js [jsos:46–79](https://github.com/nodejs/node/blob/v24.22.2/lib/os.js#L46-L79)从internalBinding('os')取得_getOSInformation，并在module加载时**不带参数**调用它以初始化type/version/release/machine常量。

native GetOSInformation先调用uv_os_uname；error分支要求args.Length()>=1再收集UV错误。无参数的module初始化遇错误，正好会在此CHECK上abort。[nodeos:82–107](https://github.com/nodejs/node/blob/v24.22.2/src/node_os.cc#L82-L107)。

[nodeos:82–91](https://github.com/nodejs/node/blob/v24.22.2/src/node_os.cc#L82-L91)

```c
82: static void GetOSInformation(const FunctionCallbackInfo<Value>& args) {
83:   Environment* env = Environment::GetCurrent(args);
84:   uv_utsname_t info;
85:   int err = uv_os_uname(&info);
86: 
87:   if (err != 0) {
88:     CHECK_GE(args.Length(), 1);
89:     USE(env->CollectUVExceptionInfo(
90:         args[args.Length() - 1], err, "uv_os_uname"));
91:     return;
```

bundled libuv在unix/core.c [uvcore:1850–1860](https://github.com/nodejs/node/blob/v24.22.2/deps/uv/src/unix/core.c#L1850-L1860)直接调用libc uname，返回-1时将errno转为libuv error；成功后复制sysname/release/version/machine。因此这里终于连接到前述Apple uname的**全部5项**，与先前V8只读单个kernel release的路径不同。

该Libc uname逐项查询，即使某项失败仍继续其余项，最终rval=-1；要让此实现完整成功，有限名单为：

| 精确只读sysctl | 依据 |
|---|---|
| hw.pagesize_compat | 原Abseil页大小链；Root compat-only实验已支持。 |
| kern.ostype | uname.sysname，[uname:48–52](https://github.com/apple-oss-distributions/Libc/blob/71bbe350ab79eef58113991d817ccc6165061a64/gen/uname.c#L48-L52)。 |
| kern.hostname | uname.nodename，[uname:54–58](https://github.com/apple-oss-distributions/Libc/blob/71bbe350ab79eef58113991d817ccc6165061a64/gen/uname.c#L54-L58)。 |
| kern.osrelease | uname.release，[uname:60–64](https://github.com/apple-oss-distributions/Libc/blob/71bbe350ab79eef58113991d817ccc6165061a64/gen/uname.c#L60-L64)；亦是V8 AVX探测项。 |
| kern.version | uname.version，[uname:66–80](https://github.com/apple-oss-distributions/Libc/blob/71bbe350ab79eef58113991d817ccc6165061a64/gen/uname.c#L66-L80)。 |
| hw.machine | uname.machine，[uname:81–85](https://github.com/apple-oss-distributions/Libc/blob/71bbe350ab79eef58113991d817ccc6165061a64/gen/uname.c#L81-L85)。 |

**隐私能力变化必须明示。** Node GetOSInformation返回的4项不包含nodename，但libc uname仍为填nodename而读取kern.hostname。允许该名字会允许sandbox读取hostname，不能描述成“仅CPU或页大小元数据”。本研究没有读取/导出实际hostname。若Root不接受这个能力，node:os/node:test应保留为不支持，不能悄悄广泛放行或篡改Node源码以消除失败。

## 有界决定：最多这6个具体读取，不开通全部sysctl

三处错误都已有对应函数体。Root可仅在新合成fixture临时profile验证上述6项联合，保持NoFork/network/Root-readonly/path/credential/执行期限等全部边界不变。这里多出的4个uname项有这次真实node:os失败链依据；不是为了让Python诊断helper好看而加。

若同进程node:test越过本次assert，仍须以实际脚本/两文件测试与原隔离契约验证评价该有限能力，不能从host --version或源码推理宣称通过。若还有新的失败，保存并报告具体阻塞；本轮不再扩sysctl名单、猜system service/JIT规则或改frozen任务/使用unsandboxed fallback。若6项实际查询成功却同一断言仍出现，当前公开Libc与实际build映射不足，明确blocked而不继续无限试错。

原页大小与V8两个有限假设分别已由Root实验推进；第三处按实际断言补齐确定的Node→libuv→Libc调用链，不引入新的泛化假设。**本作者未执行这些实验、未改production policy；node:test尚未获得本研究的通过记录。** 到此停止源码研究。

