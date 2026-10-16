# ISP_for_clash

一个可安装的 Codex skill：把用户提供的 ISP 代理添加到指定的 Clash Verge Rev / Mihomo 订阅，支持直连、地区匹配中继，以及在已有 `AI` 等分组中直接选择。

**Skill 名称：`clash-isp`。** 本仓库提供配置流程与离线准备工具，不提供代理服务，也不包含真实代理账号、密码或机场订阅链接。

## 在其他 Codex 对话中安装

把下面这段话直接发给新对话即可：

```text
请使用 skill-installer，从这个 GitHub 仓库安装 clash-isp skill：
https://github.com/choucaicai/ISP_for_clash

分支：master
Skill 路径：skills/clash-isp
完整地址：https://github.com/choucaicai/ISP_for_clash/tree/master/skills/clash-isp

只安装 skill，不修改我的 Clash 配置。
如果已经安装，不要直接覆盖，先说明现有版本与仓库版本的差异。
```

这里明确指定 `master`，因为安装器可能默认使用 `main`。安装完成后在下一轮对话调用 `$clash-isp`；如果当前环境没有刷新技能列表，开启新对话再使用。

仅发送仓库链接不等于安装授权，建议连同“安装 skill”的请求一起发送。该流程针对支持本地 skills 的 Codex 环境，不代表普通网页聊天可以直接修改电脑配置。

### 使用 Codex 自带安装器

如果环境已提供 `skill-installer`，也可以在终端执行以下命令（macOS / Linux）：

```bash
CODEX_SKILLS_HOME="${CODEX_HOME:-$HOME/.codex}"
python3 "$CODEX_SKILLS_HOME/skills/.system/skill-installer/scripts/install-skill-from-github.py" \
  --repo choucaicai/ISP_for_clash \
  --ref master \
  --path skills/clash-isp
```

默认安装到 `$CODEX_HOME/skills/clash-isp`，未设置 `CODEX_HOME` 时是 `~/.codex/skills/clash-isp`。安装器脚本位置以当前 Codex 环境为准。已有同名目录时安装器会拒绝覆盖。

公开仓库通常可直接下载。私有仓库需要相应 GitHub 访问权限；不要把 token 写进聊天或安装命令。当前环境若仅能使用 SSH，可先通过 SSH 克隆，再按下节手动安装。

### 手动安装

macOS / Linux，在尚不存在目标目录时执行：

```bash
git clone --branch master git@github.com:choucaicai/ISP_for_clash.git

SKILLS_DIR="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$SKILLS_DIR"
if [ -e "$SKILLS_DIR/clash-isp" ]; then
  printf '%s\n' '已存在 clash-isp，请先比较差异并备份，不要直接覆盖。'
else
  cp -R ISP_for_clash/skills/clash-isp "$SKILLS_DIR/clash-isp"
fi
```

SSH 克隆要求本机已有可访问该仓库的 SSH 身份；公开仓库也可把克隆地址替换为 `https://github.com/choucaicai/ISP_for_clash.git`。

Windows 可将仓库内整个 `skills/clash-isp` 文件夹复制到 `%USERPROFILE%\.codex\skills\clash-isp`；如果配置了 `CODEX_HOME`，使用对应的 `skills` 目录。不要只复制 `SKILL.md`，辅助脚本和参考文档也需要保留。

## 安装后怎么用

在 Codex 中提供目标订阅、ISP 信息和希望的分流方式，例如：

```text
使用 $clash-isp

目标订阅：Nexitally_Clash.yml
目标分组：AI，保留原有机场节点供选择

ISP 列表：
<IP:端口:用户名:密码>
<IP:端口:用户名:密码>

协议：SOCKS5，端口以我提供的实际信息为准
地区：第一条是美国，第二条是新加坡
中继：美国 ISP 用该订阅的美国节点，新加坡 ISP 用该订阅的新加坡节点
直连选项：本次不添加
默认选择：保留当前选择
网站分流：Claude、ChatGPT、Gemini、Kiro 跟随 AI 分组

先备份，配置后重新激活并核对实际出口；不要修改其他订阅或普通网站分流。
```

也可以只追加节点，不修改规则：

```text
使用 $clash-isp，把我提供的 ISP 添加到指定订阅的“工作代理”分组。
本次只添加直连选项，不改任何网站规则，也不切换当前选中的节点。
目标订阅和 ISP 信息如下：……
```

