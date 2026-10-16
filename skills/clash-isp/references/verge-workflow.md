# Clash Verge Rev / Mihomo 操作细节

仅在本次要操作 Clash 配置时阅读。路径、UID、节点、端口必须现场发现；这里没有用户凭据或硬编码订阅。

## 1. 发现目标订阅与运行时

常见数据目录候选：

- macOS：`~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev`
- Windows：`%APPDATA%/io.github.clash-verge-rev.clash-verge-rev`
- Linux：`~/.local/share/io.github.clash-verge-rev.clash-verge-rev`

这些是候选，不是跨版本保证。优先用应用的“打开配置目录”或核心进程命令行确认实际位置及 `-f` 文件；不要读取/输出无关订阅密钥。

`profiles.yaml` 通常包含 `current` 和 `items`。通过目标项目的精确 UID 和 `file` 找到订阅原文件；`option.proxies`、`option.groups`、`option.rules`、`option.script` 等指向局部增强。必要时通过同 UID 元数据查 `file`，不要拿另一个订阅的增强 UID 复用。没有某种增强引用时，先通过客户端创建该订阅的对应增强，再重新读取；不要臆造 UID 写元数据。

同名订阅尤其常见。展示名称、UID、当前状态、更新时间足够供用户选择，不展示完整订阅 URL。用户说“当前订阅”才可直接使用 `current`。

当前启用订阅的实际分组、选择及端口以运行时 API 为准，另保存客户端元数据中的 `selected`。核心可能通过 Unix socket、认证 HTTP controller 或其他本地入口暴露 API：

```bash
# SOCKET_PATH 必须来自实际运行的核心/配置，而不是照抄固定值。
curl --noproxy '*' --unix-socket "$SOCKET_PATH" http://localhost/proxies
curl --noproxy '*' --unix-socket "$SOCKET_PATH" http://localhost/rules
curl --noproxy '*' --unix-socket "$SOCKET_PATH" http://localhost/configs
```

使用认证 controller 时保护 secret，不把 secret 放进日志。不要为操作方便开启外网监听。记录规则模式、系统代理/TUN 状态、混合端口和单独 HTTP/SOCKS 端口；这些端口并不总是 7897。

## 2. 准备局部增强

典型节点增强结构是 `prepend` / `append` / `delete`，它不是一份完整 Clash 配置：

```yaml
prepend: []
append:
  - name: ISP US-01 (via LA)
    type: socks5
    server: 192.0.2.10
    port: 12324
    username: USERNAME_FROM_USER
    password: PASSWORD_FROM_USER
    dialer-proxy: EXACT_EXISTING_US_NODE
delete: []
```

示例 IP 是文档地址，不能用于连接。直连变体省略 `dialer-proxy`。不在默认模板添加 `udp: true`；上游不支持 UDP 时不能靠 Clash 声明补足。HTTP 代理使用 `type: http` 及商家给定端口，不能把 HTTPS 目标与 TLS 代理入口混淆。

不要修改远程原文件，它会被订阅更新覆盖。也不要把生成的 `clash-verge.yaml` 当持久化唯一来源。客户端可能会把追加节点注入 `Proxies`，要观察最终结果，不能因此改变普通分组的当前选择。

已有分组优先用目标订阅脚本找到并更新 `config["proxy-groups"]` 中的对象。保留原有 `main` 行为；如果已有复杂脚本，包装其返回结果而不是整份覆盖。新增候选放在组内前面，保留其余选项并去重。已有自动选择组改成 `select` 是行为改变，需用户同意。

脚本和节点增强在不同版本中的执行顺序不能想当然。用已知节点名称列表更新组，或在明确可用的阶段操作；最后必须检查客户端生成后的组成员和引用。

### 准备工具输入

在权限为 0700 的暂存目录里写权限为 0600 的 `request.json`，不要用含凭据的 shell 参数/heredoc 作为常规传递方式。可用文件编辑工具或读取用户已给出的私有文件。

