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

## 接入更多用户

其他用户同样只需在 LXNet 生成自己的 API Key 后 `/bind` 即可，bot 会使用各自的 Key 查询各自的数据。

## 致谢

- [NoneBot2](https://github.com/nonebot/nonebot2) — 机器人框架
- [NapCat](https://github.com/NapNeko/NapCatQQ) — QQ 协议端
- [LXNet](https://maimai.lxns.net) — Maimai 数据 API
- [maimai-bot](https://github.com/Diving-Fish/maimaidx-prober) — 参考项目
