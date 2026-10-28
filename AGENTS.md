# 给 Codex：部署“文献雷达”

你的任务**只有部署**：把本文件夹发布成一个 GitHub 仓库，让 GitHub Actions 每天自动运行、推送微信、发布网页。程序已经写好并测试过，不需要重新设计，也不需要重新调研。

## 边界（必须遵守）

- **不要改 `radar/` 里的代码，也不要改 `config.toml` 里的筛选规则**。唯一要改的是 `config.toml` 里的 `site_url`。如果真有 bug 挡住部署，只做最小修复，跑通 `python tests/test_rules.py` 和 `python tests/eval_sets.py`，然后向用户说明改了什么、为什么改
- **不开通任何付费服务**；不接 TikHub、玻尔、SocialDataX；不加任何微信抓取
- **不在这台电脑上创建计划任务、后台进程或开机自启**（用户很反感黑色命令行窗口反复弹出）。所有定时运行都交给 GitHub Actions
- 不动 `D:\Research\FrontierRadar`（旧项目）
- 推送 token 只能存进 GitHub Secrets，**不能写进任何文件、commit 或日志**
- 需要用户本人操作的只有两件事：登录 GitHub、用微信扫码拿 Server酱 SendKey。其余步骤不要反复来问

## 步骤

1. **准备 workflow 文件**：把 `部署文件\radar.yml` 复制到 `.github\workflows\radar.yml`（等同于双击 `准备上传（双击一次）.cmd`）
2. **GitHub 登录**：检查 `gh --version`。没有就 `winget install --id GitHub.cli -e`。然后 `gh auth status`，没登录就运行 `gh auth login --web`，让用户在浏览器里完成授权。如果网络不通，用户电脑上有 Clash 代理 `127.0.0.1:7897`，可以给 git/gh 设 `HTTPS_PROXY`
3. **建仓库并推送**（公开仓库；免费版 GitHub Pages 需要公开仓库）：
   ```powershell
   git init -b main
   git add -A
   git commit -m "文献雷达：初始版本（含旧站导入数据）"
   gh repo create paper-radar --public --source . --push
   ```
   提交前确认 `.github/workflows/radar.yml` 在里面（`git ls-files .github`）
4. **微信推送 token**：让用户打开 https://sct.ftqq.com ，用微信扫码登录 → 复制 SendKey 发给你。然后：
   ```powershell
   gh secret set SERVERCHAN_SENDKEY --body "<用户给的SendKey>"
   ```
   （不要用 PushPlus：它要求交费实名认证）
5. **打开 GitHub Pages**（main 分支 /docs 目录）：
   ```powershell
   gh api -X POST repos/{owner}/paper-radar/pages -f "source[branch]=main" -f "source[path]=/docs"
   gh api repos/{owner}/paper-radar/pages --jq .html_url
   ```
   把得到的网址填进 `config.toml` 的 `site_url = "..."`，commit 并 push
6. **允许 workflow 写仓库**（保险起见）：
   ```powershell
   gh api -X PUT repos/{owner}/paper-radar/actions/permissions/workflow -f default_workflow_permissions=write
   ```
7. **第一次运行并盯到结束**：
   ```powershell
   gh workflow run radar.yml
   gh run list --workflow radar.yml --limit 1
   gh run watch <run-id>
   ```
   这次运行会依次做：查最近 30 天新论文并推送“文献雷达已启动”→ 导入旧云端站（只做一次）→ 历史回填 2024-01 以来的论文（这一步最多跑 35 分钟，整次运行可能要 40 分钟左右，属于正常）
8. **回填没做完就再跑一次**：看这次运行的 Summary 里“剩余月份”。不是 0 就再 `gh workflow run radar.yml`，直到变成 0；那时微信会收到“历史回填完成”

## 验收清单（全部满足才算完成，逐条向用户报告）

- [ ] 仓库网址；`.github/workflows/radar.yml` 已在仓库里
- [ ] `SERVERCHAN_SENDKEY` 已设置（`gh secret list` 能看到）
- [ ] 网页能打开，页头写着“共 xxx 篇”（导入后至少 709 篇），“按月浏览”能点开月份
- [ ] `site_url` 已填并 push
- [ ] 第一次运行所有步骤都是绿色；把 Summary 里的来源状态表原样贴给用户（Crossref 必须“正常”，个别期刊 RSS 失败可以接受，列出来就行）
- [ ] **用户亲口确认**微信收到了“文献雷达已启动”（只看到接口返回 ok 不算）
- [ ] 回填“剩余月份”为 0，或者说明还剩几个月、下一次定时运行会接着做

## 常见问题

| 现象 | 处理 |
|---|---|
| Actions 里 `git push` 报 403 | 执行第 6 步，然后重跑 |
| 日志里 `serverchan:fail` | 把返回内容告诉用户（常见原因：SendKey 复制错了、当天免费额度用完）。修好后重跑 |
| 日志显示 `dry-run push` | 说明 Secret 没读到：检查名字是否正好是 `SERVERCHAN_SENDKEY` |
| Crossref 全部失败 | 一般是临时故障，隔 10 分钟重跑；连续失败再把错误原文告诉用户 |
| Pages 打开 404 | 等 1–3 分钟；确认 Settings → Pages 的来源是 main /docs |
| 回填超时没做完 | 正常，再跑一次会接着做（进度存在 `data/state.json`） |

做完后用几句话告诉用户结果：网址、微信是否收到、回填进度。不要写长篇状态报告。
