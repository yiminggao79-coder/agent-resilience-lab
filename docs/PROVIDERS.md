# 模型协议与配置

三种 wire 协议各自实现，不用"换个 base URL 就兼容"的偷懒做法。原因是工具调用表示、结果回传、推理状态与终止信号在三种协议里完全不同，混为一谈会在流式与续传上出错。

## 协议对照

| 协议 | 实现要点 | 实际验证到什么程度 |
|---|---|---|
| Chat Completions（JSON / SSE） | 工具 schema 翻译、`tool_calls` 完整门控、随机 receipt 回传验证反馈真被使用 | DeepSeek 官方 `https://api.deepseek.com` 真实多轮 Coding 使用；**非流式**用于长任务 |
| OpenAI Responses | 完成项、reasoning 与 native 续传、`response.completed` 终止判定 | 本地 HTTP/SSE 契约 + 兼容端点修复 `error:null` 后取得完整响应；原生 OpenAI 真实任务未测 |
| Anthropic Messages | 有序 thinking/signature/tool_use、工具结果、`message_stop` | 本地契约；DeepSeek 官方 Anthropic 兼容基址的 SSE + Thinking low + 工具反馈实际通过 |

三协议共用的完成门控：只看 HTTP 200 不算完成，EOF 不算完成，半段 JSON 不算完成。参数校验通过且终止标记确认后，才允许进入工具执行。`--stream-text` 与 `--stream-tools` 是显示选项，不是完成回执。

## 端点配置

```sh
.venv/bin/python -I -m agent_lab providers
```

供应商配置存放在受信 JSON 中，每个协议有各自的默认 Key 环境变量。硬性限制：

- 凭据只注入明确端点；拒绝隐式代理、跨主机重定向与远端明文。
- 需要显式 `--allow-api` 才会真实发起付费请求。
- 项目命令、测试、MCP 子进程不继承模型 Key。

## 费用账本

`BudgetLedger` 是独立 SQLite 文件，必须放在**被操作工作区之外**，程序不会自动创建或重置它。

- 预留 → 结算 → 未知三种记录分离；未知用量不按零元处理。
- 收到 HTTP 402 立即停止付费调用。
- 估算按用户给定价格与峰时上界计算，**不是发票**，也不代表供应商实际扣费。
- 历史账本不可删除或重建来"释放"未知额度。

## 推荐参数

长任务场景（也是冻结验收使用的配置）：

```
--max-steps 50 --max-seconds 900 --model-timeout 180 --tool-timeout 12
--max-output-tokens 16384 --max-context-bytes 131072
--max-summary-input-bytes 32000 --auto-compact-bytes 104857 --max-auto-compactions 6
--thinking enabled --reasoning-effort low
```

注意 `131072` 是**字节**，不是 token。字节启发式实现简单、可离线复现，但与任何一家 provider 的 tokenizer 都不等价。

## 后续用法

```sh
# 同一个 DB 与 workspace 上继续
.venv/bin/python -I -m agent_lab status TASK_ID
.venv/bin/python -I -m agent_lab turns TASK_ID
.venv/bin/python -I -m agent_lab continue TASK_ID '追加需求' --workspace /path/to/project
.venv/bin/python -I -m agent_lab resume TASK_ID --workspace /path/to/project
```

`approval / approve / deny` 展示精确动作摘要；`pending` 解释未知状态，不会替操作者确认副作用。
