# 文献雷达：合成氨 / 焦耳热 / ALD

追踪这三个方向的新论文和公众号科研报道：

- **每天自动查新两次**（北京时间 07:17、19:17），有新论文就**推送到微信**：每篇带中文标题和 2–3 句中文简介（需要一个免费的智谱 API Key，见下文；没配时显示英文摘要开头）
- **网页资料库**：从 2024 年 1 月起，**按年、按月归档**，可以搜索、筛选、收藏、标记已读，导出到 Zotero
- **RSS**：可以加进 Zotero 的“订阅”，在 Zotero 里直接看新论文
- **只盯文献源头，不再抓公众号**：公众号报道的论文都来自期刊和预印本，直接盯源头更早、更稳。旧站的 124 篇公众号科研报道留在网站里

全部免费。程序跑在 GitHub 的服务器上，不用开电脑，不用登录微信，也没有会过期的 cookie。

> **已部署**（2026-10-28）：仓库 https://github.com/guizehua88-max/paper-radar ，网页 https://guizehua88-max.github.io/paper-radar/ 。
> 还差一步：在 GitHub 仓库 Settings → Secrets and variables → Actions 新建 `SERVERCHAN_SENDKEY`（值是 Server酱 的 SendKey），填好后微信推送就开始工作。

> **现在就能看**：双击 `打开文献雷达网页.cmd`（或直接打开 `docs\index.html`），能看到已经从旧网站导入的 709 条记录。不联网也能看。

---

## 目录里有什么

| 路径 | 内容 |
|---|---|
| `打开文献雷达网页.cmd` | 双击用浏览器打开本地网页 |
| `config.toml` | **所有可调的东西都在这里**：方向、关键词、排除词、关注作者、推送门槛、期刊 RSS |
| `radar/` | 程序本体（只用 Python 标准库，不需要安装依赖） |
| `data/papers/` | 资料库，一个发表月份一个文件（`2026-07.json` 等） |
| `docs/` | 网页（GitHub Pages 直接发布这个文件夹） |
| `legacy/` | 从旧网站导出的原始数据、旧站 40 条相关性判例（以后改了规则可以重新导入） |
| `部署文件/radar.yml` | 定时任务（部署前双击 `准备上传（双击一次）.cmd` 复制到 `.github/workflows/`） |
| `tests/` | 规则测试，`python tests/test_rules.py` / `python tests/eval_sets.py` |

---

## 和旧网站（FrontierRadar）的关系：吸收了什么

旧站有两版：本地 Python 版（`D:\Research\FrontierRadar`），以及 Cloudflare 云端版（paperresearch.de5.net）。这次对着它的源码、数据库和测试用例逐项看过，保留了有用的部分，去掉了跑不通或要花钱的部分。

**已经并进来的数据**
- 旧本地库 **6604 篇**（发表日期都在 2026 年 7–9 月，当时只抓了 90 天）。用新规则重新筛选后，留下 **585 篇**相关论文。旧规则自己就把其中 83% 判成了“低相关/排除”；旧站的收藏、已读、笔记都是 0 条，所以没有可迁移的个人记录
- 旧站公众号库 **852 篇推文**，过滤掉行情、教学、会议、获奖类之后，留下 **124 篇科研报道**（叶天南、陈萍、江莉龙等团队的成果报道都在里面）。推文里提到的 DOI 和论文自动关联：论文卡片上会显示“公众号报道 ×N”
- 旧云端站（10 月以后的数据）：首次部署后会**自动导入一次**（我这边连不上外网，只能交给 GitHub 去拉）

**借鉴的设计**
- **六档相关度**（旧站最有价值的东西）：⭐交叉 / 直接 / 可迁移 / 待核 / 背景 / 排除。旧站的分类器原样保留在 `radar/legacy_rules.py`，和新的关键词规则一起判断：旧规则判“直接/交叉”最准，新规则挡噪声最严，两者合并。用旧站 40 条人工判例检验，39 条一致
- 每篇给出**中文规则说明**（研究对象、供热方式、用途），比如“甲烷干重整；涉及原子层沉积”“材料制备/结构调控｜焦耳热用于制备”
- **日期不造假**：出版社只给到月份的，就显示月份，不编具体日期
- 收藏、已读；导出 **CSV**（带 BOM、防公式注入，这两点照搬旧站）和 **RIS**（导入 Zotero）
- 来源状态表：每个来源这次是成功还是失败，页面底部写清楚

