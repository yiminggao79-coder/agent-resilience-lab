# 原始完整门槛失败保持不变

本次真实模型摘要已安装（generation1、覆盖25条原消息、2471 UTF-8字节），并出现在实际后续模型请求中。摘要请求tools为空，内部文本callback被静默；原始消息archive未改，模型同Session第二Turn完成exclude功能。实际原/新外部oracle和两个输入未被修改的探针通过。

原始report.json的task_success=false不改。原因是tests/test_regressions.py在原文件中追加了新的NormalizeExcludeRegressions类，触发既有测试文件byte hash不变门槛。changes.diff显示只追加测试，没有删除或改写原测试方法。模型任务要求preserve all existing tests，并要求新增回归；对整个文件不可追加这一点，提示比评分条件更含糊。不能直接据此断言模型篡改了原测试结果，也不能事后把门槛改为通过。

另一个独立副本恢复了starting-project的全部原测试，保留候选生产代码，run_tests退出码0。补充结果在posthoc_diagnostic.json，仅诊断，不重写原报告/分数。该副本没有进入Agent工具工作区。下一套任务应把“保留旧用例”与“既有文件逐字节只读、新测试只能新文件”明确区分，并在用户需要时使用通用host只读路径策略；不按此题硬编码。

压缩前实际请求视图25343字节，之后21948字节；这是一次短真实会话的压缩与续开发，不是百万token长期任务质量证据。七次API请求（含一次摘要）、九次工具尝试、42.11秒，输入48310/输出2069。全程源码指纹未变。原任务及生成代码可从candidate/及diff核查。
