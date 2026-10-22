# 更新日志

## v1.0.5（2026-10-22）

- 修复 token 轮换后索引断裂（issue #1）：保存时同时更新原始配置指纹与当前令牌指纹，下一轮仍用原环境变量 / Secret 启动也能恢复最新 access / refresh token；
- 新增 Actions 跨运行状态保存：加密缓存 `qoder_tokens.json` 与 `qoder_machine_id`，配置 `QODER_CACHE_KEY` 后公开 / 私有 fork 都可安全续用；
- Actions 在脚本异常时也会尝试保存已完成的 token 轮换，避免旧 Secret 中的刷新令牌失效后无法恢复；
- 新增无凭据回归测试与 tests workflow（7 项 token store 测试全部通过）。

## v1.0.4（2026-10-20）

- 新增一次性 Pro 升级包：`eligibility → claim`，成功领取 +1800 积分。
- 已领取、活动未开放、404/403/410 均按中性状态处理，不重复计数。
- 复用现有设备风控头与 token 续期链路。

## v1.0.3（2026-10-10）

- 签到脚本支持国际版（qoder-intl）每日 100 Credits：国际版账号用 `QODER_GLOBAL_TOKENS` 单独配置，自动走 `openapi.qoder.sh`。
- 国际版风控身份分层：环境变量三件套（`QODER_MACHINE_TOKEN` / `QODER_MACHINE_CODE` / `QODER_MACHINE_TYPE`）、`QODER_UMID_PATH` 组件、本机 Qoder 客户端 / qodercli；Linux / 青龙缺组件时可从官方包提取（sha512 校验，`QODER_UMID_NO_INSTALL=1` 关闭）。
- 活动窗口（每天 10:00 UTC+8）未开或风控身份缺失时不落「已签」状态、如实提示，后续轮次自动补领；401/403/429 与非 CLAIMED 状态按上游口径回报。

## v1.0.2（2026-10-09）

- 仓库描述与 README 顶栏同步：明确「中国版每日签到 + 国际版设备授权登录」。
- 说明澄清：每日签到活动仅中国版有，国际版用于设备授权登录获取 Token。

## v1.0.1（2026-10-09）

- Actions 工作流默认改为**仅手动触发**：上游仓库不再空跑定时任务。
- fork 后如需定时，取消 `.github/workflows/daily.yml` 里 `schedule:` 的注释即可。
- README 同步补充 fork 启用定时的说明。

## v1.0.0（2026-10-09）

- 首个公开版本
- 首个版本：Qoder 中国版每日签到 + 设备授权登录工具，支持多账号、Token 自动续期、多渠道推送。

> 每个版本单独发布 Release，历史版本保留可下载。
