"""Platform + naming hygiene.

Every check here maps to a real support ticket: the `.meta` pair rename is easy,
but "two files differ only by case", "the pack came from macOS with NFD names",
or "the path is 268 characters" is what actually breaks builds.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict

from .i18n import tr
from .model import Asset, Issue
from .project import ProjectInfo

_INVALID_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_NOISE_RE = re.compile(
    r"(\bfinal\b|\bnew\b|\bold\b|\bcopy\b|\bbackup\b|\bbak\b|\(\d+\)|副本|最终|拷贝|未命名|untitled)",
    re.IGNORECASE,
)
_VERSION_TAIL_RE = re.compile(r"(_v?\d{1,3})$", re.IGNORECASE)
_MAX_PATH_WARN = 240
_MAX_PATH_HARD = 258


def _classify_style(stem: str) -> str:
    if " " in stem:
        return "spaced"
    if "-" in stem:
        return "kebab-case"
    if stem.lower() == stem:
        return "snake_case" if "_" in stem else "lowercase"
    if "_" in stem:
        return "Mixed_Snake"
    if stem[:1].isupper():
        return "PascalCase"
    if stem[:1].islower() and any(ch.isupper() for ch in stem[1:]):
        return "camelCase"
    return "other"


def check_paths(info: ProjectInfo, assets: list[Asset]) -> list[Issue]:
    issues: list[Issue] = []
    root_len = len(str(info.root)) + 1

    case_map: dict[str, list[str]] = defaultdict(list)
    bad_chars: list[str] = []
    reserved: list[str] = []
    trailing: list[str] = []
    long_warn: list[str] = []
    long_hard: list[str] = []
    non_nfc: list[str] = []
    wide_chars: list[str] = []
    double_ext: list[str] = []

    for asset in assets:
        rel = asset.rel
        case_map[rel.lower()].append(rel)
        name = asset.path.name
        if _INVALID_CHARS.search(name):
            bad_chars.append(rel)
        stem_wo_ext = name.rsplit(".", 1)[0] if "." in name else name
        if stem_wo_ext.upper() in _RESERVED:
            reserved.append(rel)
        if name.endswith((" ", ".")):
            trailing.append(rel)
        full_len = root_len + len(rel.replace("/", "\\"))
        if full_len > _MAX_PATH_HARD:
            long_hard.append(f"{rel} ({full_len})")
        elif full_len > _MAX_PATH_WARN:
            long_warn.append(f"{rel} ({full_len})")
        normalized = unicodedata.normalize("NFC", name)
        if normalized != name:
            non_nfc.append(rel)
        if any(ord(ch) > 0x2E80 for ch in name):
            wide_chars.append(rel)
        parts = name.split(".")
        if len(parts) > 2 and parts[-1].lower() == parts[-2].lower():
            double_ext.append(rel)

    collisions = {key: rels for key, rels in case_map.items() if len(rels) > 1}
    if collisions:
        sample = sorted({rel for rels in collisions.values() for rel in rels})[:40]
        issues.append(Issue(
            severity="critical",
            code="path.case_collision",
            title=tr(f"{len(collisions)} 组路径仅大小写不同", f"{len(collisions)} path pairs differ only by case"),
            detail="Windows / macOS 默认大小写不敏感，git 拉取或打包时这些文件会互相覆盖，"
                   "Unity 也只能识别其中一个。",
            paths=sample,
            hint="改名时保留一个，另一个加后缀区分（如 _a / _b）。",
        ))
    if bad_chars:
        issues.append(Issue("critical", "path.invalid_chars",
                            tr(f"{len(bad_chars)} 个文件名含 Windows 非法字符", f"{len(bad_chars)} file names contain Windows-illegal characters"), paths=bad_chars[:40],
                            hint="Unity 能建出来，但迁移到 Windows / 打 zip / 上 git 会失败。"))
    if reserved:
        issues.append(Issue("critical", "path.reserved_name",
                            tr(f"{len(reserved)} 个文件名是系统保留名", f"{len(reserved)} file names are Windows reserved names"), paths=reserved[:40],
                            detail="CON / PRN / AUX / NUL / COM1-9 / LPT1-9 在 Windows 上无法正常读写。"))
    if trailing:
        issues.append(Issue("warn", "path.trailing_dot_space",
                            tr(f"{len(trailing)} 个文件名以空格或点结尾", f"{len(trailing)} file names end with a space or dot"), paths=trailing[:40]))
    if long_hard:
        issues.append(Issue("critical", "path.too_long",
                            tr(f"{len(long_hard)} 个路径超过 Windows 260 字符上限", f"{len(long_hard)} paths exceed the 260-character Windows limit"), paths=long_hard[:20],
                            hint="移动前先把工程挪到更短的根目录，否则移动会直接失败。"))
    if long_warn:
        issues.append(Issue("warn", "path.long",
                            tr(f"{len(long_warn)} 个路径接近 260 字符上限", f"{len(long_warn)} paths are close to the 260-character limit"), paths=long_warn[:20]))
    if non_nfc:
        issues.append(Issue("warn", "path.nfd",
                            tr(f"{len(non_nfc)} 个文件名不是 NFC 规范形式（疑似 macOS 来源）", f"{len(non_nfc)} file names are not NFC-normalized (likely from macOS)"),
                            paths=non_nfc[:20],
                            detail="同一串字符在 macOS(NFD) 与 Windows(NFC) 下字节不同，"
                                   "会导致「文件明明存在却找不到」和重复资源。"))
    if double_ext:
        issues.append(Issue("info", "path.double_extension",
                            tr(f"{len(double_ext)} 个文件名有重复扩展名", f"{len(double_ext)} file names have a doubled extension"), paths=double_ext[:20],
                            hint="例如 Sword.png.png —— Unity 按最后一个扩展名识别。"))
    if wide_chars:
        issues.append(Issue("info", "path.non_ascii",
                            tr(f"{len(wide_chars)} 个文件名含中日韩/全角字符", f"{len(wide_chars)} file names contain CJK / full-width characters"), paths=wide_chars[:20],
                            detail="Unity 本身支持，但部分构建链、AssetBundle 名、"
                                   "CI 脚本和第三方工具按 ASCII 处理会出错。"))
    return issues


def check_naming(assets: list[Asset]) -> list[Issue]:
    issues: list[Issue] = []
    styles = Counter()
    noisy: list[str] = []
    versioned: list[str] = []
    spaced: list[str] = []

    for asset in assets:
        if asset.category in ("script", "data"):
            continue
        stem = asset.stem
        styles[_classify_style(stem)] += 1
        if _NOISE_RE.search(stem):
            noisy.append(asset.rel)
        elif _VERSION_TAIL_RE.search(stem):
            versioned.append(asset.rel)
        if " " in stem:
            spaced.append(asset.rel)

    if noisy:
        issues.append(Issue("warn", "name.version_noise",
                            tr(f"{len(noisy)} 个文件名带「最终版 / 副本 / (1)」类噪音", f"{len(noisy)} file names carry version noise (final / copy / (1))"), paths=noisy[:40],
                            hint="这类名字会在迭代中无限增殖，建议用内容或用途命名。"))
    if versioned:
        issues.append(Issue("info", "name.version_suffix",
                            tr(f"{len(versioned)} 个文件名带 _v1 / _02 版本后缀", f"{len(versioned)} file names carry a _v1 / _02 style suffix"), paths=versioned[:30],
                            hint="版本号应交给 git，不要写进文件名。"))
    if len(styles) >= 2:
        top = styles.most_common(3)
        issues.append(Issue(
            "info", "name.inconsistent_style",
            tr(f"命名风格混用：{' / '.join(f'{style} {count}个' for style, count in top)}",
               "Mixed naming styles: " + " / ".join(f"{style} x{count}" for style, count in top)),
            detail=tr(f"共识别出 {len(styles)} 种风格。风格本身不影响运行，但会显著拖慢检索与协作。",
                         f"{len(styles)} naming styles in use. Harmless at runtime, but it slows "
                         "down searching and collaboration a lot."),
            hint="建议全项目统一 snake_case（大小写不敏感平台最安全），并在预览里批量转换。",
        ))
    if spaced:
        issues.append(Issue("info", "name.spaces",
                            tr(f"{len(spaced)} 个文件名含空格", f"{len(spaced)} file names contain spaces"), paths=spaced[:30],
                            detail="Unity 可以处理，但命令行工具、shell 脚本、CI 里极易出错。"))
    return issues


def _sha256(path, limit: int = 64 * 1024 * 1024) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            read = 0
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                read += len(chunk)
                if read > limit:
                    return None
    except OSError:
        return None
    return digest.hexdigest()


def _human_bytes(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def find_duplicate_content(assets: list[Asset]) -> dict[str, list[str]]:
    """Hash only size-collision groups, so a big project stays fast."""
    by_size: dict[int, list[Asset]] = defaultdict(list)
    for asset in assets:
        if asset.size > 0:
            by_size[asset.size].append(asset)
    duplicates: dict[str, list[str]] = {}
    for group in by_size.values():
        if len(group) < 2:
            continue
        by_hash: dict[str, list[str]] = defaultdict(list)
        for asset in group:
            digest = _sha256(asset.path)
            if digest:
                by_hash[digest].append(asset.rel)
        for digest, rels in by_hash.items():
            if len(rels) > 1:
                duplicates[digest] = sorted(rels)
    return duplicates


def check_duplicates(assets: list[Asset]) -> list[Issue]:
    duplicates = find_duplicate_content(assets)
    if not duplicates:
        return []
    wasted = 0
    paths: list[str] = []
    by_size = {asset.rel: asset.size for asset in assets}
    for rels in duplicates.values():
        keep = rels[0]
        wasted += by_size.get(keep, 0) * (len(rels) - 1)
        paths.extend(rels)
    return [Issue(
        "warn", "dup.content",
        tr(f"{len(duplicates)} 组内容完全相同的文件（重复占用 {_human_bytes(wasted)}）", f"{len(duplicates)} groups of byte-identical files ({_human_bytes(wasted)} wasted)"),
        detail="同一张贴图/音频被打包多次会同时增加包体与内存占用，"
               "而且修改其中一份不会同步到其他份。",
        paths=sorted(paths)[:40],
    )]
