# JobClaw 同步手册

在 `【JobClaw 项目目录】` 执行。路径占位符见根目录的《填写清单》。通过 `python3 jobclaw.py status` 获取实际数据目录。CLI 的 prepare 可用 `--output` 把大载荷写入该目录。Windows 用 `py -3`。

## 初始化

```sh
python3 jobclaw.py init
python3 jobclaw.py status
python3 jobclaw.py configure --input "【配置草稿路径】" --expected-revision 0
```

后续修改前读取 `status` 和 `sync prepare` 中的当前配置。将确认后的完整配置保存为草稿，提交当前配置版本。确认 `scan_ledger.ok`。若配置提交成功、导出失败，运行 `python3 jobclaw.py export-ledger`。从生成的搜索范围与台账给发现 Agent 提供最小所需输入。

## 接收与核查

把用户交来的 UTF-8 日报接到 inbox，使用 `scan-任务标识-YYYY-MM-DD.md` 格式。同名同内容复用已有文件；同名不同内容保留并按 `-R02` 等编号另存。任务标识来自原任务，交接无法写入的文件可由本机安排同日后缀。日报正文保持原样。用户直接给链接时可以独立核查，不必构造日报。

```sh
python3 jobclaw.py sync prepare
python3 jobclaw.py sync preview --input "【同步JSON路径】"
python3 jobclaw.py sync apply --input "【同步JSON路径】"
```

载荷字段为 `idempotency_key`、`policy_fingerprint`、`input_hashes`、`batches`、`report_receipts`。指纹取 prepare 的 `agent_contract.fingerprint`，输入哈希整表照抄。重试同一载荷保持幂等键；核对后改载荷使用新键。每批一个原始来源，每份日报以名称和 SHA‑256 为单位回执。

### 来源批次

- `source` 包含 `url`、`content`、`checked_on`。同批证据来自该 URL，`content` 为实际页面相关原文或可见阻断证据（给定位语句保留独立行），`checked_on` 为实际核查日期，格式 YYYY-MM-DD。
- `companies` 可放 `id`、`name`、`source_locator`，以及有证据的 `parent_id`、`industry`、`region`、`website`、`channel`。官网和招聘入口各用一个 HTTP(S) URL。更新时加 `expected_revision`，已有主体按 ID 复用。
- `jobs` 每条需 `company_id`、`identity_key`、`title`、`direction`、`posting_status`、`source_url`、`source_locator` 和 `jd_quality`。尽量补 `city`、`department`、`batch`、`recruitment_type`、`deadline`、`salary`、`degree`、`experience`、`application_url`、`hc_status`、`status_note`。未知招聘类型用 `unknown`，未确认招聘名额用 `unconfirmed`。以当前契约提供的状态值为准。更新时加 `expected_revision`，只提供实际核查过的改动。一次未见或入口失败可以在 tracking 留下范围说明。原有已核证的时间与状态按实际证据更新。
- `full` 的 JD 需 `description`、`requirements` 两项真实原文；`partial` 至少有一项原文。这两种情况均需 `jd_source_url`、`jd_checked_on`，与本批来源一致。`missing` 时留空四个 JD 字段。
- `tracking` 每条含 `id`、`company_id`、`kind`、`reason`、`scope`、`next_action`、`source_locator`。用 `status` 区分 `open` 和 `resolved`，关联具体岗位时用 `job_id` 或已登记的 `job_identity_key`。更新沿用核查 ID，并带 `expected_revision`。技术受阻用 `blocked`，附 `blocker_type`、`blocker_evidence` 逐字引文、`blocker_url`（与本批 URL 相同）；一般缺项用 `uncertain`。公司级记录可省略岗位字段。
- `source_locator` 应在本批 `content` 的某一行中逐字可定位，可用原始标题或错误提示。对重复来源和已有完整 JD 保留证据强度与核验日期。

preview 结束后检查新增、更新、重复和冲突。确认后提交同一载荷。取回执、岗位计数、个人标记保留和导出结果。apply 成功而 `scan_ledger.ok=false` 时，数据库已经提交，运行 `export-ledger` 修复生成文件。

### 日报和复查回执

每个回执包含 `name`、`sha256`、`status`、`scope`、`note`。整份都有处置才用 `processed/all`。只处理选定项时用 `partial/selected`，只补受阻用 `partial/blocked_only`。处置可以是已录入、已确认重复、明确排除或已登记带范围的受阻。note 列出线索编号、结论和剩余范围。整份零新增的合法报告可以提交空 `batches` 和该份回执。

处理 Web 复查队列时，可加 `review_completions` 数组，元素为请求的 `id` 和 `result`。对应追踪记录和本次原始证据须在同一同步里。入口仍受阻时追踪可保持 open，请求是否结束取决于本次用户要求和实际核查结果。

## 评估和重评

```sh
python3 jobclaw.py review prepare
python3 jobclaw.py review preview --input "【重评JSON路径】"
python3 jobclaw.py review apply --input "【重评JSON路径】"
```

顶层为 `idempotency_key`、`policy_fingerprint`、`input_fingerprint`、`review_head`、`summary`、`rules`、`reviews`。复制 prepare 的两个指纹和 `review_head`（首轮为 null）。没有可归纳的当前个人理由时 `rules` 填空数组；有归纳时，每条填 `title`、`interpretation`、`scope`、`evidence`（数组元素为 `job_id` 与当前理由的逐字 `quote`）。

全部岗位各提交一条 review，字段为 `job_id`、`job_revision`、`ai_score`、`reason`、`gaps`、`basis_quote`。已取得原文时引用与该判断相关的一段原文。JD 未取得时 `ai_score=null`、`basis_quote=""`。0 分额外传契约允许的 `mismatch_type`、明确的理由和原文依据。核实不足可用 null；没有待核事项可在 gaps 写「无」。即使岗位结束，也根据个人规则判断匹配度。个人正分保持优先展示。
