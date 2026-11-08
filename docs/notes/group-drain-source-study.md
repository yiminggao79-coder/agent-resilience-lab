# macOS unreaped leader / group drain：有界源码依据

2026-11-08；研究范围基于项目1825730，先读 `native-fork-tier-plan.md`、[native-fork-tier-source-study.md](native-fork-tier-source-study.md)，不重复既有setcontrol/spawn反例。本作者仅读公开Apple源码与项目文档，未执行native probe、修改runtime/profile、安装或操作Git，未读key、实际ledger、V3/frozen/private内容，未调用模型API。

**结论：公开base支持“独占保留未reap的普通leader可固定其PID/PGID身份”，也支持进入一个有界owned fixture；不支持把单个NOTE_EXIT、leader退出或空/截断列表称为通用进程树静止。** 必须保持组逃逸入口已阻断这一前提，且将API失败、未完成观察和deadline耗尽留为unknown。production NoFork不因本文改变。

## 固定来源与本机差异

以下均由官方GitHub连接器按XNU commit `d8b80295118ef25ac3a784134bcf95cd8e88109f`取得实际函数体，对应公开xnu-10063.141.1。作者已确认host为macOS14.8.2 / Darwin23.6.0、build `10063.141.1.707.11~1`；公开base不等于本机私有patch。本作者没有重新读取host信息。

| 文件 | 固定blob | 本次只追的体 |
|---|---|---|
| bsd/kern/kern_fork.c | `5201d84ee667b2d5f50684547c03e8d5b8447b4f` | fork→fork1→cloneproc→forkproc的身份/发布段 |
| bsd/kern/kern_proc.c | `62f9712abe40f60a4365342f7a8457a19e5ab6b4` | group add/leave/ref、pinsertchild、zombie查询 |
| osfmk/kern/thread.c | `35cc23b0d6089e274c0857770a8b7158e5b46fd5` | thread_terminate_self最后线程→proc_exit |
| bsd/kern/kern_exit.c | `11a82124feed54ae73c3df126fc052303bc81114` | proc_exit发布终止状态；wait4→reap_child_locked移除身份 |
| libsyscall/wrappers/libproc/libproc.c | `a5d2ff115d66b22b1f09678da489515c54bf862b` | proc_listpgrppids / proc_pidinfo封装 |
| bsd/kern/proc_info.c | `b33d15299447ba88be8c17d9c53795c912b519c6` | proc_info_internal→list / pidinfo，与short BSD状态输出 |
| bsd/man/man2/kqueue.2 | `13049a96d8caf7719964c322ff88ab44853a1f27` | NOTE_EXIT / NOTE_REAP定义，仅辅助协议含义 |

这是三条具体路径的必要callee，不是全kernel审计。没有运行上游测试，也没有把入口定位当函数体追踪。只转述机制/符号，不复制实现；文件原始Apple APSL2.0、BSD/UC及Mach等header依各固定源文件保留，[官方APSL全文](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/APPLE_LICENSE)为许可来源。

## 路径1：fork身份预留早于allproc发布

