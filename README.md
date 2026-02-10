# astrbot_plugin_minecraftconsole

一个用于 **AstrBot v4** 的 Minecraft RCON 控制台插件：在聊天中通过指令把命令转发到 Minecraft 服务器执行。

> [!WARNING]  
> 本插件及文档由 AI 生成，内容仅供参考，请仔细甄别。
>
> 已测试1.8.8
>
> 插件目前仍处于开发阶段，无法 100% 保证稳定性与可用性。

---

## ✨ 功能特性

- ✅ `/mc-command <命令>`：通过 RCON 执行 Minecraft 控制台命令（例如 `say hello`、`time set day` 等）
- ✅ 管理员校验：仅允许 `admins` 列表中的用户使用
- ✅ 异步 RCON（asyncio）：不阻塞事件循环
- ✅ 连接复用：插件运行期间复用连接，失败自动重连
- ✅ 输出截断：防刷屏（可配置最大输出长度）

---

## 📦 安装

### 方式一：手动安装

1. 将插件文件夹放入 AstrBot 插件目录，例如：

   - Windows：`C:\Users\<你>\.astrbot\data\plugins\astrbot_plugin_minecraftconsole`
   - Linux：`~/.astrbot/data/plugins/astrbot_plugin_minecraftconsole`

2. 确保插件目录中至少包含以下文件：

   ```text
   astrbot_plugin_minecraftconsole/
     __init__.py
     main.py
     config.py
     rcon_client.py
     utils.py
     message_formatter.py
     _conf_schema.json
   ```

3. 重启 AstrBot 或在面板中重新加载插件。

---

### 方式二: 使用AstrBot客户端远程下载安装

## ⚙️ 配置说明

本插件使用 AstrBot 的插件配置系统（`_conf_schema.json`）自动生成配置项并自动保存。

在 AstrBot 管理面板中找到本插件配置并填写：

| 配置项 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 是否启用插件 |
| `admins` | list | `[111, 222, 333]` | 管理员 user_id 列表（允许使用 `/mc-command`） |
| `rcon_host` | string | `127.0.0.1` | RCON 地址 |
| `rcon_port` | int | `25575` | RCON 端口 |
| `rcon_password` | string | `""` | RCON 密码（必填） |
| `timeout` | float | `5` | 超时时间（秒） |
| `max_output` | int | `1500` | 聊天回显最大长度（超过会截断） |

---

### ✅ admins 示例（你想要的写法）
yaml
admins:
  - 111
  - 222
  - 333

> 说明：`admins` 支持数字或字符串，只要最终能匹配 `event.get_sender_id()` 的值即可。

---

## 🧩 Minecraft 服务端设置（必须）

在 Minecraft 服务端 `server.properties` 中确保开启 RCON：
properties
enable-rcon=true
rcon.port=25575
rcon.password=你的密码

修改后需要重启 Minecraft 服务端才能生效。

### 🔥 常见网络问题提醒

- 若 Minecraft 服务器不在本机，注意：
  - 防火墙放行 `rcon.port`
  - 云服务器安全组放行端口
  - Docker/面板服注意端口映射（如 `-p 25575:25575`）

---

## 🚀 使用方法

在聊天中发送：
text
/mc-command say hello

更多示例：
text
/mc-command time set day
/mc-command weather clear
/mc-command list
/mc-command difficulty hard

### ⚠️ 重要：MC 命令不要带斜杠

你给插件的参数应当是 **控制台命令**：

- ✅ 正确：`/mc-command say hello`
- ❌ 错误：`/mc-command /say hello`

---

## 🛠️ 常见问题（FAQ）

### 1) 提示“你没有权限使用该指令”

检查 AstrBot 面板配置里的 `admins` 是否包含你的 `user_id`。

### 2) 提示“RCON 未配置：请填写 rcon_password”

检查：

- 插件配置里的 `rcon_password` 是否填写
- `server.properties` 是否 `enable-rcon=true`
- Minecraft 服务端是否重启

### 3) Minecraft 返回 `Unknown command`

这通常是因为你发给 Minecraft 的命令不对。

请确保你执行的是 Minecraft 控制台命令，例如：

- `say hello`
- `time set day`

而不是：

- `mc-command say hello`

如果你在插件回显中看到它执行了 `mc-command say hello`，说明参数解析逻辑有问题，请确认你已使用最新版的 `utils.py` 并使用 `parse_command_args()` 进行解析。

### 4) 执行命令卡住/超时

可能原因：

- `rcon_host`/`rcon_port` 填错
- 端口没放行（防火墙、安全组、Docker 映射）
- `timeout` 太小
- 服务端 RCON 不稳定或被限制

---

## 🔒 安全建议

- **强烈建议**：
  - 不要在群聊公开暴露 RCON 功能
  - 将 `admins` 设置为最小范围
- 如果担心误操作：
  - 你可以自行加命令黑名单/白名单（例如禁止 `stop` / `op` 等）

---