```json
{
  "group": "AI",
  "create_group": false,
  "nodes": [
    {
      "name": "ISP US-01 (via LA)",
      "type": "socks5",
      "server": "192.0.2.10",
      "port": 12324,
      "username": "USERNAME_FROM_USER",
      "password": "PASSWORD_FROM_USER",
      "dialer_proxy": "EXACT_EXISTING_US_NODE"
    }
  ],
  "domains": [
    {"type": "DOMAIN-SUFFIX", "value": "chatgpt.com"},
    {"type": "DOMAIN-SUFFIX", "value": "openai.com"}
  ],
  "redirect_from": []
}
```

- `nodes` 是本次要加入的选项，至少一个。无需重复列出过去已管理的节点。供应商不同可改名称，不强制 IPRoyal。
- `type` 仅接受 `socks5` / `http`。可选 `tls` 是到代理入口的 TLS；未指定时保持协议默认。
- `create_group` 默认 false；仅用户要求新建组时启用。工具无法代替最终配置检查。
- `domains` 默认空，不默认扩展 AI 域名。只支持精确 `DOMAIN` 和 `DOMAIN-SUFFIX`；其他规则由 agent 按实际语义编辑。
- `redirect_from` 例如 `["AI-IPRoyal"]`：仅把该订阅现有**规则增强**的相应策略改为目标组，不删除组，也不修改原订阅规则。迁移后继续检查生成结果及其他增强/脚本引用。

执行 `prepare_profile.py` 后，输出目录包含按原相对路径排布的待应用增强、带原哈希的 `manifest.json` 和无凭据的 `plan.json`。输出的节点增强仍含凭据，只留本地私有目录。工具在缺少增强引用、未知格式、名称冲突、危险中继引用时失败；不要绕过报错强行覆盖。

应用不是直接把整个输出目录复制到数据目录。只按 manifest 的 `files` 映射逐个处理：比对源哈希、备份原文件、保留权限、写入相应目标；不要复制 manifest/plan 或替换 `profiles.yaml`。生成后若有新变化，停止并重新准备。现存同名节点协议或端点不同属于冲突，须明确解决，不能误改另一个服务。

工具以带目标组标识的管理块包装 `main`，更新同一组时替换自己的管理块，保留用户脚本；默认仅添加，不自动清理旧 ISP。要只保留部分候选时按用户要求明确删去对应管理块名称，再校验，不能把“追加”当成“替换全部”。

## 3. 规则与合并分组

将一组 ISP 候选从旧分组移到 `AI` 时，节点迁移与规则迁移必须同时核对。否则高优先级 `...,AI-IPRoyal` 仍会绕过用户在 `AI` 的选择。

按用户授权的服务范围处理规则。可以包括 Claude、ChatGPT/OpenAI、Gemini、Kiro 等，但不能仅因这次历史例子就把整张 AI 清单加到每个订阅。优先保留订阅已覆盖的其他 AI 服务。需要域名审计时再查官方网络文档，避免把全部 `google.com`、`amazonaws.com` 等共享后缀导向 ISP。

去除旧组前搜索所有增强、脚本、生成后的规则与组成员，确认没有引用，包括 `dialer-proxy`。只删除本次明确合并的组，保留无关组。旧 `selected` 元数据可能留有无效历史记录，不等于运行时仍有该组；优先由客户端管理保存，不为清理记录整份重写元数据。

出口测试规则（如 `DOMAIN,api.ipify.org,AI`）也属于规则变更：已有则可沿用；新增仅在用户同意测试/分流的范围内，临时规则测试后清理，不把测试站永久改道当作隐含要求。

## 4. 校验、生成与选择持久化

