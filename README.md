# JobClaw 工作雷达

JobClaw 是一套面向个人求职的 Agent 工作流。你和 Codex 对话设置求职目标、履历和评分标准。Codex 找岗位、核查招聘原文、去重、评分并入库。你在本地工作台查看结果，按条件筛选、收藏、标记投递或手动改分。

用 Codex 就能开始。定时扫描是可选项，适合希望分渠道持续发现新岗位的人。

## 第一次使用

1. 在 GitHub 页面点 **Code → Download ZIP** 并解压，或从 **Code** 复制地址后克隆。用 Codex 打开包含 `jobclaw.py` 的目录，并确认本机有 Python 3.10 或更新版本。Windows 可以将下文的 `python3` 换成 `py -3`。
2. 复制下面这句话给 Codex。它会和你确认规则，用几条真实岗位试评，再写入你的本地工作区。首次启动从空库开始。

   > 用 JobClaw 帮我初始化工作雷达。先问我想找什么、做过什么、哪些岗位优先、哪些明确不考虑。整理成搜索范围和 0—3 分规则，找几条真实岗位和我试评。等我认可后保存配置，再核查这批岗位、入库和评分，最后打开本地工作台。

3. 让 Codex 打开工作台，或在项目根目录自行启动：

   ```sh
   python3 jobclaw.py serve
   ```

   在本机浏览器打开 [工作台](http://127.0.0.1:18765/)。服务运行期间保持启动它的进程。已有进程占用该端口时，按[填写清单](填写清单.md)换一个端口。

Codex 在项目内使用[工作雷达 Skill](.agents/skills/jobclaw/SKILL.md)。如果 Skill 列表里找不到 `$jobclaw`，让 Codex 直接读取该文件。需要浏览器才能查看的来源，可由 Codex 使用当前可用的浏览器工具核查。

## 日常使用

| 你想做什么 | 可以直接对 Codex 说 |
| --- | --- |
| 找一批新岗位 | 「按我的搜索范围查一批新岗位，核查来源后入库评分。」 |
| 查指定链接 | 「核查这几个招聘链接。职责和要求保留原文，招聘状态单独标注。」 |
| 处理扫描日报 | 「核查并处理最新日报。」 |
| 补查受阻页面 | 「补查我在工作台里标记的受阻入口。」 |
| 修改偏好并重新排序 | 「把目标加上另一个城市，再按新标准重评。」 |

Codex 处理完后，刷新工作台查看最新结果。重评会保留收藏、投递和手动评分。

## 接入定时发现

1. 完成首次配置后，运行 `python3 jobclaw.py status` 查看本地工作区和 `inbox` 的实际位置。工作区生成的 `search-scope.md` 和 `scan-ledger.md` 分别提供搜索范围和去重台账。发现 Agent 还可读取尚未处理的日报，用于减少重复发现。
2. 复制[通用扫描提示词](templates/定时扫描提示词.md)，按[填写清单](填写清单.md)替换 `【】`。每条任务分配独立标识和扫描渠道，并在对应 Agent 的调度器设置运行时间。没有定时器时也可手动使用模板。
3. 本机 Agent 可以直接把日报写入 `inbox`。云端 Agent 可以交付 Markdown 文件或完整正文，再把结果交给 Codex 放入本机 `inbox`。日报文件名形如 `scan-official-roles-2026-01-15.md`。
4. 有了日报后，对 Codex 说「处理这些日报」。扫描任务负责发现和记录，Codex 负责深挖、入库和评分。私人履历和精筛规则留在本机配置里。

## 本地运行数据

`python3 jobclaw.py status` 返回实际工作区与 inbox 路径。默认数据、日报、配置草稿和同步 JSON 都保存在根目录下被 Git 忽略的 `.jobclaw/`。运行 `python3 jobclaw.py init` 可初始化目录并生成 `search-scope.md`、`scan-ledger.md`，也可在已有库上安全重复运行。

规则改动后，当前规则下还未评估的岗位显示为待评估。重评历史、收藏、投递及手动评分继续保留。招聘状态、核查日期和匹配分分别展示。网页只监听本机地址。

## 开发检查

```sh
python3 -m unittest discover -s tests -v
node --check app/static/app.js
```

Python 测试使用临时数据和虚构来源。Node 仅用于开发时的 JavaScript 语法检查。

## 开源许可证

JobClaw 的代码、Codex Skill、提示词模板和文档采用 [GNU Affero General Public License v3.0](LICENSE)（AGPL-3.0-only）。

Copyright © 2026 john-ops-lab
