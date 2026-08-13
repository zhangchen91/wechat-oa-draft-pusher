# wechat-oa-draft-pusher

把写好的文章（Markdown / HTML / Word / 纯文本）一键推送进**微信公众号草稿箱**的 WorkBuddy 技能。只创建草稿、不自动群发，排版细节由你人工确认后再发。

## 特性

- **纯标准库实现**（Python `urllib` / `json` / `re`），无需 `pip install` 任何依赖，Python 3.8+ 即可运行。
- 支持 **Markdown(.md) / HTML(.html) / Word(.docx) / 纯文本(.txt)** 四种正文。
- 自动上传**封面图**与正文中所有本地配图，拿到微信永久素材 `media_id` / 图床地址。
- **默认强制 IPv4 出口**：微信 IP 白名单只接受 IPv4，部分环境默认优先 IPv6（如 `2408:` 开头），会导致 `40164` 且无法加白；强制 IPv4 后微信会看到可加白的 IPv4。可用 `--no-force-ipv4` 关闭。
- 遇到 `40164` 时，**直接从微信报错里解析出它实际看到的调用方 IP**（外部回显服务可能给错），方便你加白名单。
- `access_token` 本地缓存，避免重复请求。

## 安装（WorkBuddy 技能）

```bash
# 方式一：直接克隆到用户级技能目录
git clone <本仓库地址> ~/.workbuddy/skills/wechat-oa-draft-pusher

# 方式二：下载 ZIP 后解压到 ~/.workbuddy/skills/wechat-oa-draft-pusher
```

安装后，在 WorkBuddy 对话里说「使用 wechat-oa-draft-pusher 把 xxx.md 推到我的公众号草稿箱」即可。

## 配置 AppID / AppSecret（三种方式，优先级：命令行 > config.json > 环境变量）

1. **命令行**：`--appid <APPID> --appsecret <APPSECRET>`
2. **配置文件**：复制 `config.example.json` 为 `config.json` 并填入真实值（已被 `.gitignore` 忽略，不会提交）
3. **环境变量**：`export WX_APPID=xxx WX_APPSECRET=xxx`

> AppSecret 只显示一次，请妥善保存，**切勿提交进仓库**。

## 使用

```bash
# 先验证凭据与白名单（dry-run 只取 token + 解析，不建草稿）
python3 scripts/push_to_draft.py \
  --appid "<APPID>" --appsecret "<APPSECRET>" \
  --article "article.md" --dry-run

# 正式推送（带封面与配图目录）
python3 scripts/push_to_draft.py \
  --appid "<APPID>" --appsecret "<APPSECRET>" \
  --article "article.md" \
  --cover "images/cover.png" \
  --images-dir "images" \
  [--title "标题"] [--author "作者"] [--digest "摘要"] \
  [--config config.json] [--token-cache token.json]
```

或直接用配置好的 `config.json`：

```bash
python3 scripts/push_to_draft.py --article "article.md" --cover "images/cover.png" --images-dir "images"
```

## 推荐的文章目录结构

```
正式推送/
├─ article.md        # 正文（支持 frontmatter: title / author / digest）
├─ cover.jpg         # 指定封面
└─ images/           # 正文配图
   ├─ 01.png
   └─ 02.png
```

正文中图片用相对路径引用，例如 `![图1](images/01.png)` 或 `<img src="images/01.png">`，脚本按文章所在目录解析；找不到时回退到 `--images-dir/文件名`。

## 前置准备

1. 一个**已认证**的微信公众号（需要管理员扫码权限）。
2. 公众号 `AppID` 和 `AppSecret`（微信公众平台 → 设置与开发 → 基本配置 → 开发者凭据）。
3. 配置**公众号 IP 白名单**：首次调用若报 `40164`，按脚本提示把出口 IPv4 加入白名单（路径：公众号后台 → 设置与开发 → 基本配置 → IP 白名单）。

## 错误处理速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `40164 api unauthorized` | 出口 IP 不在白名单 | 加脚本报出的 IPv4 到白名单后重试 |
| `40013 invalid appid` | AppID 错误 | 核对 AppID |
| `40125 invalid appsecret` | AppSecret 错误/已重置 | 重新获取并保存 |
| 配图未出现在草稿 | 路径解析失败 | 检查相对路径或用 `--images-dir` |
| 封面为空 | 未传 `--cover` | 传封面图，否则用微信默认封面 |

## 局限

- 只创建草稿，不自动群发；排版样式（字体、间距、标题层级）可能需人工微调。
- 不替代写作，只承担「格式转换 + 图片上传 + 创建草稿」最机械的部分。

## License

MIT
