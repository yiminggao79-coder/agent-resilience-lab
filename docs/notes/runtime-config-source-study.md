# Codex配置层调用链与本地显式Host配置

固定版本`e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`，三个源码blob与原始下载身份
验证、实际阅读行段和逐段正文在evidence/codex-config/manifest.json。
最初旧core/config_loader路径404，随后从同commit公开tree定位新config crate；没有用错误路径作结论。
Python默认CA请求失败后使用系统curl验证TLS读取公开tree，最终正文从GitHub connector按固定ref取回。
没有关闭证书验证、访问用户Codex配置/凭据或执行原版同题。

## 已核实的状态与调用路径

1. [loader:147–210](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/config/src/loader/mod.rs#L147)
   `load_config_layers_state(fs,codex_home,cwd,cli_overrides,options,thread_config_loader)`
   解构LoaderOverrides/strict/cloud，读取指定或embedded packaged defaults，并把相对路径转换为
   该层base目录下的绝对语义。这里没有把“当前目录下随便一个文件”直接当全部有效权限。
2. [loader:240–294](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/config/src/loader/mod.rs#L240)
   先获得thread配置层、建立layers并push默认层；CLI override通过`build_cli_overrides_layer`
   构造，strict模式先校验，相对路径以cwd或codex_home为base；随后load system层。
3. [loader:420–525](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/config/src/loader/mod.rs#L420)
   完成project trust上下文后追加project layers，CLI/UI覆盖作为`SessionFlags`独立层，thread层
   按优先级插入，legacy managed/MDM再入栈，validate enabled layers后构造ConfigLayerStack。
   本文未完整展开project发现、全部trusted broker或requirements供应链，不声称全部已还原。
4. [state:315–350](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/config/src/state.rs#L315)
   构造时验证enabled层与ordering，并单独保存requirements/model provider requirements；
   [state:650–665](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/config/src/state.rs#L650)
   `effective_config`遍历低→高层merge，再应用provider requirements；
   [state:724–742](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/config/src/state.rs#L724)
   该iterator过滤disabled层，反向iterator复用正向而非另一套语义。
5. [overrides:9–100](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/config/src/overrides.rs#L9)
   CLI项按dotted key逐一写入TOML table；structured feature支持boolean/table特殊merge，
   普通末节点插入或替换，中间非table节点被转换。并非简单字符串拼接执行命令。

**推断与选择**：原版用分层provenance、信任状态及单独requirements支持多来源配置，
其复杂度服务于云/组织/项目/用户/UI生态。本地三天预算里最迫切的问题是多个CLI动作
重复八类授权；采用一个显式Host文件足以提高实际使用效率，且能复用已有runtime identity。
这是受上述机制启发的独立窄方案，不是上游config.toml/profile/MDM的兼容实现。

## 我们如何实现与保留差异

`runtime_config.load_runtime_config`读取≤32KiB工作区外绝对JSON，普通单链接文件、
最终路径不可直接symlink，沿途不可经过可写workspace；同一FD前后与最终path metadata
比对，重复key/NaN/坏UTF-8/深度/未知字段/错类型统一固定错误，不回显正文或路径。
只允许version1与现有Tools kwargs/read_only_paths。没有model/key/env/budget/自动安装字段。

`add_runtime_arguments`令权限参数默认SUPPRESS，`resolve_cli_runtime`先判断用户是否
真的提供这些参数，再恢复旧默认。因此`--command-backend nofork`和唯一缩写也被准确
识别为显式覆盖；与配置混用直接拒绝。本地故意不做隐式merge：文件或flags二选一，
降低权限来源判断成本。model-config/MCP仍各自显式入口，不在此合并。

实际权限仍经Tools、whole runtime/read-only policy构造；配置文件位置、空白、mtime变化
不改变有效权限即可复用，SDK/入口字节或scope变化则由既有state/审批校验拒绝。
没有用config SHA代替可执行文件/资源描述符，也没有把新配置加载当恢复成功。

`runtime-check`只构造同一Tools admission并返回manifest/read-only scopes及两个false标记，
先于Store/model/key分支返回；不spawn、不创建Task/DB/control目录、不证明某工具能工作。
真实执行仍须run与精确审批。没有命令给任何MCP服务、子进程或模型新增权限。

## 可验证与局限

首轮19相关/0skip14.270s；追加真实FD读取中改写反例后10新测试/0skip4.923s。
实际freshCLI仅使用一份Host配置：registered Ruby读取资源→精确审批→恢复→新session fork；
改变grant/资源字节拒绝，readonly测试不变、all_native_joined。决策Scripted，不是LLM分数。
default runtime-check返回legacy None且不写Root/DB；坏config先于Key/Store，冲突先于读文件。
最终独立审查及整仓结果以EVALUATION为准。

配置是Host信任输入，不对抗恶意Host并发改写路径；稳定FD保证本次一致读取，
不声称永久锁定整个文件系统。没有自动发现、配置编辑器、层来源UI、云/组织policy或TOML兼容。
下一项应继续真实Coding/长任务/交互能力，不能因配置可用就宣布Codex级完整复现。