1. 备份将改文件、原始哈希及全部运行时选择。确保当前订阅没有在过程中被用户切走，文件也无并发变化。
2. 核对 ISP 类型/端口、中继节点实际存在、无循环、组内名字唯一、原有选项保留。若增强里的节点不在原订阅，要把所有相关增强一起考虑，不能仅检查原文件。
3. 可用 Mihomo 二进制校验独立候选：`mihomo -t -d DATA_DIR -f CANDIDATE_YAML`。不要启动候选去抢占当前端口。
4. 客户端“重新激活订阅”会重新生成增强后的运行配置。单独 `PUT /configs` reload 只读取已生成配置，不证明新增强已应用。不同版本功能名称可能不同；用实际 UI/受支持接口，不猜调用名。
5. 重新激活可能重置选择，恢复其他组并设置本次目标。目标是非活动订阅时先不要激活；可做离线校验并说明待启用验证。
6. 检查 `/proxies` 的实际 `type`、组成员和 `now`，`/rules` 的真实策略，以及 `/configs` 的真实端口。绿色 alive 和界面节点名不能证明协议或出口正确。
7. API 的 `PUT /proxies/{URL_ENCODED_GROUP}` 可暂时切换选择，但不保证写回客户端。用 UI 选中目标并检查该订阅 `selected`，或使用实际存在的客户端持久化接口。应用期间不要擅自改用户 OS 代理/VPN/TUN 设置。
8. 校验失败时只回退本次改动；已有新变化则停止，不能用旧备份覆盖用户新编辑。最多两次针对性修复，再报告具体失败阶段和保留的备份。

## 5. 验证与 timeout 分类

最小验收：从本地混合/HTTP 端口访问 HTTPS IP 回显端点，返回值必须是用户选定 ISP。该请求要明确命中目标组，必要时观察连接页/API 的 `chains`、`rule`、`rulePayload`。使用 curl 的正常证书校验：

```bash
curl --noproxy '' --proxy "http://127.0.0.1:$ACTUAL_MIXED_PORT" \
  --connect-timeout 10 --max-time 20 --fail --silent --show-error \
  --write-out '\nHTTP %{http_code}, total %{time_total}s\n' \
  https://api.ipify.org
```

测试多个节点可使用隔离 Mihomo 实例：独立私有目录、空闲回环端口、只包含本次节点和必需中继，不监听局域网、不启动 TUN、不改变系统代理或全局 AI 选择，结束后停止实例并处理临时凭据文件。不要通过含敏感信息的 verbose 日志排错。

本次只改分组时一次出口验收通常足够。用户要求稳定性比较时才按**同一路径顺序**测每个选项（例如每个 5 次，或按指定的 10 次），记录成功率、实际出口、目标站及总请求耗时；不要把总 HTTPS 耗时称为 TCP RTT。连续两次失败后先诊断，避免无限重试。

- TCP connect timeout：本机至 ISP/中继的网络、防火墙、TUN 路由、上游可达性；不证明密码错误。
- SOCKS 认证拒绝 / HTTP 407：协议、用户名密码、供应商权限或来源限制。
- TLS 错误：区分“连代理入口的 TLS”与“HTTPS 目标的 TLS”；不能用 `-k` 作为修复。
- HTTP 403/目标站阻断：可能代理已通但网站拒绝；与端口 timeout 分开报告。
- 出口仍是机场：可能只测到前置、未命中目标规则、选错组或旧连接复用。

“直连”测试要显式排除 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` 影响，但 TUN/VPN 仍可能透明接管；不能仅凭 curl 参数证明走了物理直连。用户要求关闭现有代理才能测试时，按当前工具权限处理，并记录/恢复原状态。

IPRoyal 关于 ISP 同时来源限制的参考：[官方帮助](https://help.iproyal.com/en/articles/7222115-how-many-devices-can-i-simultaneously-use-with-isp-proxies)。产品条款可能变化，以用户当前套餐和实际响应为准；不同来源的直连/中继不要并发压测同一个 ISP。

工具自身测试（使用合成配置，不接触实时配置）：

```bash
python3 -m unittest discover -s scripts -p 'test_*.py' -v
```