**有意没有带过来的**
- TikHub / SocialDataX 等付费社媒采集、玻尔按次计费检索：太贵
- 微信读书和电脑微信采集：登录态会失效、会被风控，还得开着电脑
- 本地服务进程、租约调度、模型解读队列等复杂机制：维护成本太高，换成了“GitHub 定时跑一次脚本”的简单结构

---

## 部署（约 10 分钟，只需做一次）

### 1. 建一个 GitHub 仓库
0. 先在本文件夹里**双击 `准备上传（双击一次）.cmd`**。它会把定时任务文件 `部署文件\radar.yml` 复制到 `.github\workflows\radar.yml`（GitHub 只认这个位置；我这边的远程工具不允许直接写 `.github` 文件夹，所以要你双击这一下）
1. 登录 [github.com](https://github.com)，点右上角 **+ → New repository**，名字比如 `paper-radar`，选 **Public**（免费版 GitHub Pages 只支持公开仓库；里面只有公开的论文信息，推送用的 token 会单独加密保存）
2. 点 **uploading an existing file**，把本文件夹里的**全部内容**（包括 `.github` 文件夹）拖进去，点 **Commit changes**。文件较多时可以分两次拖

   > 手动方式：如果 `.github` 文件夹没传上去，在仓库页面点 **Add file → Create new file**，文件名填 `.github/workflows/radar.yml`，把 `部署文件\radar.yml` 的内容整个粘贴进去，点 Commit

### 2. 拿到微信推送 token
- **Server酱（推荐）**：打开 [sct.ftqq.com](https://sct.ftqq.com)，微信扫码登录 → 复制 SendKey。免费版每天 5 条，本项目一天最多 2 条，够用
- 备选：WxPusher 极简推送（免费，扫码得到 `SPT_` 开头的 token；现在主要推送到 WxPusher 自己的 App，微信里接收要另外开通渠道）
- 不推荐 PushPlus：2024-08 起必须交费实名认证（要填身份证号）才能发送

### 3. 把 token 填进仓库
仓库页面 → **Settings → Secrets and variables → Actions → New repository secret**
Name 填 `SERVERCHAN_SENDKEY`（用 WxPusher 就填 `WXPUSHER_SPT`），Secret 粘贴 token → Add secret

### 4. 打开网页
**Settings → Pages** → Source 选 **Deploy from a branch** → Branch 选 `main`、文件夹选 `/docs` → Save。
几分钟后这一页会显示网址，形如 `https://你的用户名.github.io/paper-radar/`。把它填进 `config.toml` 的 `site_url = ""`，推送里就会带“查看全部”链接。

### 5. 第一次运行
**Actions** → 如果提示启用就点启用 → 左侧 **文献雷达** → **Run workflow**。这次运行会做三件事：
1. 查最近 30 天的新论文，微信收到“文献雷达已启动”
2. 导入旧云端站的数据（只做一次）
3. **历史回填**：按月补齐 2024-01 以来的论文。每次最多跑 35 分钟，没做完的话下次定时运行会接着做，一般一两次就能补完，补完后微信收到“历史回填完成”

之后每天自动跑两次，你不用再管。

> 收不到推送？在 Actions 里点开那次运行，看日志最后几行的推送结果；运行页面下方的 Summary 里有每个来源的状态。

---

## 网页怎么用

- **最新动态**：最近 3 周新收录的论文，按收录日期分组
- **按月浏览**：按年份列出每个月，点月份就看那个月发表的论文（按钮上是条数，⭐ 是交叉方向的篇数）。底下“在全部月份中搜索”可以跨月搜索
- **我的收藏**：点卡片上的 ☆ 收藏。收藏和已读记在这台设备的浏览器里，换设备不会同步
- 筛选：相关度（默认“重点” = 交叉 + 直接 + 可迁移）、类型（论文 / 公众号推文）、方向、顶刊、关注作者、预印本、未读、公众号报道过
- **导出**：当前列表导出为 RIS（Zotero 里：文件 → 导入）或 CSV（Excel）

## 中文简介（免费，需要做一次设置）

推送和网页卡片上的“简介”由大模型根据**标题 + 摘要**写成：中文标题，加 2–3 句话说明研究对象、方法和主要结果。没有摘要的论文只写一句主题概括，并标注“仅据标题”，不会编结果。

用的是智谱开放平台的免费模型 **GLM-4.7-Flash**（不行时自动换 GLM-4-Flash-250414），不花钱：
1. 打开 [bigmodel.cn](https://bigmodel.cn)，手机号注册登录（如果要求实名认证就按提示做，不收费）
2. 控制台 → **API Key** → 新建，复制
3. GitHub 仓库 → **Settings → Secrets and variables → Actions → New repository secret**，Name 填 `ZHIPU_API_KEY`，粘贴 → Add secret

之后每次运行：先给本次新论文写简介再推送；然后用剩余时间给资料库里已有的论文补写（重点论文优先，每次约 20 分钟，几天内补完）。网页底部会显示“重点论文已写 X/Y 篇”。

没配置时：推送里显示英文摘要的前一两句（“摘要：…”），网页卡片上显示“摘要节选”。

> 也可以换成任意 OpenAI 兼容接口：设置 `LLM_API_KEY` + `LLM_BASE_URL` + `LLM_MODEL`（会优先使用）。

## 推送规则

只推送**新出现**、分数达到 `min_push_score`（默认 18）的论文：
- 交叉 40 分、直接 25 分、可迁移 15 分、待核 9 分、背景 6 分
- 关键词出现在标题里 +3，顶刊 +4，关注作者 +8

所以默认会推送交叉、直接，以及标题里就有焦耳热/ALD 的可迁移论文；电催化/光催化/锂介导制氨、氨分解、只讲工厂流程/能源系统/经济性的论文归“背景”，只放网页。一天最多两条消息；**推过的不会再推**；连续 7 天没有新论文发一条“运行正常”的心跳；主数据源出故障时发提醒。导入和回填的论文不会逐篇推送。

## 覆盖面：会不会漏？

**来源**
- **Crossref**：几乎所有正规期刊（ACS、Elsevier、Wiley、RSC、Springer Nature、Science、中文期刊里有 DOI 的）和 ChemRxiv 等预印本，论文上线后一般当天就能查到。每次用 20 多组检索词查最近 5 天
- **arXiv**、**期刊 RSS**（Nature 系列 12 本、Science / Science Advances、Joule、Chem、Angew、AM、AEM、AFM）：比 Crossref 早几个小时，且带摘要
- **补摘要**：Crossref 常缺摘要（Elsevier、ACS 尤其多），会再去 Semantic Scholar、Europe PMC 查；有 `OPENALEX_API_KEY` 时也查 OpenAlex。摘要越全，筛选越准、简介越好

**用旧公众号库检验过（2026-10-29）**：旧站公众号推文里写明 DOI 的论文，2024 年以后的共 19 篇：
- **14 篇资料库里有**。其中几篇乍看对不上，是因为同一篇 Angew 有德文版/国际版两个 DOI（已合并）、先收了预印本后来正式发表（现在会自动换成正式版）、推文里的 DOI 写错或写残
- **3 篇是规则有意不收的**：半导体/电子器件用的 ALD 薄膜 2 篇，焦耳热做吸波材料 1 篇。后一类现在改为：**标题明确写了闪蒸焦耳热/碳热冲击的，也收进网页，归“背景”**
- **2 篇真漏了**：一篇 Nature Chemical Engineering 的闪蒸焦耳热论文（标题只写 “Flash-encapsulated fluorination”，出版社也没交摘要，关键词规则看不到）；一篇 Nature Reviews Chemistry 的锂介导电化学制氨综述（检索词偏热催化）。现在加了 Nature 系列 RSS（带摘要）、补摘要、锂介导检索词来补这两类

**仍然可能漏的**：只有中文、没有 DOI 的期刊论文；标题和摘要都不出现这些关键词的论文（比如只在正文里用了焦耳热）；会议摘要和专利。想多盯某个方向，在 `config.toml` 里加检索词或关键词即可。

## 调整方向

都在 `config.toml` 里，在 GitHub 网页上点文件 → 铅笔图标就能改：
- 想多查某类论文 → 在对应方向的 `crossref_queries` 里加一句英文检索词
- 有漏掉的写法 → 在 `include` 里加关键词（中英文都行）
- 某类噪声老出现 → 在 `exclude` 里加关键词
- 推送太多或太少 → 调 `min_push_score`
- 关注的课题组 → `[watch] authors`

## 可选增强（Settings → Secrets → Actions）

| Secret 名 | 作用 |
|---|---|
| `CONTACT_EMAIL` | 你的邮箱。Crossref 对带邮箱的请求服务更稳定（只发给 Crossref） |
| `OPENALEX_API_KEY` | 在 [openalex.org/settings/api](https://openalex.org/settings/api) 免费申请。多一个带摘要的来源，筛选更准，用量远低于免费额度 |
| `ZHIPU_API_KEY` | **推荐**。智谱免费模型写中文标题和简介（见上文“中文简介”） |
| `LLM_API_KEY` + `LLM_BASE_URL` + `LLM_MODEL` | 换成其他 OpenAI 兼容接口写简介（比如 DeepSeek：`https://api.deepseek.com/v1` + `deepseek-chat`，按量付费），同时开启 AI 相关度复核 |
| `WECOM_WEBHOOK` | 企业微信群机器人，作为另一种推送方式 |

**Zotero 订阅**：Zotero → 文件 → 新建订阅 → 从网址 → `https://你的用户名.github.io/paper-radar/feed.xml`

## 常用命令（可选，本机需要 Python 3.11+）

```
python -m radar                                   日常运行一次
python -m radar --backfill 2024-01                按月回填（可中断，再运行会接着做）
python -m radar --import-export legacy/legacy-export.json.gz   重新导入旧站数据（改了规则之后）
python -m radar --import-cloud https://paperresearch.de5.net   导入旧云端站
python -m radar --render-only                     只重新生成网页
python -m radar --maintain                        补摘要、写中文简介、规则改过时重筛资料库
python tests/test_pipeline.py                     离线跑一遍完整流程（不联网、不推送）
python tests/test_rules.py && python tests/eval_sets.py         规则自检
```

## 常见问题

**简介准不准？** 简介只根据标题和摘要写，提示词要求不准编数字和结论；没有摘要时只概括主题并标明。但它是 AI 写的，可能有误，引用前请看原文。

**为什么不直接监控公众号？** 微信没有开放接口，免费办法都得靠一个登录着的账号去抓，登录态会过期，抓得多了还会被风控。旧项目在 TikHub、微信读书、电脑微信三条路上都卡在这里。WeWe RSS 走的就是微信读书这条路，2026 年 5 月已经归档停更。公众号报道的论文基本都来自期刊，直接盯期刊和预印本通常更早、更全。以后如果拿到某个公众号的 RSS 地址，按 `config.toml` 里 `[[rss]]` 的格式加进来，就会走同样的筛选。

**会不会哪天自己停了？** GitHub 会暂停 60 天没有任何活动的公开仓库里的定时任务。本项目每次运行都会提交数据，正常情况下不会触发。万一收到 GitHub 邮件说 workflow 被禁用，到 Actions 页点 Enable 就行。另外连续 7 天没有推送时会发一条心跳消息，收不到心跳就说明它停了。

**仓库会不会越来越大？** 数据按月份分文件存，每次只改动有变化的那几个月份，git 历史增长很慢。
