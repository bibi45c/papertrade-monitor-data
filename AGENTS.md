# Papertrade data archive

初版项目规则，供人工审核。

- 用户已批准私有仓库 `bibi45c/papertrade-monitor-data`，用于归档现有监控的公开市场数据。代码仓库与数据仓库分开。
- D1 为近期缓存：分钟明细保留 14 天，更早每个 UTC 小时只留首条、保留到 90 天。归档通过 Worker 的只读分页 `/api/archive` 查询，每页最多 120 条；首尔服务器每 15 分钟拉取、每天 00:30 UTC 推送，GitHub Actions 仅作手动备用（两者不得同时定时运行）。按 UTC 小时存储 gzip JSONL，保留完整快照与原始金额字符串；D1 抽稀或过期不删除已归档文件。
- 服务器只用仅对本仓库有写权限的部署密钥提交，手动 workflow 用本仓库自带的 `GITHUB_TOKEN`。禁止存储账户凭据、钱包、私钥或个人交易数据。
- 验收命令以 README 和 workflow 为准；归档脚本仅依赖 Python 标准库，workflow 使用 Python 3.12。
- 未知、故障与过期字段不能替换为零。相同分钟必须幂等；数据变更、分页异常和数量不匹配必须报错。缺失分钟和来源字段失败分别记录。
- `chain-backfill/` 保存按区块读取的链上状态（每 5 分钟，含区块号与原始整数），只用于补分钟归档缺失的字段，与 `snapshots/` 分开存放、不得合并；`series/exposure-series.json` 由首尔服务器每天用 `scripts/build_series.py` 从两者生成。`scripts/chain_backfill.mjs` 为一次性 Node 复现脚本，归档主流程仍只依赖 Python 标准库。
- 新架构、安全边界、数据删除、付费和生产变更遵守用户全局审批。批准的恢复方式是停用服务器定时器（必要时恢复 workflow 定时）、回退 Worker，保留两处数据。
- 此文件首版人工审核后，修改必须先提供完整 diff 并取得明确批准。
