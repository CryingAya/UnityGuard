# UnityGuard · Unity 素材体检与安全整理

两个能力，一条安全线：

- **体检（audit）**：只读扫描，把「引用会断 / 包体会胖 / 光照会错」的问题找出来。**永远免费。**
- **执行（plan → apply → rollback）**：按你确认过的计划改名/移动**文件或整个文件夹**，同步 `.meta`，写日志，可**字节级回滚**。
- **修复（plan --rule fix）**：把体检发现的导入设置问题一键改掉（法线图 sRGB、NormalMap 类型、Read/Write、长音频 PCM、短音效 Streaming）。只改 `.meta`，不动资源本身。

> **这一版是免费完整版**：体检、生成计划、执行、回滚、去重、修改导入设置，全部功能不限量。
> 先把工具用起来、攒真实用户；付费版（团队批量、编辑器插件）之后再谈。

## 下载

- **最新版**：[Releases](https://github.com/你的用户名/UnityGuard/releases/latest) → 下载 `UnityGuard-0.6.0-win64.zip`
- 下载慢的话：把压缩包转存到 123 云盘 / 蓝奏云，把直链贴在下面（国内建议这么做）

解压后约 40 MB，Windows 10/11 64 位，**不需要安装 Python**。

发布包里有 `SHA256SUMS.txt`，可以自己核对（`Get-FileHash .\UnityGuard.exe -Algorithm SHA256`）。

---

## 用 exe（推荐，不需要装 Python）

```
UnityGuard.exe                              # 双击：显示用法
UnityGuard.exe "D:\MyGame"                  # 体检（把工程文件夹拖到 exe 上）
UnityGuard.exe plan "D:\MyGame" --rule category
UnityGuard.exe plan "D:\MyGame" --rule fix          # 一键修导入设置
UnityGuard.exe apply --plan 计划.json --dry-run
UnityGuard.exe apply --plan 计划.json --yes
UnityGuard.exe rollback "D:\MyGame" --last
```

`dist\UnityGuard.exe` 7.3 MB 单文件，**目标机器不需要 Python**。

> 自己构建：`python tools/build_exe.py`（需要 `pip install pyinstaller`）。
> 构建脚本末尾会自动做冒烟测试。

## 用源码（开发/调试）

```powershell
python unity_asset_audit.py "D:\MyGame"                      # 体检（免费，只读）
python unity_asset_audit.py plan "D:\MyGame" --rule category  # 生成整理计划（免费）
python unity_asset_audit.py plan "D:\MyGame" --rule fix       # 生成修复计划（免费）
python unity_asset_audit.py apply --plan plan_xxx.json --dry-run
python unity_asset_audit.py apply --plan plan_xxx.json --yes
python unity_asset_audit.py rollback "D:\MyGame" --last       # 回滚（免费）
```

只需 Python 3.9+，**零第三方依赖**（打包用的 PyInstaller 只在开发机需要）。

---

## 为什么这个工具敢动你的文件

Unity 的引用是 **GUID**，GUID 存在 `.meta` 里。所以真正的危险不是"改错名字"，而是**主文件动了、`.meta` 没跟上**，Unity 就把它当成新资源、重发一张身份证，场景里的引用全变 Missing。

执行引擎的每一条设计都对着这个风险：

| 机制 | 它防的是什么 |
| --- | --- |
| 计划即唯一输入 | `apply` 只认 `plan.json`，不认临时参数；你审过的就是会执行的 |
| 全量前置校验 | 先把所有源文件的 size+mtime+SHA256 复核一遍，任何一处不符就**一个文件都不动** |
| `.meta` 成对事务 | 移动主文件后立刻移动同名 `.meta`，缺一个就整条操作回滚 |
| **文件夹整树搬移** | 目录连同 `Folder.meta` 一起走，内部所有资源与它们的 `.meta` 原样保留；新父目录自动补齐 `.meta` |
| **整树哈希校验** | 文件夹操作用「路径+大小+内容哈希」的树哈希校验，回滚前发现里面被改过就拒绝 |
| GUID 断言 | 每移一次都重新读 `.meta` 的 guid，和移动前比对，不一致立即中止 |
| 写前日志（WAL） | 日志先落盘再动手；崩溃/断电后仍能按日志撤销已做的部分 |
| 幂等 | 同一份计划重复执行 = 0 个新操作；崩溃后直接重跑也能接着走 |
| 绝不覆盖 | 目标已存在（含**仅大小写不同**）直接报错，不做静默替换 |
| **互换/环/链自动排序** | `a↔b` 会自动插入临时名（`.ugswap-01`）分两步走；链条 `b→d, a→b` 自动反序执行；半途中断会拒绝重跑并要求先回滚 |
| Unity 锁检测 | 检测到 `Temp/UnityLockfile` 就拒绝执行 —— 编辑器开着时改名，Unity 会当成两个资源 |
| 路径边界 | 只允许 `Assets/` 内的文件与文件夹；`..`、绝对路径、移进自己的子目录、系统保留名一律拒绝 |
| 字符串路径告警 | 移出 `Resources/` 会破坏 `Resources.Load`，计划里明确警告（自动整理规则直接跳过它们） |

回滚不是"再移回去"这么简单：它倒序遍历日志、校验目标文件哈希没被你改过、清理这次新建的空目录，最后整棵树回到**路径与内容哈希完全一致**的状态。

---

## 一键修导入设置（`plan --rule fix`）

体检发现的导入问题，直接变成可执行的修复计划。**只改 `.meta` 里对应的那几行**，guid、缩进、行尾（CRLF/LF）、其余所有键值一个字节都不动，改前自动备份。

| 体检发现 | 修复动作 | 为什么值钱 |
| --- | --- | --- |
| 法线图被勾了 sRGB | `sRGBTexture: 1 → 0` | 光照发脏、法线方向发虚，很多人查一整天 |
| 法线图 `textureType` 不是 NormalMap | `textureType: x → 1` | 不做 DXT5nm 压缩与解包，法线整体错乱 |
| 粗糙度/金属度/AO/Mask 勾了 sRGB | `sRGBTexture: 1 → 0` | 线性数据被当颜色采样 |
| 贴图 Read/Write | `isReadable: 1 → 0` | 内存里常驻 CPU 副本，贴图一多内存翻倍 |
| 长音频用 PCM | `compressionFormat: 0 → 1`（Vorbis） | 包体，BGM 通常能压到 1/10 |
| 长音频 DecompressOnLoad | `loadType: 0 → 1` | 整段解码进内存 |
| 短音效 Streaming | `loadType: 2 → 0` | 每次播放走磁盘，脚步/UI 音有延迟 |
| 模型 Read/Write | `isReadable: 1 → 0` | 网格的 CPU 副本 |
| 贴图超过 2048（可选） | `maxTextureSize → N` | 需要显式加 `--cap-texture-size 2048`，默认不动画质 |

```powershell
UnityGuard.exe plan "D:\MyGame" --rule fix
UnityGuard.exe apply --plan <计划> --dry-run
UnityGuard.exe apply --plan <计划> --yes
```

注意：`.meta` 改完要**重新打开 Unity**（或让它重新导入）才会生效 —— 工具会检测 `Temp/UnityLockfile`，编辑器开着时直接拒绝执行。

---

## 快速开始

```powershell
# 1) 体检（免费，只读，输出 md/html/json）
python unity_asset_audit.py "D:\MyGame" --out .\report

# 2) 手动改名 / 移动（旧=新，可重复；路径相对 Assets 或写全路径都行）
python unity_asset_audit.py plan "D:\MyGame" `
    --rename "Art/sword final.png=Art/Textures/sword_01.png" `
    --rename "Audio/step.wav=Audio/SFX/footstep_01.wav"

# 3) 按类型自动归档到 Assets/Organized/<category>/（大工程这才是卖点）
python unity_asset_audit.py plan "D:\MyGame" --rule category

# 4) 生成修复计划（法线图 sRGB、Read/Write、音频压缩……）
python unity_asset_audit.py plan "D:\MyGame" --rule fix
python unity_asset_audit.py plan "D:\MyGame" --rule fix --cap-texture-size 2048

# 5) 预览 → 执行 → 需要时回滚
python unity_asset_audit.py apply --plan "D:\MyGame\.unityguard\plans\plan_xxx.json" --dry-run
python unity_asset_audit.py apply --plan "D:\MyGame\.unityguard\plans\plan_xxx.json" --yes
python unity_asset_audit.py rollback "D:\MyGame" --last
```

计划和日志都放在工程的 `.unityguard/`（点开头，Unity 不会导入）：

```
MyGame/.unityguard/plans/plan_20261124_120000.json
MyGame/.unityguard/journals/rollback_20261124_120500.json
```

`apply` 必须带 `--yes` 才会写盘；只带 `--plan` 会拒绝并提示先 `--dry-run`。

---

## 免费完整版

| | 本版 |
| --- | --- |
| 体检 audit | 不限 |
| 生成计划 plan（category / fix / dedupe） | 不限 |
| 执行 apply（含文件夹、互换、去重隔离） | **不限** |
| 回滚 rollback | 不限 |
| 中英双语（`--lang en`） | 支持 |
| 授权 | 不需要 |

v0.6 之前有一层「单次最多 25 个操作」的限制，已经去掉：**现在没有任何功能上限**。
`licensing.py` 里保留了一个 `FREE_EDITION` 开关，将来要出付费版时改一个常量就能恢复闸门，
引擎代码不用动。

```powershell
python unity_asset_audit.py license --show     # 会显示「免费完整版（全部功能）」
```

---

## 去重（`plan --rule dedupe`）

体检里已经知道哪些文件内容完全相同、哪些没有被引用。去重就是把这个结论变成动作：
**把没人引用的重复副本移到 `Assets/_Duplicates~/`**。

这个目录名以 `~` 结尾，**Unity 完全不导入它** —— 等于从工程里消失，但文件还在磁盘上，
你确认没用之后自己删掉整个目录即可。之所以不直接删：删了就没法反悔，而"移走"可以回滚。

规则：

* 只处理**内容完全相同**（SHA-256 相同）的文件；
* **有一份还被引用，就保留那一份**，其余隔离；
* 一份都没被引用，保留路径最短的那个；
* `Resources/`、`StreamingAssets/`、脚本、着色器、`.dll` 一律不动（它们是按名字/字符串路径加载的）；
* 每个文件连同 `.meta` 一起移动，所以 GUID 跟着走，不会凭空多出断链。

```powershell
UnityGuard.exe plan "D:\MyGame" --rule dedupe
UnityGuard.exe apply --plan <计划> --dry-run
UnityGuard.exe apply --plan <计划> --yes
```

---

## 中英双语

```powershell
UnityGuard.exe "D:\MyGame" --lang en
set UNITYGUARD_LANG=en        # 或者用环境变量，对双击启动也生效
```

报告（md / html / json）的界面、章节、表格、问题标题都会跟着语言走；
没指定时按系统语言判断（英文系统输出英文，其余默认中文）。

> 已全覆盖：界面、报告结构、问题标题、**每条问题的详细解释与建议**都是双语的
> （44 条解释由 `unityguard/i18n_issues.py` 提供，`tests/test_i18n.py` 会在缺翻译时失败）。

---

## 可行性验证（不是嘴上说安全）

```powershell
python tools/feasibility_check.py
```

它在**一次性副本**上做六组实验，共 61 项断言：

1. **合成工程**：同目录改名 / 跨目录移动 / 仅改大小写 —— 每条都验证 `.meta` 跟随、`guid` 不变、体检结论无新增断链、重复执行幂等、回滚后整棵树字节级复原、新建目录被清理；
2. **模拟崩溃**：在第 2 个操作后中止 —— 验证日志能撤回到初始状态；
3. **真实工程**（Unity 官方 2d-extras）：改一张被 `Dungeon.asset` 引用的贴图 —— 验证 GUID 不变、引用方仍指向同一 GUID、体检的断链数不增加、回滚复原；
4. **真实工程整目录搬移**：把整个 `DungeonRuleTile/` 连同目录 `.meta` 挪到 `Assets/Packs/` —— 验证目录 GUID 不变、内部资源 GUID 全部不变、断链数不增加、回滚后整棵树字节级复原；
5. **一键修复**：体检 → `plan --rule fix` → 执行 → 六类导入问题全部消失、所有 GUID 不变、健康分上升 → 回滚后字节级复原；
6. **名称互换**：`a↔b` 自动拆成「临时名 → 目标名」，内容互换、GUID 跟着文件走、无残留临时文件、断链不增加、可回滚。

当前结果：**61 / 61 通过**（去重另有 8 个专项测试）。

---

## 体检都查什么

### GUID 与 `.meta`
缺失 `.meta`（Unity 会重发 GUID）/ 重复 GUID（引用随机指向另一份）/ 孤儿 `.meta` / `.meta` 无合法 guid / `.gitignore` 忽略 `*.meta`（直接判严重）/ 目录级 `.meta`。

### 引用可达性
从 Build Settings 场景、`Resources/`、`ProjectSettings/*.asset`、Addressables 数据出发做 BFS，分类给出：
**从未被引用** / **只被同样用不到的资源引用（孤岛）** / **只通过代码字符串加载**。
同时报告**断链引用**（Inspector 里的红色 Missing），内置资源 GUID 已排除。

Unity 5.x 的**二进制**场景无法被外部可靠解析：检测到就**停用**该判断并告警，而不是把每个资源都报成"没用"。

### 导入设置
法线/粗糙度/金属度/AO 被标 sRGB、法线图 `textureType` 不是 NormalMap、贴图 Read/Write、非 UI 贴图 > 2048、长音频 PCM/DecompressOnLoad、短音效 Streaming、FBX Read/Write 与内嵌材质。

### 平台与命名
非法字符、`CON/PRN/AUX/NUL/COM1-9/LPT1-9`、名字以空格或点结尾、仅大小写不同的冲突、NFC/NFD（macOS 来源）、260 字符上限、重复扩展名、命名风格混用、"最终版/副本/(1)" 噪音。

### 重复文件
按大小分组后再算 SHA-256，只报告不删除。

---

## 真实工程验证

| 语料 | 来源 | 序列化 | 结果 |
| --- | --- | --- | --- |
| Pathfinding 9 个工程 | Sebastian Lague（Unity 5.3–5.5） | 二进制 | 9/9 正确停用引用分析；`Episode 6` 报出**真实的 7 个资源缺 `.meta`**（作者那一集漏提交了） |
| 2d-extras | Unity 官方（2019.4） | YAML | 48 个资源、真实 GUID 图；4 个 tile 判「未使用」、3 张被它们引用的贴图判「孤岛」——语义正确 |

这批真实数据抓出并修掉了 2 个致命误报（二进制工程全量误报「未使用」、`.`/`~` 文件误报「缺 `.meta`」），
以及 1 个执行期隐患（`Resources.Load` 的字符串路径会被移动破坏）。

---

## 测试

```powershell
python -m unittest discover -s tests -v
```

88 个测试：体检（24）+ 执行引擎（37）+ 导入设置修复（15）+ 去重（8）+ 双语覆盖（4）。

---

## 代码结构

```
unity_asset_audit.py        # 入口（薄壳）
unityguard/
  project.py                # 工程识别、Assets 遍历、分类表
  metainfo.py               # .meta 解析（guid + 导入器键值）
  refs.py                   # 引用图、入口 BFS、代码字符串引用
  checks.py                 # 工程/元数据/引用类检查
  hygiene.py                # 路径与命名、重复内容
  media.py                  # 图片/音频头探测 + 导入设置交叉校验
  report.py                 # Markdown / HTML / JSON（含健康分）
  plan.py                   # 计划模型 + 全量前置校验
  apply.py                  # WAL、成对移动、GUID 断言、回滚
  metaedit.py               # 一键修复导入设置（.meta 原地改写）
  licensing.py              # 免费/Pro 分级与离线授权码
  gui.py                    # Tkinter 图形界面（和 CLI 共用同一个引擎）
  cli.py                    # audit / plan / apply / rollback / license
tools/make_sample_project.py    # 合成 Unity 工程（埋 20+ 种坑）
tools/feasibility_check.py      # 执行引擎可行性验证
tools/build_exe.py              # 打包桌面 UnityGuard\（GUI + CLI，目录版）
tests/                          # 88 个测试
```

---

## 已知边界

- 引用分析基于**文本 YAML**；二进制序列化的工程会被检测并停用该判断。
- Addressables 只把 settings/groups 当入口，不解析「地址键」字符串是否被代码引用。
- 不解析 FBX/PSD 内部结构，只看头部与 `.meta`。
- 命名风格与贴图用途是**启发式**，报告里一律标「疑似」。
- 文件夹操作暂不支持「跨盘符」；名称互换（a↔b）需要拆两步执行，工具会拦下来。
- 授权码是离线验签，**不是 DRM**；想认真防破解就得联网校验，那就违背"本地工具"的定位了。
- `fixtures/` 下的第三方工程只用于本地验证，**不要提交进仓库**（各自许可证不同）。

---

## 为什么不做「通用改名器」

查过 Unity Asset Store：同类插件已经有人在卖，
[Batch Renamer Pro & Organize](https://assetstore.unity.com/packages/tools/utilities/batch-renamer-pro-organize-324358) $4.99、
[BT-Smart Rename Pro](https://assetstore.unity.com/packages/tools/utilities/bt-smart-rename-pro-advanced-project-organizer-354728) $15，
两边收藏数都只有个位数；通用工具这边 [Eagle](https://tooltrim.com/en/tool/eagle/pricing) $34.95 买断、Advanced Renamer ~$30、PowerToys 的 PowerRename 免费。

结论：**「改名」不值钱，「改完引用不断、包体变小、出错能回滚」才值钱。**
所以免费版负责证明这件事（体检 + 预览 + 回滚），付费版卖"一次处理完整个包"。

## 路线图

- [x] v0.1 只读体检
- [x] v0.2 plan / apply / rollback + 免费/Pro 分级
- [x] v0.3 文件夹整树搬移（含目录 `.meta`）+ 单文件 exe
- [x] v0.4 导入预设一键修正（sRGB / NormalMap / Read-Write / 音频压缩）
- [x] v0.5 名称互换 / 环 / 链自动排序（必要时插入临时名）
- [x] v0.6 免费完整版（去掉额度限制）+ 内容去重 + 中英双语界面
- [ ] v0.7 英文详细说明补完 + 购买页 / 授权服务器
- [ ] v0.7 hash 去重（移到 Unity 忽略的 `_Duplicates~`）+ Unity 编辑器插件形态（`AssetDatabase.MoveAsset`）+ Addressables 地址键同步
