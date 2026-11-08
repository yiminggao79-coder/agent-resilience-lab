# MCP Streamable HTTP：有界源码研究与本地范围

2026-11-08。Codex固定 `e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9`；官方MCP规范使用日期版本2025-11-25，Python SDK另固定以下commit。只读公开源码/规范及本地mcp.py；无外部MCP服务器调用、runtime修改、安装、模型API、key/ledger/private checkpoint/V3/frozen读取或Git操作。未执行上游测试；不称wire parity、远端副作用可回滚或完整OAuth支持。

**决定依据：HTTP可作为现有MCP协议处理的一个独立传输，但不是stdio换个URL。当前官方Python SDK不能直接装进Python3.9；若保留本项目运行环境，先做窄的独立transport，保留现有JSON-RPC校验、catalog限制与unsafe未知效果语义。**

## 体级证据与真实短链

以下仅指定范围已读，其他分支不作完整研究声明。源码归属OpenAI，未复制实现体；现有Apache-2.0归属见LICENSE/NOTICE。

| 固定Codex文件 / blob | 已追机制 |
|---|---|
| `codex-rs/config/src/mcp_types.rs` / `4a81cabe3763ef573dc09d2689b5a3febf66a80a` | [config:672–689](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/config/src/mcp_types.rs#L672-L689) StreamableHttp字段：url、bearer env名、固定/env headers、local header helper。配置字段实际存在，不表示本地应全复制。 |
| `codex-rs/codex-mcp/src/rmcp_client.rs` / `c6440742da182bd67a1a8986fe655cb902b7d67c` | [entry:1538–1617](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/codex-mcp/src/rmcp_client.rs#L1538-L1617) make_rmcp_client的HTTP分支，resolve auth→server-specific HttpClient→redirect mode→new_streamable_http；[entry:1156–1187](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/codex-mcp/src/rmcp_client.rs#L1156-L1187) start_server_task调initialize。 |
| `codex-rs/rmcp-client/src/rmcp_client.rs` / `221336dfc813b41d70fdf16554525bb5306e3ab4` | [client:626–733](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/rmcp_client.rs#L626-L733)构造/Connecting→initialize→Ready；[client:1527–1546](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/rmcp_client.rs#L1527-L1546)adapter+rmcp transport；[client:1551–1637](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/rmcp_client.rs#L1551-L1637)serve_with_lifecycle；[client:1026–1154](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/rmcp_client.rs#L1026-L1154)tool call；[client:1315–1343](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/rmcp_client.rs#L1315-L1343)Closed；[client:1754–1848](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/rmcp_client.rs#L1754-L1848)retry边界。 |
| `codex-rs/rmcp-client/src/http_client_adapter.rs` / `f6d88975c85c437877674474873dc0b8fd57c35d` | [adapter:118–206](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/http_client_adapter.rs#L118-L206)headers与取消特例；[adapter:282–319](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/http_client_adapter.rs#L282-L319)404/401/403/Accepted；[adapter:370–445](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/http_client_adapter.rs#L370-L445)JSON/SSE；[adapter:458–508](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/http_client_adapter.rs#L458-L508)DELETE；[adapter:794–815](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/http_client_adapter.rs#L794-L815)redirect选择；[adapter:962–1006](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/http_client_adapter.rs#L962-L1006)body/SSE尺寸helper。 |
| `codex-rs/rmcp-client/src/http_client_redirect.rs` / `5d8cfee05f889421aa003bc37bae074d4c1a1475` | [redirect:40–151](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/http_client_redirect.rs#L40-L151)逐跳自检、same-origin、共享deadline与方法变化；MAX_REDIRECTS=10在24行。 |
| `codex-rs/rmcp-client/src/request_cancellation.rs` / `5ec49d70e84d90f9d9c14d20455fd78990121c4b` | [cancel:38–80](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/request_cancellation.rs#L38-L80)request scope取消/等待写入；[cancel:90–131](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/request_cancellation.rs#L90-L131)OnDrop→notify_cancelled。 |

可审查链：host transport config→make_rmcp_client HTTP分支→transport recipe/Connecting→initialize取走pending transport→StreamableHttpClientTransport包adapter→serve_with_lifecycle→读取peer_info并置Ready→call_tool_inner构造CallToolRequest并等ServerResult::CallToolResult→shutdown置Closed。

**证据终点明确**：initialize/initialized实际包的生成、MCP-Protocol-Version何时由rmcp写入，以及shutdown时库是否必发DELETE，委托Rust SDK内部，本轮未展开；不能将本链称完整底层handshake/close追踪。adapter有delete_session实现并处理405，但RmcpClient.shutdown_for只证明Closed/释放service，本身不显式调用DELETE。它更不证明远端工作停止。

## 规范事实（2025-11-25）

[官方Streamable HTTP规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#streamable-http)：每个客户端JSON-RPC消息单独POST，Accept同时含application/json和text/event-stream；通知/响应接受时202无body，请求响应可以JSON或SSE，client须支持两者。初始化可返回MCP-Session-Id，之后请求携带；后续HTTP请求还带协商的MCP-Protocol-Version。session 404要求重建session；退出应DELETE，405允许。断流不等于取消，应显式cancel notification；GET可用于通知或Last-Event-ID恢复。SSE断开/重连规则不能被描述成自动安全重放工具。

这里不将spec的server-side Origin/auth要求当成client已经实施的保证；也不展开OAuth标准。与本地现有支持2025-11-25/2025-06-18的协商保持区分：固定Codex另外有2026-07-28现代协议分支，不能照搬其events/stream/discovery行为并称本地2025兼容。

## 最有价值的5个本地机制

**1. 状态与headers，而非每call临时无状态HTTP。** Codex明确Connecting/Ready/Closed，启动后保留service/capabilities；adapter构造Accept/Content-Type、可选Authorization和session header，custom headers也参与。Python SDK的[prepare/extract实现](https://github.com/modelcontextprotocol/python-sdk/blob/52e64ddb9f8f897bcb74de0bc9417f820af35a59/src/mcp/client/streamable_http.py#L174-L219)则清楚保存session ID与initialize结果version，之后每请求补两种MCP header。本地应复用start→initialize→initialized→catalog→call状态，限制session/version/header大小，禁止model自行覆盖协议或授权header；session ID不进入metadata Trace。

**2. JSON与SSE都有最终JSON-RPC权威边界。** Codexadapter按Content-Type分流；JSON收body再parse，SSE交sse parser，仍由service按request ID处理；204/202表示HTTP accepted，不能直接当tools/call成功。当前adapter body cap是Option，且modern/discovery与普通legacy分支不同，不能宣称所有上游HTTP/SSE都有同一总量上限。本地需给headers、JSON body、SSE行/event/总bytes与完整操作deadline各自有限预算，处理server通知/请求后直到匹配final result/error；partial SSE或EOF不能生成成功tool feedback。现有mcp.py的重复key/非法数值/响应ID/结果结构/catalog限制值得复用。

**3. Auth与redirect是authority边界。** Codexadapter所有HTTP client先包SameOriginRedirectHttpClient；Follow也会逐跳检查原origin，最多10跳，共享deadline；旧301/302 POST与303还可能转bodyless GET。部分现代/agent auth模式直接Stop。不能从Follow enum推导允许跨域。SDK v1.x同样使用within-origin helper，但其注释另允许特定同host HTTP→HTTPS升级；本轮未展开SDK helper实现，不称与Codex规则完全一样。本地首版可直接拒绝所有redirect、URL userinfo/fragment、ambient proxy及OAuth自动发现，只接受明确host配置的endpoint与TLS策略；认证支持另行显式授权，不从其他软件凭据或DeepSeek变量获取。无auth静态fixture足够先验传输。

**4. 重连不等于重放安全。** Codex [client:1775–1788](https://github.com/openai/codex/blob/e6bf88ae022cdab968e5a48d5cbeea4d2e304fc9/codex-rs/rmcp-client/src/rmcp_client.rs#L1775-L1788) 在session 404后reinitialize并再执行operation；一般transient retry只允许label=tools/list（1822–1844）。这不提供外部exactly-once证明。本地可对future操作重新initialize，但已发出的unsafe tools/call若失败/超时/断线仍unknown，不能为修session自动再发同call。不要把旧session-id或SSE cursor持久化成跨restart的已确认效果凭据。

**5. Cancel/close只是尽力控制。** OnDrop helper通知peer取消并等待/跟踪写入；普通路径可在future drop后spawn通知task，不能据此证明服务器接受、更不能证明副作用停止。adapter另有现代event-stream取消直接关闭本地stream的特例，不能推广成2025远端取消确认。DELETE405被接受且其他失败可见；SDK terminate_session错误只记录warning。首版可有界发送cancel/DELETE并关闭连接，保持unknown effect，不能给远程MCP贴native local_quiescent标签。外部server始终是显式trusted extension，不在command sandbox内；现有host read-only模式对MCP的拒绝不应因换HTTP而绕过。

## 官方Python SDK与Python3.9的实际限制

未安装或执行SDK。只读官方pyproject与v1 HTTP体：

| 固定官方SDK来源 | 已核实 |
|---|---|
| main commit `77001c96f9507be0f4d009b83210a384a5e08bda`；pyproject blob `6d7626d79b4a78c66e5dd1837b28530e677a0d34` | [pyproject1–6](https://github.com/modelcontextprotocol/python-sdk/blob/77001c96f9507be0f4d009b83210a384a5e08bda/pyproject.toml#L1-L6)要求Python>=3.11。不是本地installed版本检测。 |
| v1.x commit `52e64ddb9f8f897bcb74de0bc9417f820af35a59`；pyproject blob `6bee7f4923e43865081411a3c48455a4930f2af5` | [pyproject1–6](https://github.com/modelcontextprotocol/python-sdk/blob/52e64ddb9f8f897bcb74de0bc9417f820af35a59/pyproject.toml#L1-L6)要求Python>=3.10。 |
| 同v1.x src/mcp/client/streamable_http.py，blob `4fd743b9c4ce6979814f7f10f44193e9f0cefaff` | [355–418](https://github.com/modelcontextprotocol/python-sdk/blob/52e64ddb9f8f897bcb74de0bc9417f820af35a59/src/mcp/client/streamable_http.py#L355-L418)POST/JSON分支；[597–612](https://github.com/modelcontextprotocol/python-sdk/blob/52e64ddb9f8f897bcb74de0bc9417f820af35a59/src/mcp/client/streamable_http.py#L597-L612)DELETE；[638–648](https://github.com/modelcontextprotocol/python-sdk/blob/52e64ddb9f8f897bcb74de0bc9417f820af35a59/src/mcp/client/streamable_http.py#L638-L648)redirect/context close说明。代码含3.10 union类型语法，不能在3.9上称官方支持。 |

选择只有两种明确路径：保持3.9，用stdlib HTTP+既有有界SSE/JSON处理独立实现窄协议；或未来单独批准升级整个项目Python与依赖后再采用SDK。本轮不建议复制/改声明使SDK“支持3.9”，也没有证明早期历史SDK版本满足当前协议与安全要求。

## 建议下一有限实现边界（未实现）

保留stdio默认/配置兼容，另加显式Streamable HTTP类型；host配置url、有限timeout/bytes/catalog，不允许工具参数选endpoint、headers、代理或环境。可先支持HTTPS，以及仅synthetic测试显式启用loopback HTTP；无OAuth、动态header shell helper、legacy双endpoint SSE fallback、background GET、Last-Event-ID恢复或自动replay。该范围可处理POST的JSON/SSE与session初始化，但必须明确通知push/恢复兼容限制，不称完整规范实现。

先用本地synthetic HTTP barrier contract验证initialize/header/202/JSON/SSE、非匹配ID与通知、session404不重放call、invalid/truncated/oversized内容、TLS/redirect拒绝、超时cancel/DELETE/unknown、private headers/raw结果不进Trace，及stdio兼容。原server catalog/工具决策/审批语义不因transport改写。没有真实外部server通过记录，也不从源码阅读宣称服务兼容。

未追：rmcp完整wire handshake与transport Drop、OAuth/keychain/provider/refresh、HTTP executor底层TLS/proxy行为、SDK within-origin helper与SSE reconnect全部体、全部2026协议/事件扩展。证据已经足以选择一个3.9可实施的窄HTTP transport；本研究到此停止。

