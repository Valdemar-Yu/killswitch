# killswitch

A macOS PF kill switch for Clash Verge Rev / Mihomo. It blocks ordinary Internet traffic after detecting that the core has exited, TUN is disabled, or the configured virtual interface is missing.

**killswitch** 面向 Claude Code 等自动运行、自动重试的客户端：当 Clash 失效时，及时封锁普通外网流量，降低任务继续运行时意外直连、暴露本机出口 IP 的风险。它在系统层工作，不需要修改 Claude 或其他客户端。

核心和 TUN 正常时保留 Clash 的规则分流，国内网站的 `DIRECT` 访问可以继续使用。热点断开本身不触发封锁；TUN 确实消失或关闭则会触发。

> [!IMPORTANT]
> 需要保密出口 IP 的服务应在 Clash 中走代理。健康状态下，本项目的 PF anchor 为空，不强制所有程序进入 TUN，也不会拦截规则中的 `DIRECT`。`DIRECT` 目的站点看到本机真实出口 IP 是正常结果。

## 适用环境

- macOS，使用系统自带的 PF 和 launchd。
- Clash Verge Rev 的 Mihomo 服务模式，开启 TUN。
- `/usr/bin/python3`，版本 3.9 或更新。
- 安装、启用和回滚需要管理员权限。

默认核心位置为 Clash Verge Rev 的标准安装目录。安装时通过 `sudo` 获取桌面用户 UID，并据此生成本机 Unix socket 路径，不固定用户名、用户编号或 `utun` 编号。其他 Mihomo 安装方式可以手动指定核心路径、核心 UID 和 Unix socket。

## 安装与快捷命令

先打开 Clash Verge Rev 并开启 TUN，再安装：

```bash
git clone https://github.com/Valdemar-Yu/killswitch.git
cd killswitch
sudo /bin/sh install.sh
```

安装完成后直接使用：

```bash
killswitch status       # 查看守护进程和当前规则
killswitch rollback     # 解除防护，移除本项目的规则
killswitch enable       # 回滚后重新启用
```

也提供兼容快捷命令：

- `clash-enable`：重新启用。
- `clash-rollback`：回滚。
- `clash-status`：查看状态。

这些命令会按需要自动请求 `sudo` 密码。启用成功后输出 `successfully enable！`，回滚成功后输出 `successffully rollback！`；失败不会显示成功提示。

回滚会移除本项目的 LaunchDaemon 和 PF anchor，并释放本项目持有的 PF 启用引用。它保留 root 拥有的程序副本和配置，因此回滚后可以离线重新开启，不需要保留 Git 仓库所在目录。

> [!NOTE]
> 命令安装在 `/usr/local/bin`。如果已有同名命令或旧版私人脚本，请用 `command -v clash-enable` 确认实际执行的是哪个文件。首次安装也可直接运行 `sudo /bin/sh install.sh`。

## 触发条件

放行需要同时满足：核心进程存在、Unix API 返回 `tun.enable=true`、返回的对应 `utun` 接口存在。

以下情况会加载封锁规则：

- 核心崩溃、被结束、主动退出或尚未启动。
- TUN 关闭，即使旧虚拟接口仍然存在。
- TUN 对应的虚拟接口消失。
- 无法读取进程列表，或本机 Unix API 无法提供可信 TUN 状态。
- 守护进程正常退出；launchd 会尝试重启它。

以下情况本身不会触发：

- 热点或 Wi-Fi 断开、没有网络。
- 网关、IP 地址或网络切换。
- 接口仍在，但 `UP/RUNNING` 标志发生变化。
- 代理节点不可用，但核心、本机 API 和 TUN 仍正常。

如果断网同时导致 TUN 接口消失，仍会封锁。核心与 TUN 恢复后自动解除封锁。

