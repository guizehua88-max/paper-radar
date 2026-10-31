# 免费摘要接口实测（2026-10-31）

范围：仅公开论文元数据，只读；未写入论文库，未调用模型或通知，未使用 API Key、付费服务或出版社抓取。执行脚本：`tests/probe_abstracts_live.py`（每次显式指定 1–5 个 DOI，100 秒上限）。以下为实际回执的简要记录。

## 原本缺摘要的论文

| DOI | 期刊与标题 | Semantic Scholar | Europe PMC | Crossref |
| --- | --- | --- | --- | --- |
| 10.1021/acs.iecr.3c04299 | Industrial & Engineering Chemistry Research — Strengthening Hydrogen Spillover in Ceria-Supported Co–Fe Bimetallic Catalysts for Boosting Ammonia Synthesis | 正常响应，无摘要 | 正常响应，无摘要 | 正常响应，无摘要 |
| 10.1021/acscatal.4c00830 | ACS Catalysis — CN 2 2– Vacancies Enhance Ammonia Synthesis over Air-Durable Alkaline Earth Metal Cyanamide-Supported Cobalt Catalysts | 正常响应，无摘要 | 正常响应，无摘要 | 正常响应，无摘要 |
| 10.1016/j.ijhydene.2026.158426 | International Journal of Hydrogen Energy — Calcination atmosphere dictates the chemical state of ruthenium on MgO: From strongly bonded interfacial RuO2 species to active metallic Ru clusters for ammonia decomposition to hydrogen | 正常响应，无摘要 | 正常响应，无摘要 | 正常响应，无摘要 |

此组覆盖 ACS 和 Elsevier。每篇 `attempted` / `checked` 均包含这三个来源，`failed=[]`，`complete=true`，`found={}`。结果是补到 **0 / 3 篇**，不能声称已解决所有缺摘要。

## 阳性对照

DOI `10.1002/adfm.202400773`（Recent Progress on Computation-Guided Catalyst Design for Highly Efficient Nitrogen Reduction Reaction）原有摘要，用作验证接口和解析链路，不计作新补齐。

Semantic Scholar 正常返回与该 DOI 精确对应的摘要，清理后 **1243 字符**；`found_by="Semantic Scholar"`、`complete=true`、`failed=[]`。因已得到摘要，不再调用后续接口。

## 修复验证

`tests/test_abstract_regressions.py` 的 10 项测试通过。覆盖真实尝试记录、失败来源停止当前批次并转用独立来源、每批 deadline、按剩余来源分摊时间、精确 DOI 绑定、Crossref 查询上限和按查询日期公平轮换、未请求记录不算失败、错误日志不泄露 API Key。

限制：此实测不代表全库覆盖率。没有摘要时继续只展示可信中文标题；后续定时补查只对有真实查询记录的条目退避，不把跳过条目错误标记为已经查过。