**源码事实。** 普通fork syscall同步调用fork1（[fork:571–605](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_fork.c#L571-L605)），fork1调用cloneproc（[314–324](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_fork.c#L314-L324)，cloneproc先forkproc，再fork_create_child，失败则forkproc_free（[677–720](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_fork.c#L677-L720)）。

forkproc在proc_list_lock内选择PID：排除phash中已有进程、已有PGID与session ID，然后插入PID hash、记录pidversion/uniqueid、pgrp_enter_locked继承父组（[958–1006](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_fork.c#L958-L1006)）。pgrp_enter取得组引用，pgrp_add_member插入pg_members并设置p_pgrpid / p_sessionid / p_pgrp（[2605–2614](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_proc.c#L2605-L2614)、[2274–2294](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_proc.c#L2274-L2294)）。

随后cloneproc才pinsertchild并设SRUN（[745–753](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_fork.c#L745-L753)）；pinsertchild在proc_list_lock内插入父children和allproc、清P_REF_NEW（[2434–2457](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_proc.c#L2434-L2457)）。因此存在**已经预留PID/进入组但尚未进入allproc**的构造窗口。仅扫allproc/zombproc不直接看到这个窗口；不能把一次列表快照解释为对所有in-flight创建的原子屏障。

普通fork还在返回前检查父P_LEXIT或当前thread不active，必要时终止新child并标记DEADPARENT（[618–636](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_fork.c#L618-L636)）。这是父退出与fork并发的实际处理，不应从“fork函数已返回”之外猜所有创建入口都相同。

## 路径2：NOTE_EXIT、SZOMB、reap是不同边界

**源码事实。** thread_terminate_self在task active_thread_count减到0的最后线程上调用proc_exit（[550–603](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/osfmk/kern/thread.c#L550-L603)）。proc_exit先proc_refdrain（[2203–2208](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_exit.c#L2203-L2208)），之后排空/reparent已有children：childdrainstart→循环处理children→childdrainend（[2367–2391](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_exit.c#L2367-L2391)、[2452–2460](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_exit.c#L2452-L2460)）。childdrain仅等待已持parentrefs者并置drain标志（[1000–1029](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_proc.c#L1000-L1029)），**本身不是所有descendants或整个PGID的join**。

proc_prepareexit此前将进程从allproc移至zombproc并标P_REF_DEAD（[2094–2112](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_exit.c#L2094-L2112)）。那里的旧注释提到pidhash，但实际所读语句没有phash_remove；不能据该注释宣称PID已释放。proc_exit在task脱离后发送NOTE_EXIT（[2529–2543](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_exit.c#L2529-L2543)），随后才在正常可由parent等待路径设SZOMB并发SIGCHLD（[2586–2623](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_exit.c#L2586-L2623)）。官方man也分开NOTE_EXIT和NOTE_REAP（[537–560](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/man/man2/kqueue.2#L537-L560)）。

**未reap身份有直接依据。** wait4在SZOMB分支最终调用reap_child_locked（[2939–2949](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_exit.c#L2939-L2949)、[3004–3007](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_exit.c#L3004-L3007)）；该函数才pgrp_leave_locked、从zomb列表与parent children移除、phash_remove，再pgrp_rele（[2806–2832](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_exit.c#L2806-L2832)）。leave移除成员并清p_pgrp；组引用降至0才destroy，且destroy要求成员为空再移除PGID hash（[2590–2602](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_proc.c#L2590-L2602)、[2310–2319](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_proc.c#L2310-L2319)、[2630–2643](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_proc.c#L2630-L2643)）。结合路径1的PID分配检查，普通未reap zombie leader仍占PID、成员与组引用，支撑PID=PGID anchor不被再分配。

**条件与推断。** 这要求可信parent独占wait、不忽略SIGCHLD/设NOCLDWAIT、不先死亡、没有另一个poll/wait/communicate偷偷reap；NOCLDWAIT/shadow/dead-parent路径可由kernel自行reap（[2552–2584](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_exit.c#L2552-L2584)、[2624–2655](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_exit.c#L2624-L2655)）。kqueue是通知，不调用wait来保留身份；NOTE_EXIT本身甚至早于SZOMB，不能作为“已进稳定zombie状态”的唯一gate。注册竞态/进程已退出时的attach行为未追filter实现，fixture应先注册、再释放child握手。

对于**这里追过的普通fork同步路径**，由最后线程进入proc_exit再到SZOMB，合理支持“该进程自身已无仍在执行、以后再发布child的fork调用”；其已成功创建child应在allproc/zombproc中，或已终结回收。该结论不是“leader的所有后代已死”：仍活的grandchild可以继续fork。未逐项证明所有非fork创建方式、异步内核创建、ptrace/exec-shadow特殊路径与host私有patch；不把这段局部顺序升级为普遍定理。

## 路径3：libproc提供有锁的有限列表，不提供事务或complete位

**实际链。** 用户态proc_listpgrppids→proc_listpids→__proc_info，kernel proc_info_internal分发到proc_listpids；pidinfo同理（[wrapper:45–109](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/libsyscall/wrappers/libproc/libproc.c#L45-L109)、[dispatch:298–306](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/proc_info.c#L298-L306)）。

- **单位/错误：**底层proc_listpids返回字节数，proc_listpgrppids再除sizeof(int)，返回的是PID个数。两者并非相同单位。底层syscall失败被wrapper转成0，pidinfo失败也返回0并保留errno；不要只判断-1。可信调用需隔离/清理errno、检查完整返回大小；0+错误不能当空组。
- **容量：**buffer=NULL返回的是全局(nprocs+20)*sizeof(int)估值，并非匹配PGID的精确计数。kernel按buffer容量与(nprocs+20)取较小分配；只填到容量便停止，无显式truncated位/所需长度（[369–389](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/proc_info.c#L369-L389)、[451–474](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/proc_info.c#L451-L474)）。size查询与下次数据查询间可变化。
- **zombies/快照：**kernel在proc_list_lock内遍历allproc，再遍历zombproc，按p_pgrpid过滤并跳过exec shadow（[391–407](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/proc_info.c#L391-L407)、[451–470](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/proc_info.c#L451-L470)）。因此正常未reap leader应列出，但不是对内核pg_members所有构造中对象的直接遍历。解锁后copyout；返回后组内出生/退出可继续发生。
- **pidinfo zombie查询须显式启用：**BSDINFO / SHORTBSDINFO / BSDINFOWITHUNIQID / UNIQIDENTIFIERINFO仅在arg非0时允许proc_find_zombref；arg0下ESRCH不证明已reap（[2122–2149](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/proc_info.c#L2122-L2149)）。proc_find_zombref从PID hash找已exited对象，并用P_LIST_WAITING与reap互斥（[784–820](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/kern_proc.c#L784-L820)）。short BSD info实际输出pid/ppid/pgid/status（[797–805](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/proc_info.c#L797-L805)）；须以SZOMB状态判断，不能只因findzomb=1就称对象已SZOMB。部分退出中进程已入zombproc但尚未设SZOMB。
- **跨调用身份/权限：**pidinfo有权限gate，扩展flags可比较idversion/uniqueid，但普通wrapper flags为0（[2143–2169](https://github.com/apple-oss-distributions/xnu/blob/d8b80295118ef25ac3a784134bcf95cd8e88109f/bsd/kern/proc_info.c#L2143-L2169)）；本轮未设计/验证这些扩展ABI。分开的list与pidinfo不是同一快照。除leader外其他PID可reap后复用；不要缓存裸PID并在稍后向它发signal。未知返回、权限错误、size错误或PGID不符都不能静默忽略。

## 本地选择：只进入一个owned fixture，不据此发布fork tier

这是作者建议，尚未实现/验证，不要求修改现有production profile。

1. 固定短寿命、无TTY的owned leader/child/grandchild，PID数与fork次数硬上限；collector是可信独占parent。leader启动握手后再执行已界定动作。先建立kqueue，再释放握手；全程不调用会reap的Popen.poll/wait/communicate，不设SIGCHLD ignore。验证leader在NOTE_EXIT后可通过带zombie查询的pidinfo达到SZOMB、仍在组列表；保留它到drain结束。
2. 在已有受控入口约束下观察/kill该owned PGID，有限重扫与状态查询直到“除anchor外，没有非SZOMB成员”；仍活成员可能继续生child，所以需再次完整列举，不能只检查初始PID集合。返回容量装满即扩容重试，有固定容量/时间上限；到上限/任何API失败便unknown。持有zombie anchor时**不要求列表为空**，因为anchor按设计仍占成员。先以原始观察验证这个命题，不能直接签发生产quiescence receipt。
3. 只有在bounded fixture中核实未观察到组逃逸、没有未决状态/容量问题、所有已见成员已终止或被可信核实回收后，才由parent最终wait leader并记录exit status。leader reap后不再对旧PGID发signal，避免身份释放后误伤。owner死亡/被竞争reap/超时保留unknown与既有quarantine；不是把kill返回成功当作join。

一个fixture可以安排leader先退出而受控grandchild仍活、并在退出边界做固定次数birth，检查“leader已SZOMB而组仍忙”是否被保守报告。该fixture只验证collector语义，不证明恶意任意fork树、所有资源副作用、kernel全部入口或外部系统静止。fork/组逃逸入口保障和可信supervisor自身故障仍是独立发布闸门；现有NoFork保持。源码证据已足够选择该实验，停止继续全kernel搜索。

