# Eidolon Yoibanashi · 夜间搭话

作者：[S-Elirch](https://github.com/S-Elirch) · [仓库](https://github.com/S-Elirch/Eidolon_Yoibanashi)

基于 [Sallyn0225/eidolon-sc](https://github.com/Sallyn0225/eidolon-sc) **v0.2.0** 的个人 Fork，围绕夜间交流增加访问触发的主动搭话功能。

[体验展示](#体验展示) · [如何使用](#如何使用) · [兼容性](#兼容性) · [功能与开发说明](FORK.md) · [原项目说明](#原项目-eidolon-sc-的说明)

深夜，你想找她说说话。打开聊天后，消息还没发出去，她先与你打了声招呼，或接着之前的话题问上一句。

这个功能让进入聊天也成为一次交流的契机。角色是否开口、提起什么，以及何时出现消息，由关系、近期交流、可用话题与等待策略共同决定。希望在你正在输入、犹豫或翻看聊天记录时，她的消息恰好出现。

本 Fork 的新增功能采用 Vibe Coding 方式，与 Codex 协作开发；设计取舍、实现与验证范围见 [FORK.md](FORK.md)。

## 体验展示

<table>
  <tr>
    <td width="33%" valign="top">
      <img src="docs/images/visit-rinze.gif" width="100%" alt="凛世在用户发送消息前主动开口">
      <p align="center"><sub><b>她先开口</b><br>消息还没发出去，她先问起了之前的事。</sub></p>
    </td>
    <td width="33%" valign="top">
      <img src="docs/images/visit-user-first.gif" width="100%" alt="用户先发送消息，恋鐘随后正常回复">
      <p align="center"><sub><b>你先说了</b><br>写完发出，她接着你的话回应。</sub></p>
    </td>
    <td width="33%" valign="top">
      <img src="docs/images/visit-character-first.gif" width="100%" alt="用户仍在输入时恋鐘主动开口，草稿保留在输入框">
      <p align="center"><sub><b>还在输入，她的消息来了</b><br>草稿留在输入框里，先看看她说了什么。</sub></p>
    </td>
  </tr>
</table>

以上为真实 LLM 回复的浏览器手机尺寸录制，使用测试资料与模拟时钟。

## 如何使用

首次安装请下载本 Fork 的 `my-features` 功能源码，使用 [docker-compose.yml](docker-compose.yml) 构建。复制 `.env.example` 为 `.env`，至少填写 `CHAT_LLM_BASE_URL`、`CHAT_LLM_API_KEY` 和 `CHAT_LLM_MODEL`；模型需支持流式输出和工具调用。然后在源码根目录运行：

```sh
docker compose up -d --build
```

打开 <http://localhost:3000>。仅在本机测试时，可在 `.env` 设置 `EIDOLON_DEV_LOGIN=1` 使用开发登录；公网部署请按[部署说明](docs/deploy.md)配置正式登录。记忆检索、好感度和语音服务按[配置指南](docs/configuration.md)另行设置。上游预构建镜像不含本功能，不能用上游的 `pull` 命令代替源码构建。

在 **设置 → 搭话 → 夜间相遇** 中选择模式。默认关闭，按个人资料时区于 **22:00–次日 05:00** 生效，与原有后台搭话分别设置。

首次体验时，先与角色完成至少一轮普通对话，再选择「今晚想听你先说」并重新进入该角色的当前聊天，连续停留且不要先发送消息。启用好感度后需要达到信任或更高关系；未启用时不按关系档位抽选。5–8 秒是开始准备前的停留时间，模型生成和显示正文还需要额外时间。

- **自然偶遇**：根据关系抽取机会，稍作停留后，角色可能先开口；没有合适的话题时也可以保持安静。
- **今晚想听你先说**：希望体验她先开口时，可以为这一晚选择此模式。采用 5–8 秒停留等待，放宽机会抽取和部分频率限制；仍受关系、未回应及忙碌等条件约束，并非每次进入都会收到消息。下一晚恢复自然偶遇。

用户先发送消息时，尚未展示的搭话会让出交流机会。搭话实际请求模型后，刷新或离开页面不会仅因页面断开而取消生成与保存。具体行为、调用成本与体验取舍，以及供其他 Fork 接入的接口，见 [FORK.md](FORK.md) 和[模块说明](backend/src/eidolon/visit_outreach/README.md)。



## 兼容性

- **开发基线**：Eidolon-SC **v0.2.0**，普通回复与原有后台搭话继续使用上游流程。
- **Hibi 适配验证**：已在 [Eidolon-Hibi](https://github.com/x499806631-commits/Eidolon-Hibi) 的 `my-features` 分支提交 `1fb2454` 上，通过单独适配与前端合并，验证真实 LLM 访问触发、流式显示、普通回复衔接、消息保存与刷新恢复。
- **接入范围**：Hibi 验证使用通用历史、记忆与基础亲密度上下文，尚未接入其专属日程上下文及状态避让。接入 Hibi 或其他 Fork 仍需宿主接线与前端合并；这不是可直接安装的通用插件。

## 存在的问题：升级与回退

从已有 v0.2.0 部署升级时，请先备份，推荐沿用原 Compose 项目名，并确认使用原数据卷。也可以更换项目名，但必须显式映射原数据卷。改目录名后直接启动可能新建一个空卷，看起来像记录丢失。不要用 `docker compose down -v` 更新已有部署。

回退到纯上游 **v0.2.0** 时，不能只替换镜像：旧版不会清理本功能新增的表，启用过功能后删除账户可能报外键错误。可选两种处理方式：

- **恢复升级前备份**：程序与数据库一起恢复到升级前。旧备份不包含升级后新增的聊天、记忆和设置；请另行保留当前完整备份。
- **保留近期记录，卸载功能数据**：停止后端，完整备份当前数据库，在副本上清理下表中的三张功能表；检查数据库完整性与原有数据是否保持不变，再验证 v0.2.0 能正常读取和删除账户，最后切换到清理后的数据库及旧版程序。

| 功能表 | 清理的内容 |
|---|---|
| `visit_outreach_on` | 夜间搭话开关与今晚模式等设置 |
| `dev2_night_tasks` | 生成任务状态及准备结果快照 |
| `dev2_night_opportunities` | 每晚机会抽选记录 |

第二种方式不应删除原有账户、对话、记忆、亲密度或 `reach_outs` 表；正式保存的搭话使用原有 `reach_outs` 表，也应保留。清理期间必须停止后端，避免写入或重建功能表。SQLite 备份应使用数据库备份接口，或在完全停止后端后制作完整备份；运行中只复制 `.db` 文件可能漏掉 WAL 中的数据。

**验证范围**：已验证正向升级保留原有资料、对话与记忆，以及直接换回旧版可能发生的外键错误；尚未提供或验证上述三表卸载迁移工具。这是回退处理方案，不是已验收的一键回退功能，也不适用于任意其他版本或 Fork 的数据库。

---

## 原项目 eidolon-sc 的说明

以下保留原作者 [Sallyn0225](https://github.com/Sallyn0225) 的 eidolon-sc 介绍及通用部署、配置文档。上方介绍的是本 Fork 新增的访问搭话功能；下方内容介绍原项目。

<div align="center">

<img src="docs/images/landing.webp" alt="Eidolon-SC：和 283 事务所的偶像说说话" width="100%">

**和《偶像大师 闪耀色彩》的偶像实时对话的 AI 分身**

语音 · 文字 · 剧情回忆 · 跨角色共享记忆

**简体中文** · [English](README.en.md) · [日本語](README.ja.md)

[![CI](https://img.shields.io/github/actions/workflow/status/Sallyn0225/eidolon-sc/ci.yml?branch=main&style=flat-square&logo=githubactions&logoColor=white&label=CI)](https://github.com/Sallyn0225/eidolon-sc/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](backend/pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Pipecat](https://img.shields.io/badge/Pipecat-WebRTC-6E56CF?style=flat-square)](https://github.com/pipecat-ai/pipecat)
[![Next.js](https://img.shields.io/badge/Next.js-16-000000?style=flat-square&logo=nextdotjs&logoColor=white)](frontend/package.json)
[![React](https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=black)](frontend/package.json)
[![Docker Compose](https://img.shields.io/badge/Docker_Compose-2496ED?style=flat-square&logo=docker&logoColor=white)](docker-compose.yml)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/License-AGPL--3.0--or--later-A42E2B?style=flat-square&logo=gnu&logoColor=white)](LICENSE)

[预览](#-预览) · [特性](#-特性) · [快速开始](#-快速开始) · [配置指南](docs/configuration.md) · [部署](docs/deploy.md) · [架构决策](docs/adr/)

</div>

---

Eidolon-SC 让你以 **283 事务所制作人**的身份，和原作中的偶像打字聊天或者直接打电话。她用日语原声回答，附上中文字幕；能回忆自己在原作里经历过的剧情；还会记住你说过的事，并在偶像之间「传开」。

原作八个组合的 **28 位偶像**和事务员七草はづき都已经配好，可以直接对话。部署到公网后，任何人都能用 Google 或 GitHub 登录使用。

> [!IMPORTANT]
> 本项目是非官方的粉丝作品，与万代南梦宫及《偶像大师》系列的版权方无关，角色由 AI 扮演。剧情数据**不随仓库分发**，需要自行准备（[ADR-0003](docs/adr/0003-corpus-and-voices-stay-private.md)）；角色会通过回复间接地把剧情内容提供给登录的用户，这一风险见 [ADR-0006](docs/adr/0006-open-to-the-public-with-oauth.md)。

## 📸 预览

<p align="center">
  <img src="docs/images/call-demo.webp" alt="和真乃通话：她听懂中文，用日语原声回答，立绘随情绪换表情" width="100%">
  <br>
  <sub>打电话给真乃：你说中文，她用日语原声回答，字幕同步，立绘按每段话的情绪换表情</sub>
</p>

<table>
  <tr>
    <td width="50%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/images/app-dark.webp">
        <img src="docs/images/app-light.webp" alt="对话页：恋鐘想起了对灯織说过的猫">
      </picture>
      <p align="center"><sub><b>跨角色共享记忆</b>：只对灯織说过养了猫，恋鐘却说「灯織から聞いたばい」</sub></p>
    </td>
    <td width="50%">
      <img src="docs/images/outfits.webp" alt="换衣装面板">
      <p align="center"><sub><b>换衣装</b>：原作的每张卡都能穿，私服和演出服可以切换</sub></p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="docs/images/photo.webp" alt="对话页：めぐる发来一张照片">
      <p align="center"><sub><b>她发照片</b>：开口要，她按自己的样子和当时的情境拍一张</sub></p>
    </td>
    <td width="50%">
      <img src="docs/images/gifts.webp" alt="玩法页：「礼物」「成就」和「日历」三张卡">
      <p align="center"><sub><b>玩法</b>：抽奖、送礼、成就、日历</sub></p>
    </td>
  </tr>
  <tr>
    <td colspan="2">
      <img src="docs/images/schedule.webp" alt="日历的周视图：左边是待排的工作，右边一周的日程，有做完的、进行中的、保存过的和草稿">
      <p align="center"><sub><b>日程</b>：每周一发布一批工作，排进她们的一周，保存以后她就知道</sub></p>
    </td>
  </tr>
</table>

<table>
  <tr>
    <td width="33%"><img src="docs/images/phone-list.webp" alt="手机上的对话列表"></td>
    <td width="33%"><img src="docs/images/phone-chat.webp" alt="手机上的对话"></td>
    <td width="33%"><img src="docs/images/phone-call.webp" alt="手机上的通话层"></td>
  </tr>
  <tr>
    <td align="center"><sub>对话列表：按最近往来排序，可以搜中文译名、假名、罗马字和组合名</sub></td>
    <td align="center"><sub>每句日语都附中文字幕</sub></td>
    <td align="center"><sub>手机上的通话层占满全屏</sub></td>
  </tr>
</table>

<details>
<summary><b>落地页</b>：一束白光散成 29 种印象色，每一道是一位角色</summary>

<br>

<img src="docs/images/landing-tour.webp" alt="落地页的滚动叙事" width="100%">

</details>

## ✨ 特性

**和她说话**

- 🎙️ **实时语音通话**：基于 WebRTC 的全双工通话，中文、日语都能说，随时可以插话打断她。
- 🇯🇵 **日语原声 + 中文字幕**：她用日语回答，保留口癖，每句附中文字幕（[ADR-0001](docs/adr/0001-japanese-replies-with-chinese-subtitles.md)）；专有名词按读音表念对。
- 🎭 **会动的立绘**：通话时叠上原作的 Spine 立绘，会呼吸、眨眼、动嘴，随情绪换表情。原作出了新卡，每周同步一次就能穿。
- 📷 **她发照片**：你开口要，她按自己的样子和当时的情境拍一张发来，只有全年龄的内容（[配置指南](docs/configuration.md#照片)）。
- 💗 **好感度和礼物**：你说的话会让她亲近或疏远你，冒犯多了她会挂断电话、结束这段对话。每天在玩法里抽奖得到礼物送给她，她会回应；解锁成就能多得抽奖次数。
- 📅 **日历**：角色和制作人的生日、日本的节假日，她当天也知道。
- 🗓️ **日程**：每周发布的工作，你排进她的一周，她知道并以此说话；做完的工作让她更亲近你（[配置指南](docs/configuration.md#日程)）。
- 💌 **搭话**：你一阵子没来，她会在对话里先说几句；越亲近来得越早（[配置指南](docs/configuration.md#搭话)）。

**她记得什么**

- 📖 **回忆原作剧情**：导入剧情库后，她只回忆**自己出场过**的正式经历。
- 🔄 **与原作同步**：一条命令同步粉丝汉化和日语原文，新卡、新活动一周内就能回忆；歌曲和演出也写进她的世界设定。
- 🧠 **跨角色共享记忆**：对灯織说过的事，恋鐘也会知道，并以「听灯織说……」的口吻提起（[ADR-0002](docs/adr/0002-shared-memory-with-source-character.md)）。她记住的事你都能查看和删除。
- 🗂️ **历史对话**：随时开新对话，旧的留着翻看，不要了可以删。
- 🪪 **制作人档案**：称呼、性别、时区和生日都能在设置里改；生日当天她会祝你生日快乐。
- 📊 **统计**：看你和她们聊了多少回合、通了多久电话、送了多少礼物，可以导出一张分享图。
- ⚡ **按需检索**：先判断这句话要不要查资料，寒暄闲聊没有额外延迟。

**部署和运营**

- 👥 **对公众开放**：Google 或 GitHub 登录，按用户限流，记忆互相隔离；用户可以彻底删除自己的数据，数据库每天加密备份到异地。
- 📦 **数据包**：对话、照片、记忆和制作人档案导出成一个 zip，在别的部署导入后接着用；好感度、礼物和日程不随行（[ADR-0025](docs/adr/0025-data-packages-leave-affinity-and-gifts-behind.md)）。
- 🔑 **自带密钥**：用户可以填自己的 LLM、语音合成、语音识别和生图密钥，用自己的额度，满足条件时不占限额（[配置指南](docs/configuration.md#自带密钥)）。密钥只存在浏览器里（[ADR-0017](docs/adr/0017-own-api-keys-stay-in-the-browser.md)）。
- 🛠️ **后台**：看用量、花费和进行中的通话，改模型和限额不用重启，封禁用户、发公告；看不到任何人的对话和记忆。
- 🚦 **服务状态**：用户在对话列表就能看到各项服务正不正常。
- 🔌 **服务可以替换**：模型支持 OpenAI Chat Completions、Responses 和 Anthropic Messages 三种接口；语音识别、向量化各有两家可选，判断模型有三家；每项服务都可以单独走网络代理。
- 🚀 **一键部署**：Docker Compose 生产配置，自带 Caddy（自动 HTTPS）和 coturn（TURN 中继）；CI 构建镜像并发布到 GHCR。

## 🚀 快速开始

需要 Docker 与 Docker Compose，以及一个支持流式输出和工具调用的对话模型。

```sh
git clone https://github.com/Sallyn0225/eidolon-sc.git && cd eidolon-sc
cp .env.example .env    # 填写 CHAT_LLM_BASE_URL、CHAT_LLM_API_KEY、CHAT_LLM_MODEL，再加一行 EIDOLON_DEV_LOGIN=1
docker compose up --build
```

打开 <http://localhost:3000> 是落地页。点右上角的「登录」，在登录面板里选「开发登录」，随便填一个名字，同意隐私政策和使用条款、声明年满 13 岁后就进入对话列表，点一位偶像开始对话。

只填对话模型时能文字聊天。按需再补上其他服务的密钥，逐步开启语音通话、剧情回忆、记忆和立绘：

| 功能 | 需要的配置 |
|---|---|
| 文字对话 | `CHAT_LLM_*` |
| 语音通话 | `FISH_AUDIO_API_KEY`，加语音识别的 key：`SONIOX_API_KEY`（默认）或阿里云百炼的 `DASHSCOPE_API_KEY` |
| 剧情库 | 向量化的 key：`SILICONFLOW_API_KEY`（默认）或 `GEMINI_API_KEY` |
| 检索与记忆 | 向量化的 key、`SILICONFLOW_API_KEY`（重排），加判断模型：`JEV_API_KEY`（默认）、Cloudflare 或阿里云百炼的凭证 |
| 照片 | 判断模型，加生图的 key：`NOVELAI_API_KEY` |
| 立绘与招呼 | `EIDOLON_STAND_ASSETS_URL` 和存放素材的存储桶 |

每项怎么配、配了以后有什么效果，见 **[配置指南](docs/configuration.md)**；所有环境变量的说明见 [`.env.example`](.env.example)。

## 🧩 架构

```mermaid
flowchart LR
    subgraph Browser["浏览器 · Next.js"]
        Landing["落地页"]
        UI["对话列表 · 对话 · 通话层"]
        Admin["后台"]
    end

    subgraph Backend["后端 · FastAPI + Pipecat"]
        API["HTTP API"]
        Call["通话管线"]
        Engine["对话引擎"]
        Retrieval["检索"]
        Memory["记忆"]
        DB[("SQLite<br/>对话 · 照片 · 记忆 · 剧情库")]
    end

    Bucket[("对象存储<br/>立绘 · 招呼")]

    UI -- "文字回合 /api/*" --> API
    Admin --> API
    UI <-- "WebRTC 语音 + 字幕" --> Call
    Landing & UI -. "立绘、招呼音频" .-> Bucket
    API --> Engine
    Call -- "语音识别<br/>Soniox 或阿里云百炼" --> Engine
    Engine -- "Fish Audio 语音合成<br/>（带读音表）" --> Call
    Engine --> Retrieval
    Engine --> Memory
    Retrieval --> DB
    Memory --> DB
    Engine <--> ChatLLM["对话模型"]
    Retrieval <--> Judge["判断模型<br/>Jev、Clef 或阿里云百炼"]
    Retrieval <--> Emb["向量化<br/>Qwen3-Embedding 或 Gemini"]
    Retrieval <--> SF["硅基流动<br/>Qwen3-Reranker"]
    Memory <--> BgLLM["后台模型"]
    Engine <--> Image["生图<br/>NovelAI"]
```

- 打字和说话走**同一个对话引擎**，写进同一条对话记录；通话只是在引擎外面包了一层语音识别和语音合成。
- 对话模型以 `<ja>…</ja><zh>…</zh>` 的分段格式流式输出，日语一到就送去合成，不用等整段回复结束。

## 📚 文档

| 文档 | 内容 |
|---|---|
| [配置指南](docs/configuration.md) | 模型、角色、通话、立绘、剧情库、检索、记忆、照片、登录、自带密钥、网络代理、服务状态、后台的配置和效果 |
| [部署指南](docs/deploy.md) | 部署到 VPS：域名、防火墙、OAuth、剧情库每周同步、立绘与招呼素材、升级、备份与恢复、排障 |
| [`.env.example`](.env.example) | 全部环境变量及说明 |
| [架构决策记录](docs/adr/) | 为什么这样设计：日语原声、共享记忆、对公众开放、自带密钥只存在浏览器里等 |
| [`GLOSSARY.md`](GLOSSARY.md) | 领域术语表 |
| [社区玩法仓库](docs/community-forks.md) | 大家在各自 fork 上做的玩法，以及怎么登记自己的 |
| [`DESIGN.md`](DESIGN.md) · [`PRODUCT.md`](PRODUCT.md) | 视觉规范 · 产品定位 |

## 🌐 部署

生产配置 [`docker-compose.prod.yml`](docker-compose.prod.yml) 包含 caddy（HTTPS 入口）、frontend、backend 和 coturn（TURN 中继）四个服务，前后端镜像由 CI 发布到 GHCR：

```sh
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

数据库由 `deploy/db-backup.sh` 每天加密备份到私有存储桶，默认保留 30 天。完整步骤见 [部署指南](docs/deploy.md)。

## 💻 开发

**后端**（Python 3.11+，`backend/`，用 [uv](https://docs.astral.sh/uv/)）：

```sh
cd backend
uv sync
uv run pytest && uv run pyright && uv run ruff check
uv run --env-file=../.env eidolon serve   # 启动在 :8000，读取 ../characters/characters.toml
```

**前端**（Next.js 16 + [shadcn/ui](https://ui.shadcn.com/) + Tailwind CSS v4，`frontend/`）：

```sh
cd frontend
npm install
npm run typecheck && npm test && sh scripts/check-discipline.sh
npm run build && BACKEND_URL=http://localhost:8000 npm run start   # 启动在 :3000，/api/* 转发给后端
```

- **界面**：克制的 LINE 风格，属于她的地方用她的印象色；落地页自成一个永远深色的视觉世界（[ADR-0016](docs/adr/0016-landing-page-is-its-own-visual-world.md)）。视觉规范见 [`DESIGN.md`](DESIGN.md)，组件写法的约定见 [`frontend/AGENTS.md`](frontend/AGENTS.md)。
- **立绘**：要看立绘时，前端也要带上和 `.env` 一样的 `EIDOLON_STAND_ASSETS_URL`。
- **README 的截图**：前后端都跑起来后，在 `frontend/` 下运行 `npm run readme-media`，会重新拍下 `docs/images/` 里的截图和动图。
- **CI**：每个 PR 和 main 上的推送都由 GitHub Actions 跑上面这些检查和 shellcheck，再构建两个镜像；推送到 main 或 `v*` 标签时发布到 GHCR。Dependabot 每周一提依赖更新。

## 📁 项目结构

```text
eidolon-sc/
├── backend/          # Python 后端：FastAPI + Pipecat；对话引擎、通话、检索、记忆、同步与命令行（eidolon）
│   ├── src/eidolon/
│   ├── eval/         #   重排、判断模型和记忆的评测集（只含问题与标注，不含剧情原文）
│   └── tests/
├── frontend/         # Next.js 前端：落地页、对话、通话层、设置、后台
├── characters/       # 角色配置、人设、资料、头像、读音表、世界设定
├── corpus/           # 剧情数据和镜像缓存（不进仓库）
├── deploy/           # Caddyfile、剧情库与立绘的同步脚本、数据库备份脚本
├── scripts/          # 配置 OAuth 应用的向导（setup-oauth.sh）
├── docs/             # 配置指南、部署指南、架构决策记录、README 的图片
├── docker-compose.yml          # 本地开发
└── docker-compose.prod.yml     # 生产部署
```

## 📄 许可证

本项目的代码以 [GNU Affero General Public License v3.0](LICENSE)（AGPL-3.0）或（由你选择）任何更高版本发布。如果你修改了本项目并通过网络向他人提供服务，需要向这些用户公开修改后的源代码。

许可证只覆盖仓库里的代码和文档，下面这些不在范围内：

- **原作的内容**：剧情数据、角色音色、立绘素材不在仓库中，使用时须遵守各自来源的条款（ADR-0003）。仓库里的人设、角色资料、头像和 README 的截图里，角色与设定属于原作版权方。
- **Spine Runtimes**：通话层立绘用的 [`frontend/public/vendor/spine-3.6/`](frontend/public/vendor/spine-3.6/README.md) 原样收录，适用它自己的 Spine Runtimes 许可证（[ADR-0009](docs/adr/0009-spine-runtime-without-editor-license.md)）。[`LICENSING.md`](LICENSING.md) 里有一条 AGPL 第 7 节的附加许可，允许两者组合分发。
- **字体和音效**：落地页的字体子集适用 SIL Open Font License（见 `frontend/src/app/(landing)/fonts/`）；通话音效的来源和许可见 [`CREDITS.md`](frontend/public/sounds/CREDITS.md)。

## 🙏 致谢

- [LAPLACE Eidolon](https://laplace.live/eidolon)：为虚拟主播明前奶绿打造的实时语音 AI 分身，本项目的架构参考（未复用其代码）
- [SCTranslationData](https://github.com/ShinyGroup/SCTranslationData)：剧情文本与中文翻译
- [ShinyColorsDB](https://github.com/ShinyColorsDB)：原文剧情的镜像与立绘素材
- [Pipecat](https://github.com/pipecat-ai/pipecat)：实时语音管线与 WebRTC 传输
- [Fish Audio](https://fish.audio/)：语音合成
- [Soniox](https://soniox.com/)、[阿里云百炼](https://www.aliyun.com/product/bailian)：语音识别
- [硅基流动](https://siliconflow.cn/)：Qwen3 Embedding 与 Reranker
- [Google Gemini API](https://ai.google.dev/)：Gemini Embedding（可选的向量化服务）
- [TypeSafe Jev](https://docs.typesafe.ai/)、[OpenRouter](https://openrouter.ai/)、[Cloudflare Workers AI](https://developers.cloudflare.com/workers-ai/)、[阿里云百炼](https://help.aliyun.com/zh/model-studio/decision-model-api)：判断模型
- [Spine Runtimes](https://github.com/EsotericSoftware/spine-runtimes)：立绘的播放（spine-ts 3.6）
- [im@sparql](https://sparql.crssnky.xyz/imas/)、Lantis、ふじわらはじめ、imas-db、wikiwiki：世界设定里的偶像、唱片和演出资料

<div align="center">

---

<sub>《偶像大师 闪耀色彩》及其角色的版权归 ©Bandai Namco Entertainment Inc. 所有。本项目为非官方粉丝作品，与版权方无关，角色由 AI 扮演。</sub>

</div>
