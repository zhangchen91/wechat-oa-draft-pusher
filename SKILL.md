---
name: wechat-oa-draft-pusher
agent_created: true
description: 把写好的文章（Markdown/Word/HTML/纯文本）推送进微信公众号草稿箱。当用户想把 WorkBuddy 生成或整理好的文章推送到公众号、配置 AppID/AppSecret、处理 IP 白名单(40164)错误、设置封面与配图并创建草稿时使用。仅创建草稿，不自动群发。
---

# 微信公众号草稿推送（WeChat OA Draft Pusher）

把已完成排版/写作的文章推送到公众号**草稿箱**（draft box），由用户手动检查后再群发。本 Skill 只负责「创建草稿」，不会自动发布，避免违规。

## 何时使用

- 用户说「帮我把这篇文章推到公众号草稿箱」「推送到公众号」「连接公众号」等。
- 用户需要配置公众号 AppID / AppSecret 或遇到 `40164` IP 白名单报错。
- 输入形态支持：Markdown(`.md`)、HTML(`.html`)、Word(`.docx`)、纯文本(`.txt`)；可带封面图与正文配图。

## 前置准备（首次必读）

阅读 [references/setup.md](references/setup.md)，确认用户已具备：

1. 一台装了 WorkBuddy 桌面端的电脑。
2. 一个**已认证**的微信公众号（需要管理员扫码权限）。
3. 公众号 `AppID` 和 `AppSecret`（微信公众平台 → 公众号设置 → 基本配置 / 开发者凭据）。
   - **AppSecret 只显示一次**，生成后立即安全保存，切勿写入会被提交的文件中。
4. 配置**公众号 IP 白名单**：首次调用接口若报 `40164`，按 Skill 提示把出口 IP 加入白名单。

## 工作流程

### 1. 收集凭据与文章

向用户索取（不要主动猜测）：

- `AppID`、`AppSecret`
- 文章文件路径（或正文 + 配图文件夹）
- 封面图路径（可选，强烈建议提供）
- 标题 / 作者 / 摘要（可选，缺省自动推断：标题取首个 H1 或文件名，摘要取首段前 120 字）

**安全约定**：

- AppSecret 只允许出现在对话/本地缓存(`.gitignore` 忽略)中，**绝不在日志或回显里打印完整 AppSecret**。
- 把凭据传入脚本参数，或用本地 `config.json`（已加入 `.gitignore`），或用环境变量 `WX_APPID` / `WX_APPSECRET`。**不要把凭据写进 Skill 仓库**。

**凭据的三种配置方式（优先级：命令行 > config.json > 环境变量）**

1. 命令行：`--appid <APPID> --appsecret <APPSECRET>`
2. 配置文件：复制 `config.example.json` 为 `config.json`，填入真实值（已被 `.gitignore` 忽略，不会提交）
3. 环境变量：`export WX_APPID=xxx WX_APPSECRET=xxx`

配置好之后，后续调用只需 `--article` 等参数，无需每次贴 AppSecret，也更安全。

### 2. 先验证凭据与 IP 白名单

运行脚本时，**只用 token 获取来验证**即可触发白名单检查：

```bash
python3 <skill-root>/scripts/push_to_draft.py \
  --appid "<APPID>" --appsecret "<APPSECRET>" \
  --article "<文章路径>" --dry-run
```

- 脚本**默认强制 IPv4 出口**。原因：微信 IP 白名单字段只接受 IPv4，部分环境默认优先 IPv6（如 `2408:` 开头），会导致 `40164` 且无法加白。强制 IPv4 后微信会看到可加白的 IPv4。若你的环境需要 IPv6，可加 `--no-force-ipv4` 关闭。
- 若出现 `40164`：脚本会**直接从微信报错里解析出它实际看到的调用方 IP**（最权威，外部回显服务可能给错），引导用户去公众号后台【设置】→【公众号设置】→【IP白名单】添加该 IPv4，然后重试。
- 验证通过后，再进行正式推送。

### 3. 正式推送

```bash
python3 <skill-root>/scripts/push_to_draft.py \
  --appid "<APPID>" --appsecret "<APPSECRET>" \
  --article "<文章路径>" \
  --cover "<封面图路径>" \
  --images-dir "<配图目录>" \
  [--title "标题"] [--author "作者"] [--digest "摘要"] \
  [--token-cache "<token缓存路径>"]
```

脚本会：① 验证凭据；② 解析正文（Markdown/HTML/Word/纯文本）；③ 上传封面与正文中所有本地配图；④ 调用 `cgi-bin/draft/add` 创建草稿。

**更新已有草稿（而非新建）**：加上 `--update-media-id <MEDIA_ID>` 参数，脚本会改为调用 `cgi-bin/draft/update` 原地更新该草稿（不会再多出一条）。`MEDIA_ID` 取自首次创建草稿成功时日志里打印的值（用户可从公众号后台草稿箱点开草稿、地址栏 `media_id=` 参数也能看到）。适合「文章改了一版、只想更新原草稿」的场景。

### 4. 交付与边界

- 推送成功后，告诉用户到公众号后台【草稿箱】检查**标题、封面、配图、排版**。
- **只创建草稿，不自动群发**。排版细节（字体、间距、标题层级）可能需人工微调，提醒用户检查。
- 本 Skill 不替代写作，只承担「格式转换 + 图片上传 + 创建草稿」最机械的部分。

## 图片与路径约定

- 正文中图片用相对路径引用（如 `![图1](images/01.png)` 或 `<img src="images/01.png">`），脚本按文章所在目录解析；找不到时回退到 `--images-dir/文件名`。
- 所有图片先上传为**永久素材**（`material/add_material`），正文里替换为微信图床 `mmbiz.qpic.cn` 地址，封面 `thumb_media_id` 也来自永久素材。
- 推荐文章文件夹结构：

```
正式推送/
├─ article.md        # 正文
├─ cover.jpg         # 指定封面
└─ images/           # 正文配图
   ├─ 01.png
   └─ 02.png
```

## 错误处理速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `40164 api unauthorized` | 出口 IP 不在白名单 | 加白名单 IP 后重试 |
| `40013 invalid appid` | AppID 错误 | 核对 AppID |
| `40125 invalid appsecret` | AppSecret 错误/已重置 | 重新获取并保存 |
| 配图未出现在草稿 | 路径解析失败 | 检查相对路径或用 `--images-dir` |
| 封面为空 | 未传 `--cover` | 传封面图，否则用微信默认封面 |

## 进阶

- 可把排版模板（CSS/样式约定）前置到正文 HTML，让推送结果更接近最终样式；这属于 2.0 增强。
- 支持定时任务：用户可让 Agent 周期性调用本 Skill 把成稿推入草稿箱。
