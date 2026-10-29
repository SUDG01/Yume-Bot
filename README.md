# YumeBot

基于 [NoneBot2](https://github.com/nonebot/nonebot2) 的 QQ 机器人，用于街机音游 **舞萌 DX（Maimai DX）** 的信息查询。

## 功能

| 指令 | 说明 |
|------|------|
| `/info <歌名/ID>` | 查询歌曲信息（定数、谱面、SSS/SSS+ 分数线），支持别名 |
| `/查歌 <关键词>` | 按曲名或别名模糊搜索歌曲 |
| `/定数查歌 <定数/范围>` | 按定数、谱面类型和难度筛选谱面 |
| `/随歌 [条件]` | 随机推荐符合难度、类型或分类条件的谱面 |
| `/b50` | 生成你的 Best 50 成绩图片 |
| `/推分 [目标/难度]` | 根据 B50 与 Selection 生成个性化推分计划 |
| `/成绩趋势 [7/30/90]` | 生成 Rating、同步活跃度和最近成绩趋势图 |
| `/今日舞萌` | 每日人品值 + 随机推歌 |
| `/bind <密钥>` | 绑定 LXNet 个人 API Key |
| `/bind clear` | 清除绑定 |
| `/天气 <城市>` | 查询城市天气 |
| `/ai <子命令>` | AI 聊天管理（开关、触发、随机概率、记忆） |
| `/help` | 显示帮助 |

## 技术栈

- Python 3.11+
- **框架**: NoneBot2 + OneBot V11 适配器
- **协议端**: [NapCat](https://github.com/NapNeko/NapCatQQ)（Docker 部署）
- **数据源**: [LXNet 查分器](https://maimai.lxns.net)
- **图像**: Pillow (PIL)
- **存储**: SQLite

## 项目结构

```
YumeBot/
├── bot.py                   # 入口
├── .env                     # NoneBot 配置
├── plugins/
│   ├── help.py              # /help
│   ├── testplugin/          # /天气
│   ├── get_song_info/       # /info
│   ├── song_search/         # /查歌、/定数查歌、/随歌
│   ├── init_data_maimai/    # 启动时加载歌曲数据
│   ├── user_bind/           # /bind
│   ├── mai_best50/          # /b50
│   ├── mai_recommend/       # /推分
│   ├── mai_trend/           # /成绩趋势
│   └── today_wm/            # /今日舞萌
├── src/
│   ├── tools/
│   │   ├── mai_music.py     # Song/Chart 数据模型 + LXNet API
│   │   ├── get_music_alias.py
│   │   ├── data_cache.py    # 歌曲/别名内存缓存
│   │   ├── maimai_rating.py # Rating 公式与谱面定数查询
│   │   ├── b50_image.py     # B50 图片生成器
│   │   ├── push_recommend.py # 推分收益计算
│   │   ├── push_image.py    # 推分计划图片生成器
│   │   ├── song_search.py   # 曲名、别名和谱面筛选
│   │   ├── trend_data.py    # 趋势数据规范化
│   │   ├── trend_image.py   # 趋势图生成器
│   │   ├── database.py      # SQLite 用户绑定
│   │   ├── qqhash.py        # QQ 哈希（每日稳定性）
│   │   └── bindings.db      # 绑定数据库（自动生成）
│   └── static/
│       ├── b50/             # B50 原创背景与缺省曲绘
│       ├── cover/           # 曲绘封面
│       └── ttf/msyb.ttf     # 字体
├── napcat-docker/
│   └── docker-compose.yml   # NapCat 容器配置
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
# 创建虚拟环境（推荐 pyenv）
python -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置 `.env`

```ini
HOST=0.0.0.0
PORT=3132
COMMAND_START=["/"]
COMMAND_SEP=["."]
```

### 3. 下载曲绘封面

```bash
cd src/static/cover
python getcovers.py
```

### 4. 启动 NapCat（QQ 协议端）

```bash
cd napcat-docker
docker compose up -d
```

首次启动后访问 `http://localhost:6099` 扫码登录 QQ。

### 5. 启动 Bot

```bash
python bot.py
```

### 6. 使用

在 QQ 中向机器人发送 `/help` 查看所有指令。

首次使用 `/b50` 或 `/推分` 前需要先绑定 LXNet API Key：

1. 前往 [maimai.lxns.net](https://maimai.lxns.net) 注册并登录
2. 在「账号详情」生成个人 API 密钥
3. 向 bot 发送 `/bind <你的密钥>`

推分计划支持目标 Rating 和难度筛选，例如 `/推分 15000`、`/推分 14+`
或 `/推分 15000 14+`。

歌曲筛选示例：`/查歌 internet`、`/定数查歌 13.5 14.0 DX 紫`、
`/随歌 紫 14+ POPS`。成绩趋势支持 `/成绩趋势 7`、`/成绩趋势 30` 和
`/成绩趋势 90`。

## AI 聊天（可选）

`plugins/ds_ai_reply` 通过 OpenAI 兼容接口聊天，并带长期记忆：

- 群聊：@ 机器人、叫唤醒词，或按概率随机接话
- 私聊：直接发消息
- 长期记忆：后台异步抽取，跨重启保留；能区分群聊/私聊来源，记忆可混用
- 跨会话上下文：私聊能接上你刚在群里说的话；群聊也能看到你在其他群的发言
- 回忆模式：问到“还记得/上次/我说过”时，扩大长期记忆和聊天记录检索
- 自然表达：`behavior_style` / `reply_style` 控制说话方式，默认短句口语化，
  不用列表/编号/动作描写，随机接话有单独的长度上限
- 提示词保护：被问到系统提示词/内部设定/能力清单时简短带过，不复述结构
- 图片/语音/视频会以 `[图片]` 等占位符进入上下文，不会编造内容（暂不支持真正看图）
- 群管理：`/ai on|off`、`/ai trigger`、`/ai random 0.05`、`/ai clear`、
  `/ai memory me`、`/ai forget me`、`/ai status`、`/ai help`

配置分两份：

| 文件 | 内容 |
|---|---|
| `plugins/ds_ai_reply/aiconfig.toml` | 接口地址、API Key、模型、上下文预算、记忆抽取模型 |
| `plugins/ds_ai_reply/bot_config.toml` | 人格、触发方式、群白黑名单、随机接话概率、限流、记忆策略 |
| `plugins/ds_ai_reply/bot_config.local.toml` | 可选，本机/服务器覆盖项，递归覆盖上一层，已 gitignore |

服务器或本机自定义的人设、管理员、群设置建议写在 `bot_config.local.toml`，不要改
`bot_config.toml`。这样 `git pull` 更新代码时不会冲突：

```bash
cp plugins/ds_ai_reply/bot_config.local.example.toml plugins/ds_ai_reply/bot_config.local.toml
```

1. 复制并填写密钥配置（`aiconfig.toml` 已被 `.gitignore` 忽略，不会提交）：

```bash
cp plugins/ds_ai_reply/aiconfig.example.toml plugins/ds_ai_reply/aiconfig.toml
```

2. 填写 `[provider]` 下的 `base_url`、`api_key`、`model`。

3. 按需修改 `bot_config.toml`。随机接话默认关闭，可在全局或单个群里设置：

```toml
[trigger]
random_reply_probability = 0.05       # 0.00 ~ 1.00，越高越容易接话

[groups.overrides."群号"]
random_reply_probability = 0.20
```

或在群里让管理员发送 `/ai random 0.20`。

聊天记录 `hajimi.db` 也会被 `.gitignore` 忽略。机器人会保存聊天用于上下文和长期记忆
（原始消息默认保留 30 天），发送 `/ai forget me` 可以删除自己的记忆。长期记忆、用户画像和
群管理的完整设计见 [aim.md](aim.md)。

### AI 观测台

`bot_config.toml` 的 `[dashboard]` 默认开启，只监听本机：

```
http://127.0.0.1:3133/
```

可以实时看到：

- 收到的群聊/私聊消息
- 触发判断（@、唤醒词、随机接话的 roll 值、静音、限流）
- 本次注入的画像、边界、长期记忆、跨会话消息
- 发给模型的完整 prompt
- 模型返回、耗时、token
- 最终回复
- 记忆抽取结果和后台任务错误

服务器上不要直接把 3133 暴露公网，用 SSH 隧道：

```bash
ssh -L 3133:127.0.0.1:3133 user@服务器
```

然后本地打开 `http://127.0.0.1:3133/`。如果修改 `host` 为非本地地址，必须在配置里设置
`dashboard.token`，并通过 `?token=...` 访问。观测事件默认保留 7 天。

## 接入更多用户

其他用户同样只需在 LXNet 生成自己的 API Key 后 `/bind` 即可，bot 会使用各自的 Key 查询各自的数据。

## 致谢

- [NoneBot2](https://github.com/nonebot/nonebot2) — 机器人框架
- [NapCat](https://github.com/NapNeko/NapCatQQ) — QQ 协议端
- [LXNet](https://maimai.lxns.net) — Maimai 数据 API
- [maimai-bot](https://github.com/Diving-Fish/maimaidx-prober) — 参考项目
- [MaiBot](https://github.com/Mai-with-u/MaiBot) — AI 聊天风格与跨会话记忆的设计参考
