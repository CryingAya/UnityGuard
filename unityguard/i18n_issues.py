"""English copy for audit findings.

The engine writes findings in Chinese; this table supplies the English text.
Keys are the exact Chinese strings, so a missing entry simply falls back to the
Chinese sentence instead of showing a broken placeholder. Keep both sides in
sync when adding a new finding - `tests/test_i18n.py` fails if a finding has no
English text.
"""

from __future__ import annotations

from .i18n import is_english

_PAIRS: list[tuple[str, str]] = [
    ("Addressables 的「地址键（address）」是字符串，改名不会自动同步地址键，代码里按地址加载的地方会静默失败。建议改名后统一检查 Addressable Groups。",
     "Addressables 'address' keys are plain strings: renaming does not update them, so code that loads by address fails silently. Review the Addressable Groups after a rename."),
    ("CON / PRN / AUX / NUL / COM1-9 / LPT1-9 在 Windows 上无法正常读写。",
     "CON / PRN / AUX / NUL / COM1-9 / LPT1-9 cannot be read or written normally on Windows."),
    ("PCM 是 WAV 原始格式，等于把整段波形原样塞进包里；背景音乐用 Vorbis 通常能压到 1/10 且听感差异很小。",
     "PCM is the raw WAV format - the whole waveform goes into the build. Vorbis usually gets it down to about 1/10 with almost no audible difference."),
    ("Streaming 每次播放都要走磁盘 IO，短音效（脚步、UI 点击）会有明显延迟。",
     "Streaming hits the disk on every play, which is audible for footsteps and UI clicks."),
    ("Unity 5.x 默认用二进制保存场景与预制体（.unity/.prefab 里是序列化数据，不是 YAML），外部工具无法可靠解析其中的 GUID 引用。这个工程里「哪些资源没被用到」的判断会全部失真，所以本报告不出「未使用」结论，只做 .meta / 命名 / 导入设置 / 重复文件检查。",
     "Unity 5.x saves scenes and prefabs as binary blobs (not YAML), and external tools cannot reliably read the GUID references inside them. Any 'unused asset' conclusion would be wrong here, so this report skips that analysis and only checks .meta / naming / import settings / duplicate files."),
    ("Unity 会重写这些 .meta（分配新 GUID），等于引用全部失效。",
     "Unity rewrites these .meta files with a fresh GUID, which invalidates every reference."),
    ("Unity 可以处理，但命令行工具、shell 脚本、CI 里极易出错。",
     "Unity itself is fine with spaces, but command line tools, shell scripts and CI break easily."),
    ("Unity 本身支持，但部分构建链、AssetBundle 名、CI 脚本和第三方工具按 ASCII 处理会出错。",
     "Unity supports them, but some build pipelines, AssetBundle names, CI scripts and third-party tools assume ASCII."),
    ("Unity 能建出来，但迁移到 Windows / 打 zip / 上 git 会失败。",
     "Unity can create these, but moving to Windows, zipping, or committing to git will fail."),
    ("Unity 遇到重复 GUID 时取哪一个是不确定的（通常取先导入的那个），引用的对象会随机变成另一份资源。多半是复制粘贴文件夹时连 .meta 一起复制造成的。",
     "When two .meta files share a GUID, Unity picks one non-deterministically (usually the first imported) and references silently point at the wrong asset. Typically caused by copying a folder together with its .meta files."),
    ("Windows / macOS 默认大小写不敏感，git 拉取或打包时这些文件会互相覆盖，Unity 也只能识别其中一个。",
     "Windows and macOS are case-insensitive, so these paths overwrite each other on checkout or when zipping, and Unity only sees one of them."),
    ("不等于死代码：[InitializeOnLoad]、[MenuItem]、[CustomEditor] 这类特性会在编辑器启动时自动加载，不需要被引用。Editor 目录下的脚本已自动排除。",
     "Not necessarily dead code: [InitializeOnLoad], [MenuItem] and [CustomEditor] classes are loaded automatically and need no reference. Scripts under Editor/ are already excluded."),
    ("从构建场景 / Resources / ProjectSettings 出发都走不到，也没有任何资源指向它。大概率是废弃素材，删掉可以直接减小包体和导入时间。",
     "Unreachable from the build scenes, Resources, ProjectSettings, and nothing points at it. Most likely leftover art; removing it shrinks the build and speeds up imports."),
    ("会在内存里保留一份 CPU 可读副本，贴图量一大内存直接翻倍。只有需要 GetPixels / 运行时读写时才该打开。",
     "Keeps a CPU-readable copy in memory, so memory roughly doubles once you have many textures. Only needed for GetPixels or runtime read/write."),
    ("例如 Resources.Load / AssetDatabase.LoadAssetAtPath / Addressables 地址。改名后这些加载会静默失败（返回 null 而不报错），必须人工核对。",
     "For example Resources.Load / AssetDatabase.LoadAssetAtPath / Addressables addresses. After a rename these fail silently (null, no error), so they must be checked by hand."),
    ("例如 Sword.png.png —— Unity 按最后一个扩展名识别。",
     "e.g. Sword.png.png - Unity goes by the last extension."),
    ("先确认没有通过代码字符串或 Addressables 远程加载，再移入回收目录。",
     "Confirm nothing loads it via a code string or Addressables, then move it to the quarantine folder."),
    ("关闭 Unity 后重新体检，再执行变更。",
     "Close Unity, run the audit again, then apply the changes."),
    ("内嵌材质会随 FBX 生成到 .fbm 目录，难以统一改参数、难以复用。",
     "Embedded materials are generated into a .fbm folder next to the FBX, which makes them hard to tune centrally and impossible to reuse."),
    ("删除多余的 .meta，让 Unity 重新生成 GUID，然后再重新指向引用。",
     "Delete the redundant .meta files, let Unity assign new GUIDs, then re-link the references."),
    ("只有 textureType=NormalMap 时 Unity 才会做 DXT5nm 压缩与解包；按 Default 导入会让法线方向整体错乱。",
     "Unity only applies DXT5nm compression and unpacking when textureType is NormalMap. Imported as Default, the normal directions come out wrong."),
    ("只读体检不受影响。但任何改名/移动都必须在编辑器关闭后执行，否则 Unity 会看到「旧文件消失 + 新文件出现」，把它当成两个不同的资源。",
     "The read-only audit is unaffected. But any rename/move must happen with the editor closed: Unity would otherwise see 'old file deleted + new file created' and treat them as two assets."),
    ("同一串字符在 macOS(NFD) 与 Windows(NFC) 下字节不同，会导致「文件明明存在却找不到」和重复资源。",
     "The same characters have different bytes under macOS (NFD) and Windows (NFC), which produces 'the file exists but cannot be found' and duplicated assets."),
    ("同一张贴图/音频被打包多次会同时增加包体与内存占用，而且修改其中一份不会同步到其他份。",
     "Shipping the same texture/audio several times inflates both build size and memory, and editing one copy does not update the others."),
    ("如果这些资源本来就在工程里，先确认它们是否曾被版本库忽略；如果是新拷进来的，导入前先补齐 .meta。",
     "If these assets were already in the project, check whether version control ignored them; if they were just copied in, restore the .meta files before importing."),
    ("它们之间可能互相引用，形成一个孤岛（例如一套已经废弃的材质+贴图）。单看引用计数会以为它们在用，所以必须做可达性分析才能发现。",
     "They may reference each other, forming an island (for example an abandoned material + its textures). Counting references alone makes them look used, so reachability analysis is needed."),
    ("对应资源已被删除但 .meta 留在原地。数量多了会污染版本库，也可能在你重建同名文件时「复活」一个旧 GUID。",
     "The asset was deleted but its .meta stayed behind. In quantity this pollutes version control, and it can 'revive' an old GUID if you recreate a same-named file."),
    ("建议全项目统一 snake_case（大小写不敏感平台最安全），并在预览里批量转换。",
     "Pick one convention across the project (snake_case is safest on case-insensitive platforms) and convert in bulk from the preview."),
    ("把 Edit > Project Settings > Editor > Asset Serialization 改成 Force Text，重新保存场景后再体检，引用分析才可用。",
     "Set Edit > Project Settings > Editor > Asset Serialization to Force Text, re-save the scenes, then run the audit again - reference analysis only works on text assets."),
    ("把 TextureImporter 的 sRGB 关掉（或用 Data/Linear 预设）。",
     "Turn sRGB off in the TextureImporter (or apply a Data/Linear preset)."),
    ("改名时保留一个，另一个加后缀区分（如 _a / _b）。",
     "Keep one of them and add a suffix to the other (e.g. _a / _b)."),
    ("整段解码进内存（PCM 尺寸），BGM 应该用 CompressedInMemory 或 Streaming。",
     "The whole clip is decoded into memory at PCM size. BGM should use CompressedInMemory or Streaming."),
    ("模型网格会在内存中保留一份 CPU 副本，网格多的项目很吃内存。",
     "Mesh data keeps a CPU copy in memory; projects with many meshes feel this immediately."),
    ("法线/粗糙度/金属度/AO/Mask 属于线性数据。勾选 sRGB 会在采样时做一次 gamma 解码，结果就是「光照看起来脏、法线方向发虚」，而且很多人查很久都找不到原因。",
     "Normal / roughness / metallic / AO / mask maps are linear data. Marking them sRGB applies a gamma decode at sample time: lighting looks dirty and normals look soft, and it is very hard to track down."),
    ("版本号应交给 git，不要写进文件名。",
     "Version numbers belong in git, not in file names."),
    ("目录也有 .meta（存目录 GUID）。重命名文件夹时，`Folder.meta` 必须跟着一起改，否则目录引用会断。",
     "Folders have a .meta too (holding the folder GUID). When renaming a folder, `Folder.meta` must be renamed with it or folder references break."),
    ("移动前先把工程挪到更短的根目录，否则移动会直接失败。",
     "Move the project to a shorter root first, otherwise the move fails outright."),
    ("移动端/低配机上 4096 贴图会显著拉高包体与显存峰值。",
     "4096 textures noticeably raise build size and VRAM peaks on mobile and low-end hardware."),
    ("立刻从 .gitignore 移除 *.meta，并把现有 .meta 全部提交进版本库。",
     "Remove *.meta from .gitignore now, and commit all existing .meta files."),
    ("缺 .meta 的资源会被 Unity 当成「新资源」重新分配 GUID，原来指向它的场景/预制体引用会全部断开。",
     "An asset without .meta is treated as new and gets a fresh GUID, so every scene/prefab reference to it breaks."),
    ("跨大版本升级（如 2021 → 6）后，导入设置与序列化格式可能被重写，建议升级前先做一次基线快照。",
     "After a major upgrade (2021 -> 6) import settings and serialization can be rewritten; take a baseline snapshot before upgrading."),
    ("这些就是 Inspector 里红色的 Missing (Object)。常见原因：删资源没删引用、.meta 丢失导致 GUID 变化、资源包合并时覆盖。",
     "These are the red 'Missing (Object)' fields in the Inspector. Usual causes: asset deleted without removing the reference, .meta lost so the GUID changed, or an asset pack overwrote files."),
    ("这是 Unity 项目最经典的灾难：.meta 存着 GUID，丢了 .meta 就等于资源换了身份证，所有场景/预制体的引用全部变成 Missing。",
     "The classic Unity disaster: .meta files hold the GUIDs, so losing them gives every asset a new identity and all scene/prefab references turn into Missing."),
    ("这类名字会在迭代中无限增殖，建议用内容或用途命名。",
     "Names like this multiply every iteration; name by content or purpose instead."),
]

EN: dict[str, str] = {zh: en for zh, en in _PAIRS}


def localize(text: str) -> str:
    """Translate a finding's detail/hint when the UI is in English."""
    if not text or not is_english():
        return text
    return EN.get(text, text)