封锁时保留本机回环通信、DHCP 地址获取和受限的 IPv6 本地邻居/路由发现报文，方便网络重新建立。其他 IPv4/IPv6 流量默认丢弃；已有 PF 连接状态也会被清除，避免旧连接继续通过。

## 防护边界

> [!WARNING]
> 这是轮询式防护。默认检查间隔 0.5 秒，本机 API 超时 1 秒，实际响应还受系统调度和 PF 加载耗时影响；不是零延迟、零泄漏保证。被 `SIGKILL` 的监控进程无法执行退出处理，launchd 重启前也可能存在空窗。

项目检测核心、TUN 开关和接口存在性，不验证全部系统流量是否实际进入 TUN，不验证代理节点是否隐藏 IP，也不管理平台账号。它不承诺防止封号。

开启防护会作用于整台 Mac；关闭 Clash 后普通外网访问会被阻止。共享电脑上，防护绑定到安装时指定的桌面用户的 Clash 实例。

安装、恢复 anchor 和回滚会重新加载 PF 主规则集，可能影响其他程序动态添加的 PF 规则。脚本保留主配置中的其他文本条目，回滚时也保留安装后其他管理员新增的配置；原始文件副本保存在本机安装目录。与其他 VPN 或防火墙同时使用时，需要验证双方实际规则。

本机的 Clash YAML、订阅、代理节点和路由规则不会被本项目改写。公开仓库不需要上传这些文件。

## 自定义安装

通过参数选择用户或非默认 Mihomo 安装：

```bash
sudo /bin/sh install.sh \
  --uid "$(id -u)" \
  --core-path "/absolute/path/to/mihomo" \
  --core-uid 0 \
  --api-socket "/absolute/path/to/mihomo.sock"
```

可选参数：

- `--poll-interval 0.5`：两轮检查之间的暂停秒数。
- `--api-timeout 1`：本机 API 请求超时秒数。
- `--uid`：使用 Clash 的桌面用户 UID，必须不是 root。

`killswitch enable` 默认沿用已有配置；传入参数可以更新对应值。配置保存在本机 root 拥有的 `config.json`，不会作为 shell 代码执行。不支持通过外部网络 API 检测 TUN；Unix API 如另需认证，当前版本会判定为不可确认并封锁。

## 验证行为

先暂停需要保护的自动任务，再进行故障验证，避免检测空窗内的请求。

1. 保持 TUN 开启，运行 `killswitch status`，应显示 `mode=running`、`reason=core_and_tun_present`。
2. 关闭 TUN，等待状态变为 `mode=blocked`，原因应为 `tun_disabled` 或 `tun_interface_missing`。
3. 尝试普通外网访问，应失败；重新打开 TUN，状态应恢复为 `running`。
4. 单纯断开热点，如果核心和 TUN 接口仍存在，状态应保持 `running`。

查看规则或日志：

```bash
sudo pfctl -a io.github.valdemar-yu.killswitch -sr
sudo tail -n 50 /var/log/killswitch.log
```

PF 的 `No ALTQ support in kernel` 是队列功能提示，不代表规则加载失败。若网络恢复异常，运行 `killswitch rollback`。

## 开发与验证

```bash
python3 -m unittest discover -s tests -v
```

测试使用临时目录和系统命令替身，覆盖核心身份与退出、TUN 开关、接口丢失、断网标志变化、状态切换、配置校验和 PF 主配置变换，以及安装、回滚、保留其他程序的 PF 引用、删除下载目录后离线重新开启。规则语法使用 macOS 自带 `pfctl -vnf` 检查。测试不安装防火墙，不修改本机 PF，也不终止真实 Clash 进程。GitHub Actions 在 macOS runner 上运行同一套验证。

实现基于 [Mihomo 的运行配置 API](https://wiki.metacubex.one/api/#configs) 和 [TUN 配置](https://wiki.metacubex.one/config/inbound/tun/)，由本机 PF anchor 执行封锁，launchd 管理守护进程。

MIT License。