真实凭据只应在你信任的本地配置流程中提供，不要提交到 GitHub issue、PR、README 或版本库。

## 支持的能力

- 按订阅名称或精确 UID 选择目标；同名订阅先区分，不擅自修改全部订阅。
- 添加多个带认证的 SOCKS5 / HTTP ISP，支持直连和“机场节点 → ISP”的中继。
- 根据已确认的购买地区匹配中继，或使用用户指定的确切节点名。
- 加入已有手动选择分组，保留其余机场选项；也可按要求新建分组。
- 按用户指定的网站配置规则，合并旧组时同步检查旧规则引用。
- 备份、检测冲突、校验配置、重新生成增强配置、保存选择，并测试真实出口。

默认使用**目标订阅的局部增强**，避免修改会被更新覆盖的远程原文件。不同版本的客户端路径、增强格式和 API 需要现场确认；不保证任意 Clash 分支都能直接使用同一套写入方式。

## 工作原理与端口

典型中继链路：

```text
命中 AI 规则的网站
  -> AI 分组内选中的 ISP 选项
  -> 该 ISP 的 dialer-proxy 指定机场节点
  -> ISP SOCKS5 / HTTP 服务
  -> 目标网站（最终出口为 ISP）
```

本地 Clash 混合端口与远端 ISP 端口是两回事，必须从实际配置读取，不能固定写成某个值。

HTTP 代理通常也能通过 CONNECT 访问 HTTPS 网站；这不代表连接代理入口本身使用 TLS。SOCKS5 的端口同样以供应商提供的信息为准，不能凭端口号猜协议。

## 离线准备工具

`skills/clash-isp/scripts/prepare_profile.py` 只读取配置并生成**待应用文件**：不会直接改实时 Clash 配置、切换代理、上传凭据或发送网络测试请求。完整输入格式和应用检查见 [操作参考](skills/clash-isp/references/verge-workflow.md)。

依赖：Python **3.9+** 和 PyYAML。仅阅读和安装 skill 不需要先运行这个工具；工具执行环境缺少依赖时再在合适的虚拟环境中安装：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows 的虚拟环境激活命令是 `.venv\Scripts\Activate.ps1`，之后使用该环境的 Python。

列出本机订阅，不输出订阅 URL 或密码：

```bash
python skills/clash-isp/scripts/prepare_profile.py \
  --root "/实际的/Clash数据目录" --list
```

根据私有请求文件生成待应用配置：

```bash
python skills/clash-isp/scripts/prepare_profile.py \
  --root "/实际的/Clash数据目录" \
  --profile "实际订阅UID" \
  --spec "/私有目录/request.json" \
  --out "/私有目录/new-bundle"
```

输出的节点配置仍包含凭据，必须留在私有目录，不能提交到本仓库。应用前检查源文件哈希、备份、最终配置和运行时状态；核心 reload 并不等于客户端已重新生成增强配置。

## 安全与验证边界

- 不包含任何预置代理账号、机场订阅或用户本机配置；不会要求购买代理。
- 不默认关闭系统代理 / TUN，也不把其他订阅的节点静默引入目标订阅。
- 不将绿色状态、单次延迟或历史测试结果当作本次出口验证。
- 不通过关闭 HTTPS 证书校验掩盖错误。
- 同一个 ISP 的直连和中继可能对应不同来源 IP，服务商的来源限制可能影响测试；不应同时盲目压测。
- 切换分组只影响新连接；旧连接可能需要重连。

本仓库的自动化测试使用合成配置，不访问真实代理，也不证明你的网络或供应商服务一定可用。

## 开发与测试

```bash
python -m unittest discover -s skills/clash-isp/scripts -p 'test_*.py' -v
```

其中 JavaScript 行为测试需要 Node.js。默认从 `PATH` 查找 `node`；也可通过 `CLASH_ISP_NODE` 指定完整路径。缺少 Node 时会跳过这部分测试；要完整验证请确保没有相关 skip。

仓库结构：

```text
ISP_for_clash/
├── README.md
├── requirements.txt
└── skills/
    └── clash-isp/
        ├── SKILL.md
        ├── agents/openai.yaml
        ├── references/verge-workflow.md
        └── scripts/
            ├── prepare_profile.py
            └── test_prepare_profile.py
```

更新仓库后，已安装到个人 skills 目录的副本不会自动同步。更新前比较差异、备份已有定制，再替换相应文件；不要把“重新安装”当作未经确认的覆盖操作。
