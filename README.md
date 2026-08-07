# Daily Paper Digest Skill

一个可配置、可审计、跨 harness、跨平台的每日论文推送 Skill。它负责近期论文搜索、历史去重、按类别配额选文、逐论文原图归档、STAR 结构的详细中文笔记、Zotero PDF 深链与分类，以及失败后可重试的每日定时运行。

同一份标准 `SKILL.md` 可安装到：

- Codex：`~/.agents/skills/daily-paper-digest`
- Claude Code：`~/.claude/skills/daily-paper-digest`
- Qoder IDE / CLI：`~/.qoder/skills/daily-paper-digest`
- QoderWork：`~/.qoderwork/skills/daily-paper-digest`
- 其他兼容 Agent Skills 的 harness：使用自定义目标目录

支持 macOS、Linux 和原生 Windows。Windows 安装器使用 PowerShell，定时运行使用 Windows Task Scheduler。

## 它解决什么

- 用户显式指定推送时间、IANA 时区、每天总篇数。
- 用户显式指定任意论文类别、检索词、arXiv 来源分类和每类精确配额；也支持权重自动换算整数配额。
- 默认 15 篇配比：生成 3、理解 4、Agentic RL 3、具身/VLA/WAM 3、其他 2。
- arXiv ID 去版本去重，并对标准化标题二次去重；只有完整验证成功才写历史。
- 每篇论文的图片固定放在 `images/<paper-slug>/`，验证器禁止笔记引用其他论文的图片。
- 每篇笔记强制按 STAR 展开：Situation 交代场景与失败模式，Task 定义输入输出、目标和约束，Action 严格跟随论文 Method 的实际部分数和原始顺序逐部分翻译、解释，并保留必要公式、逐变量说明其作用和通俗直觉；Result 用指标、基线、消融与证据收束。Method 只有一部分就写一部分，有几部分就写几部分，不为满足固定数量人为拆分或合并。
- 原图必须放在对应论文的 `images/<paper-slug>/` 下，并紧跟所解释的 Situation、Action 或 Result 内容；至少一张可用原图必须出现在 Action 中。
- 每个 Markdown 笔记可包含 `zotero://open-pdf/...` 链接，点击直接打开 Zotero 中的原论文 PDF。
- 定时任务每分钟做一次轻量 due-check，只在配置的时区和时间运行一次；失败不会污染去重历史，并会按配置重试。

## 环境要求

- Python 3.11+
- 至少一个已安装并完成登录的 agent CLI：`codex`、`claude` 或 `qodercli`
- 可访问 arXiv
- Zotero 7（仅在启用 Zotero 集成时需要）
- Windows 建议先安装时区数据：

  ```powershell
  py -3 -m pip install -r requirements-windows.txt
  ```

## 安装

### macOS / Linux

安装到 Codex、Claude Code 和 Qoder，并让 Codex 执行每日任务：

```bash
git clone https://github.com/zlynpu/codex_search_paper_skill.git
cd codex_search_paper_skill
./install.sh \
  --harness all \
  --agent-harness codex \
  --archive-root "$HOME/paper-notes" \
  --time 09:00 \
  --timezone Asia/Shanghai \
  --total 15 \
  --quota generation=3 \
  --quota understanding=4 \
  --quota agentic_rl=3 \
  --quota embodied_vla_wam=3 \
  --quota others=2
```

只安装、不注册系统定时任务时加 `--no-schedule`。

### Windows PowerShell

```powershell
git clone https://github.com/zlynpu/codex_search_paper_skill.git
Set-Location codex_search_paper_skill
py -3 -m pip install -r requirements-windows.txt
.\install.ps1 `
  -Harness all `
  -AgentHarness codex `
  -ArchiveRoot "$HOME\paper-notes" `
  -Time "09:00" `
  -Timezone "Asia/Shanghai" `
  -Total 15 `
  -Quota @("generation=3", "understanding=4", "agentic_rl=3", "embodied_vla_wam=3", "others=2")
```

安装器会注册当前用户的 `DailyPaperDigest` 计划任务，不需要管理员权限，但用户需要处于登录状态。检查与立即触发：

```powershell
Get-ScheduledTask -TaskName DailyPaperDigest
Start-ScheduledTask -TaskName DailyPaperDigest
```

### 单独安装某个 harness

```bash
./install.sh --harness codex --agent-harness codex
./install.sh --harness claude-code --agent-harness claude-code
./install.sh --harness qoder --agent-harness qoder
```

官方路径约定可参考 [Claude Code Skills](https://code.claude.com/docs/en/skills)、[Qoder CLI Skills](https://docs.qoder.com/en/cli/Skills) 与 [QoderWork Skills](https://docs.qoder.com/qoderwork/skills)。

### 其他 harness

```bash
./install.sh \
  --harness custom \
  --target "$HOME/.my-agent/skills/daily-paper-digest" \
  --agent-harness custom \
  --no-schedule
