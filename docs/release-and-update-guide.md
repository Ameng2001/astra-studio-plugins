# 发布与更新说明

本文档说明 `astra-studio-plugins` 的发布、首次安装和版本升级流程。

## 基本原则

- `claude plugin install` 只用于首次安装。
- `claude plugin marketplace update` + `claude plugin update` 用于升级已安装插件。
- 远端仓库如果没有 bump `version`，即使刷新 marketplace，本地也可能识别不到新版本。
- marketplace 清单只应声明真实存在且可安装的插件目录。
- 插件 manifest 只写标准字段。`plugin.json` 里出现非标字段（例如把 `agents` 写成目录字符串、或保留 studio 草稿期的 `traits`/`runtime_workspace`/`governance`）会导致安装时 manifest 验证失败，整个插件装不上。

## Astra Studio Plugins

### 仓库

- GitHub: [Ameng2001/astra-studio-plugins](https://github.com/Ameng2001/astra-studio-plugins)
- Marketplace 名称: `astra-studio`

### 发布方检查项

发布前确认以下文件的版本号已经同步更新：

- [marketplace.json](../.claude-plugin/marketplace.json)
- [根级 plugin.json](../.claude-plugin/plugin.json)

工具链插件（8 个）：

- [studio-core](../studio-core/.claude-plugin/plugin.json)
- [studio-insight](../studio-insight/.claude-plugin/plugin.json)
- [studio-planner](../studio-planner/.claude-plugin/plugin.json)
- [studio-quality](../studio-quality/.claude-plugin/plugin.json)
- [studio-docs](../studio-docs/.claude-plugin/plugin.json)
- [studio-platform](../studio-platform/.claude-plugin/plugin.json)
- [studio-design](../studio-design/.claude-plugin/plugin.json)
- [studio-ontology](../studio-ontology/.claude-plugin/plugin.json)

垂直插件（1 个）：

- [fund-review](../fund-review/.claude-plugin/plugin.json)

当前工作流语义升级版本为 `0.2.1`。

### 首次安装

```bash
claude plugin marketplace add github:Ameng2001/astra-studio-plugins

# 工具链核心四件套
claude plugin install studio-core@astra-studio
claude plugin install studio-insight@astra-studio
claude plugin install studio-planner@astra-studio
claude plugin install studio-quality@astra-studio

# 按需加装
claude plugin install studio-docs@astra-studio
claude plugin install studio-platform@astra-studio
claude plugin install studio-design@astra-studio
claude plugin install studio-ontology@astra-studio
claude plugin install fund-review@astra-studio
```

### 升级已安装插件

```bash
claude plugin marketplace update astra-studio
claude plugin update studio-core@astra-studio
claude plugin update studio-insight@astra-studio
claude plugin update studio-planner@astra-studio
claude plugin update studio-quality@astra-studio
# 其余已安装的插件同理
```

### 升级失败时的排查顺序

1. 确认使用的是 `update`，不是再次执行 `install`。
2. 先刷新 marketplace 缓存，再更新插件。
3. 确认远端 `plugin.json` 与 `marketplace.json` 的版本号已经 bump。
4. 若报 manifest 验证错误（例如「`agents` 字段格式无效」），是该插件 `plugin.json` 写了非标字段，不是 CLI 版本问题——修 manifest 后重新发布。

如本地 CLI 对 `update` 支持不稳定，可使用强制安装兜底：

```bash
claude plugin install --force studio-core@astra-studio
```

## 一句话总结

- 首次安装用 `install`
- 升级用 `marketplace update` + `plugin update`
- 发布前必须 bump 版本
- marketplace 只登记真实存在的插件
- manifest 只写标准字段