```

随后在配置的 `agent.custom_command` 中填写该 harness 的非交互命令；支持 `{prompt}`、`{prompt_file}`、`{workspace}`、`{config}` 和 `{date}` 占位符。

## 配置时间、类别与配比

默认配置位置：

- macOS / Linux：`~/.config/daily-paper-digest/config.json`
- Windows：`%LOCALAPPDATA%\daily-paper-digest\config.json`
- 环境变量覆盖：`DAILY_PAPER_DIGEST_CONFIG`

显式修改时间、时区和精确配额：

```bash
python ~/.agents/skills/daily-paper-digest/scripts/configure.py set \
  --time 08:30 \
  --timezone Asia/Shanghai \
  --total 15 \
  --quota generation=3 \
  --quota understanding=4 \
  --quota agentic_rl=3 \
  --quota embodied_vla_wam=3 \
  --quota others=2
```

显式修改某类检索范围：

```bash
python ~/.agents/skills/daily-paper-digest/scripts/configure.py set \
  --terms 'agentic_rl=agentic reinforcement learning|RLVR|tool-use agent|computer-use agent' \
  --terms 'embodied_vla_wam=embodied AI|vision-language-action|world action model|robot manipulation'
```

`digest.categories` 是普通 JSON 数组，可以增加全新类别，也可改为 `weight` 比例。完整字段见 [configuration.md](skills/daily-paper-digest/references/configuration.md)。每次修改后运行：

```bash
python ~/.agents/skills/daily-paper-digest/scripts/configure.py validate
python scripts/schedule.py install
```

Windows 可重新运行 `install.ps1`，它会更新配置并重置计划任务。

已有 Hermes 或其他旧系统的推送历史时，把文件路径加入 `selection.additional_history_files`（例如 `~/.hermes/paper_radar_history.json`）。这些文件只读参与去重，不会被本项目覆盖。

## Zotero 初始化

先完全退出 Zotero，再创建顶层集合和按配置生成的子集合。命令会先备份 `zotero.sqlite`，且检测到 Zotero 正在运行时会拒绝写库：

```bash
python ~/.agents/skills/daily-paper-digest/scripts/zotero_bridge.py setup-collections
```

之后正常启动 Zotero。每日运行会通过本机 Connector 导入 PDF、放入对应子集合，并把 PDF 深链写回单篇笔记和总览。

如果不需要 Zotero，把配置中的 `zotero.enabled` 和 `zotero.required` 都设为 `false`。

## 手动运行与补推

强制生成今天：

```bash
python ~/.agents/skills/daily-paper-digest/scripts/run_daily.py --force
```

补推指定日期：

```bash
python ~/.agents/skills/daily-paper-digest/scripts/run_daily.py --force --date 2026-08-06
```

只搜索、下载原文与原图，不调用模型：

```bash
python ~/.agents/skills/daily-paper-digest/scripts/run_daily.py --force --prepare-only
```

已有同日素材且配置未变时会复用；配置变更后确需重做同一天时加 `--refresh`，旧目录会被重命名为带时间戳的备份。

## 输出结构

```text
paper-notes/YYYY/MM/DD/
├── digest.md
├── digest.json
├── JOB.md
├── paper-a.md
├── paper-a.json
├── images/paper-a/figure-01.png
├── images/paper-a/figure-02.png
└── sources/paper-a/
    ├── abstract.html
    ├── paper.html
    ├── full-text.txt
    └── paper.pdf
```

持久去重索引位于 `<archive>/pushed-paper-index.json`。运行状态和日志位于 `<archive>/.daily-paper-digest/`。

## 运行链路

```text
配置校验 → arXiv API/RSS 搜索 → 历史去重 → 按配额选择
→ 下载 HTML/PDF 与逐论文原图 → harness 撰写详细笔记
→ 结构/阶段/图片/重复校验 → Zotero 导入与分类
→ 二次校验 → 原子写入历史索引
```

模型进程只负责需要理解论文的笔记撰写；搜索、下载、路径约束、去重、图片归属、Zotero 深链和最终发布均由确定性脚本控制。

## 测试

```bash
python scripts/validate_repository.py
python -m unittest discover -s tests -v
python /path/to/skill-creator/scripts/quick_validate.py skills/daily-paper-digest
```

CI 在 Windows、macOS、Linux 以及 Python 3.11/3.13 上运行离线测试。

## 安全与隐私

- 仓库不包含 Zotero 数据库、论文历史、用户绝对路径、API Key 或登录凭据。
- Agent CLI 使用各自已有的本机登录；本项目不会读取或保存 token。
- Zotero 集合初始化只在 Zotero 关闭时执行，并创建可恢复备份。
- 定时任务以当前用户权限运行，不使用管理员权限或危险的全局 sandbox 绕过。

## License

MIT
