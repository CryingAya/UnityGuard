#!/usr/bin/env python3
"""Generate a synthetic Unity project that contains every trap UnityGuard checks.

    python tools/make_sample_project.py [out_dir] [--bad-gitignore] [--locked]

Nothing here is a real Unity import: the files are hand-written YAML with
correct GUID wiring, which is exactly what the auditor reads. That keeps the
fixture small and lets us reproduce bugs that are hard to arrange by hand
(duplicate GUIDs, orphan .meta, NFD filenames, unreachable islands).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
import sys
import unicodedata
import zlib
from pathlib import Path

PROJECT_SETTINGS = "ProjectSettings/ProjectVersion.txt"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def guid_for(rel: str) -> str:
    return hashlib.md5(("unityguard:" + rel).encode("utf-8")).hexdigest()


def write_png(path: Path, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    row = b"\x00" + bytes(rgb) * width
    raw = row * height

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    payload = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
               + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    path.write_bytes(payload)


def write_wav(path: Path, seconds: float, rate: int = 22050, channels: int = 1, bits: int = 16) -> None:
    frames = int(rate * seconds)
    block = bits // 8
    data = b"\x00" * (frames * channels * block)
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * channels * block, channels * block, bits)
    riff_size = 4 + (8 + len(fmt)) + (8 + len(data))
    payload = (b"RIFF" + struct.pack("<I", riff_size) + b"WAVE"
               + b"fmt " + struct.pack("<I", len(fmt)) + fmt
               + b"data" + struct.pack("<I", len(data)) + data)
    path.write_bytes(payload)


def tex_meta(rel: str, *, srgb: int = 1, texture_type: int = 0, max_size: int = 2048,
             readable: int = 0, guid: str | None = None) -> str:
    return f"""fileFormatVersion: 2
guid: {guid or guid_for(rel)}
TextureImporter:
  internalIDToNameTable: []
  externalObjects: {{}}
  serializedVersion: 12
  mipmaps:
    mipMapMode: 0
    enableMipMap: 1
    sRGBTexture: {srgb}
    linearTexture: 0
    fadeOut: 0
    borderMipMap: 0
    mipMapsPreserveCoverage: 0
    alphaTestReferenceValue: 0.5
    mipMapFadeDistanceStart: 1
    mipMapFadeDistanceEnd: 3
  bumpmap:
    convertToNormalMap: 0
    externalNormalMap: 0
    heightScale: 0.25
    normalMapFilter: 0
  isReadable: {readable}
  streamingMipmaps: 0
  streamingMipmapsPriority: 0
  vTOnly: 0
  ignoreMasterTextureLimit: 0
  grayScaleToAlpha: 0
  generateCubemap: 6
  cubemapConvolution: 0
  seamlessCubemap: 0
  textureFormat: 1
  maxTextureSize: {max_size}
  textureSettings:
    serializedVersion: 2
    filterMode: 1
    aniso: 1
    mipBias: 0
    wrapU: 1
    wrapV: 1
    wrapW: 0
  nPOTScale: 1
  lightmap: 0
  compressionQuality: 50
  spriteMode: 1
  spriteExtrude: 1
  spriteMeshType: 1
  alignment: 0
  spritePivot: {{x: 0.5, y: 0.5}}
  spritePixelsToUnits: 100
  spriteBorder: {{x: 0, y: 0, z: 0, w: 0}}
  spriteGenerateFallbackPhysicsShape: 1
  alphaUsage: 1
  alphaIsTransparency: 0
  spriteTessellationDetail: -1
  textureType: {texture_type}
  textureShape: 1
  singleChannelComponent: 0
  flipbookRows: 1
  flipbookColumns: 1
  maxTextureSizeSet: 1
  compressionQualitySet: 0
  textureFormatSet: 0
  ignorePngGamma: 0
  applyGammaDecoding: 0
  swizzle: 50462976
  cookieLightType: 0
  platformSettings: []
  spriteSheet:
    serializedVersion: 2
    sprites: []
    outline: []
    physicsShape: []
    bones: []
    spriteID: 5e97eb03825dee720800000000000000
    internalID: 0
    vertices: []
    indices: 
    edges: []
    weights: []
    secondaryTextures: []
  spritePackingTag: 
  pSDRemoveMatte: 0
  pSDShowRemoveMatteOption: 0
  userData: 
  assetBundleName: 
  assetBundleVariant: 
"""


def audio_meta(rel: str, *, load_type: int = 0, compression: int = 1,
               quality: float = 0.7, force_mono: int = 0) -> str:
    return f"""fileFormatVersion: 2
guid: {guid_for(rel)}
AudioImporter:
  externalObjects: {{}}
  serializedVersion: 7
  defaultSettings:
    serializedVersion: 2
    loadType: {load_type}
    sampleRateSetting: 0
    sampleRateOverride: 44100
    compressionFormat: {compression}
    quality: {quality}
    conversionMode: 0
  platformSettingOverrides: {{}}
  forceToMono: {force_mono}
  normalize: 1
  preloadAudioData: 1
  loadInBackground: 0
  ambisonic: 0
  3D: 1
  userData: 
  assetBundleName: 
  assetBundleVariant: 
"""


def model_meta(rel: str, *, readable: int = 0, import_materials: int = 1) -> str:
    return f"""fileFormatVersion: 2
guid: {guid_for(rel)}
ModelImporter:
  serializedVersion: 22200
  internalIDToNameTable: []
  externalObjects: {{}}
  materials:
    materialImportMode: 1
    materialName: 0
    materialSearch: 1
    materialLocation: 1
  animations:
    legacyGenerateAnimations: 4
    bakeSimulation: 0
    resampleCurves: 1
    optimizeGameObjects: 0
    removeConstantScaleCurves: 0
    motionNodeName: 
    rigImportErrors: 
    rigImportWarnings: 
    animationImportErrors: 
    animationImportWarnings: 
    animationRetargetingWarnings: 
    animationDoRetargetingWarnings: 0
    importAnimatedCustomProperties: 0
    importConstraints: 0
    animationCompression: 1
    animationRotationError: 0.5
    animationPositionError: 0.5
    animationScaleError: 0.5
    animationWrapMode: 0
    extraExposedTransformPaths: []
    extraUserProperties: []
    clipAnimations: []
    isReadable: {readable}
  meshes:
    lODScreenPercentages: []
    globalScale: 1
    meshCompression: 0
    addColliders: 0
    useSRGBMaterialColor: 1
    sortHierarchyByName: 1
    importVisibility: 0
    importBlendShapes: 1
    importCameras: 1
    importLights: 1
    fileIdsGeneration: 2
    swapUVChannels: 0
    generateSecondaryUV: 0
    useFileUnits: 1
    keepQuads: 0
    weldVertices: 1
    bakeAxisConversion: 0
    preserveHierarchy: 0
    skinWeightsMode: 0
    maxBonesPerVertex: 4
    minBoneWeight: 0.001
    optimizeBones: 1
    meshOptimizationFlags: 1
    indexFormat: 0
    secondaryUVAngleDistortion: 8
    secondaryUVAreaDistortion: 15.000001
    secondaryUVHardAngle: 88
    secondaryUVMarginMethod: 1
    secondaryUVMinLightmapResolution: 40
    secondaryUVMinObjectScale: 1
  importMaterials: {import_materials}
  autoGenerateColliders: 0
  userData: 
  assetBundleName: 
  assetBundleVariant: 
"""


def generic_meta(rel: str, importer: str = "NativeFormatImporter") -> str:
    return f"""fileFormatVersion: 2
guid: {guid_for(rel)}
{importer}:
  externalObjects: {{}}
  mainObjectFileID: 2100000
  userData: 
  assetBundleName: 
  assetBundleVariant: 
"""


def script_meta(rel: str) -> str:
    return f"""fileFormatVersion: 2
guid: {guid_for(rel)}
MonoImporter:
  externalObjects: {{}}
  serializedVersion: 2
  defaultReferences: []
  executionOrder: 0
  icon: {{instanceID: 0}}
  userData: 
  assetBundleName: 
  assetBundleVariant: 
"""


def shader_meta(rel: str) -> str:
    return f"""fileFormatVersion: 2
guid: {guid_for(rel)}
ShaderImporter:
  externalObjects: {{}}
  defaultTextures: []
  nonModifiableTextures: []
  preprocessorOverride: 0
  userData: 
  assetBundleName: 
  assetBundleVariant: 
"""


def folder_meta(rel: str) -> str:
    return f"""fileFormatVersion: 2
guid: {guid_for(rel.rstrip('/'))}
folderAsset: yes
DefaultImporter:
  externalObjects: {{}}
  userData: 
  assetBundleName: 
  assetBundleVariant: 
"""


def scene_meta(rel: str) -> str:
    return f"""fileFormatVersion: 2
guid: {guid_for(rel)}
DefaultImporter:
  externalObjects: {{}}
  userData: 
  assetBundleName: 
  assetBundleVariant: 
"""


def mat_body(name: str, shader_guid: str, tex_guid: str, tex_guid2: str | None = None) -> str:
    extra = ""
    if tex_guid2:
        extra = f"""    - _BumpMap:
        m_Texture: {{fileID: 2800000, guid: {tex_guid2}, type: 3}}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
"""
    return f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!21 &2100000
Material:
  serializedVersion: 8
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {name}
  m_Shader: {{fileID: 4800000, guid: {shader_guid}, type: 3}}
  m_Parent: {{fileID: 0}}
  m_ModifiedSerializedProperties: 0
  m_ValidKeywords: []
  m_InvalidKeywords: []
  m_LightmapFlags: 4
  m_EnableInstancingVariants: 0
  m_DoubleSidedGI: 0
  m_CustomRenderQueue: -1
  stringTagMap: {{}}
  disabledShaderPasses: []
  m_LockedProperties: 
  m_SavedProperties:
    serializedVersion: 3
    m_TexEnvs:
    - _MainTex:
        m_Texture: {{fileID: 2800000, guid: {tex_guid}, type: 3}}
        m_Scale: {{x: 1, y: 1}}
        m_Offset: {{x: 0, y: 0}}
{extra}    m_Ints: []
    m_Floats:
    - _Metallic: 0
    - _Smoothness: 0.5
    m_Colors:
    - _Color: {{r: 1, g: 1, b: 1, a: 1}}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", nargs="?", default="sample_project")
    parser.add_argument("--bad-gitignore", action="store_true",
                        help="把 *.meta 写进 .gitignore，用来模拟最经典的事故")
    parser.add_argument("--locked", action="store_true",
                        help="生成 Temp/UnityLockfile，模拟编辑器正在打开工程")
    args = parser.parse_args()

    root = Path(args.out).expanduser().resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    files: dict[str, str] = {}  # rel -> meta content (written at the end)

    # ------------------------------------------------------------------ settings
    (root / "ProjectSettings").mkdir()
    (root / PROJECT_SETTINGS).write_text("m_EditorVersion: 2022.3.20f1\n"
                                         "m_EditorVersionWithRevision: 2022.3.20f1 (0e25a174756c)\n",
                                         encoding="utf-8")
    (root / "Packages").mkdir()
    (root / "Packages" / "manifest.json").write_text(
        '{\n  "dependencies": {\n    "com.unity.ugui": "1.0.0"\n  }\n}\n', encoding="utf-8")
    gitignore = ["[Ll]ibrary/", "[Tt]emp/", "[Oo]bj/", "[Bb]uild/", "Logs/", "*.csproj"]
    if args.bad_gitignore:
        gitignore.append("*.meta")
    (root / ".gitignore").write_text("\n".join(gitignore) + "\n", encoding="utf-8")
    if args.locked:
        (root / "Temp").mkdir()
        (root / "Temp" / "UnityLockfile").write_bytes(b"")

    # ------------------------------------------------------------------ folders
    for folder in ("Art/Textures", "Art/美术/贴图", "Audio", "Audio/sfx", "Materials",
                   "Prefabs", "Scenes", "Scripts", "Resources", "Models", "Shaders",
                   "Dead", "Dup"):
        (root / "Assets" / folder).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ textures
    tex = "Assets/Art/Textures"
    write_png(root / tex / "hero_basecolor.png", 512, 512, (200, 120, 90))
    files[tex + "/hero_basecolor.png"] = tex_meta(tex + "/hero_basecolor.png", srgb=1)

    # 法线图：sRGB 打开 + textureType 仍是 Default —— 两个真实事故叠在一起
    write_png(root / tex / "hero_normal.png", 512, 512, (128, 128, 255))
    files[tex + "/hero_normal.png"] = tex_meta(tex + "/hero_normal.png", srgb=1, texture_type=0)

    # 粗糙度：线性数据被标成 sRGB
    write_png(root / tex / "hero_roughness.png", 512, 512, (90, 90, 90))
    files[tex + "/hero_roughness.png"] = tex_meta(tex + "/hero_roughness.png", srgb=1,
                                                  texture_type=7)

    # 4096 巨图
    write_png(root / tex / "huge_diffuse.png", 4096, 4096, (30, 60, 30))
    files[tex + "/huge_diffuse.png"] = tex_meta(tex + "/huge_diffuse.png", max_size=4096)

    # UI 图标
    write_png(root / tex / "ui_icon_coin.png", 64, 64, (255, 210, 60))
    files[tex + "/ui_icon_coin.png"] = tex_meta(tex + "/ui_icon_coin.png", texture_type=3)

    # 命名噪音
    write_png(root / tex / "Sword final copy (1).png", 256, 256, (180, 180, 190))
    files[tex + "/Sword final copy (1).png"] = tex_meta(tex + "/Sword final copy (1).png")
    write_png(root / tex / "BG_Forest 01.png", 1024, 1024, (40, 80, 50))
    files[tex + "/BG_Forest 01.png"] = tex_meta(tex + "/BG_Forest 01.png")

    # 中文路径 + 空格
    write_png(root / "Assets/Art/美术/贴图/石墙 贴图.png", 256, 256, (120, 120, 120))
    files["Assets/Art/美术/贴图/石墙 贴图.png"] = tex_meta("Assets/Art/美术/贴图/石墙 贴图.png")

    # macOS 来的 NFD 文件名
    nfd_name = unicodedata.normalize("NFD", "café_menu.png")
    write_png(root / tex / nfd_name, 128, 128, (90, 60, 40))
    files[tex + "/" + nfd_name] = tex_meta(tex + "/" + nfd_name)

    # 重复内容
    write_png(root / tex / "dup_a.png", 128, 128, (10, 200, 10))
    files[tex + "/dup_a.png"] = tex_meta(tex + "/dup_a.png")
    shutil.copyfile(root / tex / "dup_a.png", root / tex / "dup_b.png")
    files[tex + "/dup_b.png"] = tex_meta(tex + "/dup_b.png")

    # 缺 .meta 的图
    write_png(root / tex / "no_meta.png", 128, 128, (0, 0, 255))

    # 重复 GUID：两个 meta 用了同一个 guid（复制文件夹时的经典事故）
    write_png(root / tex / "dupguid_a.png", 64, 64, (255, 0, 0))
    write_png(root / tex / "dupguid_b.png", 64, 64, (0, 255, 0))
    shared = guid_for("shared-guid-demo")
    files[tex + "/dupguid_a.png"] = tex_meta(tex + "/dupguid_a.png", guid=shared)
    files[tex + "/dupguid_b.png"] = tex_meta(tex + "/dupguid_b.png", guid=shared)

    # 孤儿 .meta（文件早就删了）
    (root / tex / "deleted_long_ago.png.meta").write_text(
        tex_meta(tex + "/deleted_long_ago.png"), encoding="utf-8")

    # 从未被引用 + 孤岛
    write_png(root / "Assets" / "Dead" / "unused_concept.png", 256, 256, (90, 90, 20))
    files["Dead/unused_concept.png"] = tex_meta("Dead/unused_concept.png")
    write_png(root / "Assets" / "Dead" / "old_hero.png", 256, 256, (20, 90, 90))
    files["Dead/old_hero.png"] = tex_meta("Dead/old_hero.png")

    # ------------------------------------------------------------------ shaders
    (root / "Assets/Shaders/toon.shader").write_text(
        "Shader \"Custom/Toon\"\n{\n    Properties\n    {\n        _MainTex (\"Main\", 2D) = \"white\" {}\n"
        "        _BumpMap (\"Normal\", 2D) = \"bump\" {}\n    }\n    SubShader\n    {\n"
        "        Tags { \"RenderType\"=\"Opaque\" }\n        Pass\n        {\n"
        "            CGPROGRAM\n            #pragma vertex vert\n            #pragma fragment frag\n"
        "            #include \"ToonCommon.cginc\"\n            ENDCG\n        }\n    }\n}\n",
        encoding="utf-8")
    files["Assets/Shaders/toon.shader"] = shader_meta("Assets/Shaders/toon.shader")
    (root / "Assets/Shaders/ToonCommon.cginc").write_text(
        "// shared helpers, pulled in via #include, never referenced by GUID\n"
        "float3 ToonRamp(float3 normal) { return saturate(normal); }\n", encoding="utf-8")
    files["Assets/Shaders/ToonCommon.cginc"] = f"""fileFormatVersion: 2
guid: {guid_for('Assets/Shaders/ToonCommon.cginc')}
ShaderImporter:
  externalObjects: {{}}
  defaultTextures: []
  nonModifiableTextures: []
  preprocessorOverride: 0
  userData: 
  assetBundleName: 
  assetBundleVariant: 
"""

    # ------------------------------------------------------------------ materials
    mat = mat_body("hero", guid_for("Assets/Shaders/toon.shader"),
                   guid_for(tex + "/hero_basecolor.png"), guid_for(tex + "/hero_normal.png"))
    (root / "Assets" / "Materials" / "hero.mat").write_text(mat, encoding="utf-8")
    files["Materials/hero.mat"] = generic_meta("Materials/hero.mat")

    # 孤岛材质：只引用自己也用不到的贴图
    mat_dead = mat_body("old_hero", guid_for("Assets/Shaders/toon.shader"),
                        guid_for("Dead/old_hero.png"))
    (root / "Assets" / "Dead" / "old_hero.mat").write_text(mat_dead, encoding="utf-8")
    files["Dead/old_hero.mat"] = generic_meta("Dead/old_hero.mat")

    # ------------------------------------------------------------------ scripts
    (root / "Assets/Scripts/PlayerController.cs").write_text(
        "using UnityEngine;\n\npublic class PlayerController : MonoBehaviour\n{\n"
        "    public float speed = 5f;\n}\n", encoding="utf-8")
    files["Scripts/PlayerController.cs"] = script_meta("Scripts/PlayerController.cs")

    (root / "Assets/Scripts/UnusedHelper.cs").write_text(
        "using UnityEngine;\n\npublic static class UnusedHelper\n{\n"
        "    public static float Clamp01(float v) => Mathf.Clamp01(v);\n}\n", encoding="utf-8")
    files["Scripts/UnusedHelper.cs"] = script_meta("Scripts/UnusedHelper.cs")

    (root / "Assets/Scripts/Loader.cs").write_text(
        "using UnityEngine;\n\npublic class Loader : MonoBehaviour\n{\n"
        "    void Start()\n    {\n"
        "        var logo = Resources.Load<Texture2D>(\"logo\");\n"
        "        var rim = Shader.Find(\"Custom/RimGlow\");\n"
        "        Debug.Log(logo);\n        Debug.Log(rim);\n    }\n}\n", encoding="utf-8")
    files["Scripts/Loader.cs"] = script_meta("Scripts/Loader.cs")

    # 引用了已删除的 GUID -> 断链引用
    (root / "Assets/Scripts/BrokenLink.cs").write_text(
        "using UnityEngine;\n\npublic class BrokenLink : MonoBehaviour\n{\n"
        "    public AudioClip missing;\n}\n", encoding="utf-8")
    files["Scripts/BrokenLink.cs"] = script_meta("Scripts/BrokenLink.cs")

    # ------------------------------------------------------------------ prefab
    prefab = f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &100100000
GameObject:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  serializedVersion: 6
  m_Component:
  - component: {{fileID: 400000}}
  - component: {{fileID: 11400000}}
  m_Layer: 0
  m_Name: hero
  m_TagString: Untagged
  m_Icon: {{fileID: 0}}
  m_NavMeshLayer: 0
  m_StaticEditorFlags: 0
  m_IsActive: 1
--- !u!4 &400000
Transform:
  m_ObjectHideFlags: 0
  m_GameObject: {{fileID: 100100000}}
  m_LocalRotation: {{x: 0, y: 0, z: 0, w: 1}}
  m_LocalPosition: {{x: 0, y: 0, z: 0}}
  m_LocalScale: {{x: 1, y: 1, z: 1}}
  m_Children: []
  m_Father: {{fileID: 0}}
  m_RootOrder: 0
--- !u!114 &11400000
MonoBehaviour:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_GameObject: {{fileID: 100100000}}
  m_Enabled: 1
  m_EditorHideFlags: 0
  m_Script: {{fileID: 11500000, guid: {guid_for('Scripts/PlayerController.cs')}, type: 3}}
  m_Name: 
  m_EditorClassIdentifier: 
  speed: 5
--- !u!23 &23000000
MeshRenderer:
  m_ObjectHideFlags: 0
  m_GameObject: {{fileID: 100100000}}
  m_Materials:
  - {{fileID: 2100000, guid: {guid_for('Materials/hero.mat')}, type: 2}}
  m_AdditionalVertexStreams: {{fileID: 0}}
"""
    (root / "Assets" / "Prefabs" / "hero.prefab").write_text(prefab, encoding="utf-8")
    files["Prefabs/hero.prefab"] = generic_meta("Prefabs/hero.prefab", "PrefabImporter")

    # ------------------------------------------------------------------ scenes
    scene = f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!29 &1
OcclusionCullingSettings:
  m_ObjectHideFlags: 0
  serializedVersion: 2
  m_OcclusionBakeSettings:
    smallestOccluder: 5
    smallestHole: 0.25
    backfaceThreshold: 100
  m_SceneGUID: 00000000000000000000000000000000
  m_OcclusionCullingData: {{fileID: 0}}
--- !u!104 &2
RenderSettings:
  m_ObjectHideFlags: 0
  serializedVersion: 9
  m_Fog: 0
  m_FogColor: {{r: 0.5, g: 0.5, b: 0.5, a: 1}}
  m_AmbientSkyColor: {{r: 0.212, g: 0.227, b: 0.259, a: 1}}
  m_SkyboxMaterial: {{fileID: 0}}
  m_HaloTexture: {{fileID: 0}}
  m_SpotCookie: {{fileID: 10001, guid: 0000000000000000e000000000000000, type: 0}}
--- !u!1001 &5550000
PrefabInstance:
  m_ObjectHideFlags: 0
  serializedVersion: 2
  m_Modification:
    serializedVersion: 3
    m_TransformParent: {{fileID: 0}}
    m_Modifications: []
    m_RemovedComponents: []
  m_SourcePrefab: {{fileID: 100100000, guid: {guid_for('Prefabs/hero.prefab')}, type: 3}}
--- !u!1 &200000
GameObject:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  serializedVersion: 6
  m_Component:
  - component: {{fileID: 400002}}
  - component: {{fileID: 3300000}}
  - component: {{fileID: 2300001}}
  - component: {{fileID: 8200000}}
  - component: {{fileID: 8200001}}
  - component: {{fileID: 8200002}}
  m_Layer: 0
  m_Name: crate_audio_rig
  m_TagString: Untagged
  m_Icon: {{fileID: 0}}
  m_NavMeshLayer: 0
  m_StaticEditorFlags: 0
  m_IsActive: 1
--- !u!4 &400002
Transform:
  m_ObjectHideFlags: 0
  m_GameObject: {{fileID: 200000}}
  m_LocalRotation: {{x: 0, y: 0, z: 0, w: 1}}
  m_LocalPosition: {{x: 3, y: 0, z: 0}}
  m_LocalScale: {{x: 1, y: 1, z: 1}}
  m_Children: []
  m_Father: {{fileID: 0}}
  m_RootOrder: 1
--- !u!33 &3300000
MeshFilter:
  m_ObjectHideFlags: 0
  m_GameObject: {{fileID: 200000}}
  m_Mesh: {{fileID: 4300000, guid: {guid_for('Models/prop_crate.fbx')}, type: 3}}
--- !u!23 &2300001
MeshRenderer:
  m_ObjectHideFlags: 0
  m_GameObject: {{fileID: 200000}}
  m_Enabled: 1
  m_CastShadows: 1
  m_ReceiveShadows: 1
  m_Materials:
  - {{fileID: 2100000, guid: {guid_for('Materials/hero.mat')}, type: 2}}
  - {{fileID: 2100000, guid: 11112222333344445555666677778888, type: 2}}
  m_AdditionalVertexStreams: {{fileID: 0}}
--- !u!82 &8200000
AudioSource:
  m_ObjectHideFlags: 0
  m_GameObject: {{fileID: 200000}}
  m_Enabled: 1
  serializedVersion: 4
  OutputAudioMixerGroup: {{fileID: 0}}
  m_audioClip: {{fileID: 8300000, guid: {guid_for('Audio/bgm_theme.wav')}, type: 3}}
  m_PlayOnAwake: 1
  m_Volume: 1
  m_Loop: 1
--- !u!82 &8200001
AudioSource:
  m_ObjectHideFlags: 0
  m_GameObject: {{fileID: 200000}}
  m_Enabled: 1
  serializedVersion: 4
  OutputAudioMixerGroup: {{fileID: 0}}
  m_audioClip: {{fileID: 8300000, guid: {guid_for('Audio/sfx/sfx_step.wav')}, type: 3}}
  m_PlayOnAwake: 0
  m_Volume: 0.8
--- !u!82 &8200002
AudioSource:
  m_ObjectHideFlags: 0
  m_GameObject: {{fileID: 200000}}
  m_Enabled: 1
  serializedVersion: 4
  OutputAudioMixerGroup: {{fileID: 0}}
  m_audioClip: {{fileID: 8300000, guid: {guid_for('Audio/sfx/界面点击音.wav')}, type: 3}}
  m_PlayOnAwake: 0
  m_Volume: 0.6
"""
    (root / "Assets" / "Scenes" / "Main.unity").write_text(scene, encoding="utf-8")
    files["Scenes/Main.unity"] = scene_meta("Scenes/Main.unity")

    # 没进 Build Settings 的测试场景：不可达
    unused_scene = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!29 &1
OcclusionCullingSettings:
  m_ObjectHideFlags: 0
  serializedVersion: 2
  m_OcclusionBakeSettings:
    smallestOccluder: 5
    smallestHole: 0.25
    backfaceThreshold: 100
  m_SceneGUID: 00000000000000000000000000000000
  m_OcclusionCullingData: {fileID: 0}
--- !u!1 &900000
GameObject:
  m_ObjectHideFlags: 0
  serializedVersion: 6
  m_Component:
  - component: {fileID: 900001}
  m_Layer: 0
  m_Name: scratch_scene_keep_or_delete
  m_IsActive: 1
--- !u!4 &900001
Transform:
  m_GameObject: {fileID: 900000}
  m_LocalRotation: {x: 0, y: 0, z: 0, w: 1}
  m_LocalPosition: {x: 0, y: 0, z: 0}
  m_LocalScale: {x: 1, y: 1, z: 1}
"""
    (root / "Assets" / "Scenes" / "UnusedTest.unity").write_text(unused_scene, encoding="utf-8")
    files["Scenes/UnusedTest.unity"] = scene_meta("Scenes/UnusedTest.unity")

    # ------------------------------------------------------------------ audio
    write_wav(root / "Assets/Audio/bgm_theme.wav", 12.0)
    files["Audio/bgm_theme.wav"] = audio_meta("Audio/bgm_theme.wav", load_type=0, compression=0)

    write_wav(root / "Assets/Audio/sfx/sfx_step.wav", 0.3)
    files["Audio/sfx/sfx_step.wav"] = audio_meta("Audio/sfx/sfx_step.wav",
                                                 load_type=2, compression=1)

    write_wav(root / "Assets/Audio/sfx/界面点击音.wav", 0.2)
    files["Audio/sfx/界面点击音.wav"] = audio_meta("Audio/sfx/界面点击音.wav",
                                                   load_type=0, compression=1)

    # ------------------------------------------------------------------ models
    (root / "Assets/Models/prop_crate.fbx").write_bytes(
        b";; synthetic fbx placeholder for audit fixtures\n")
    files["Models/prop_crate.fbx"] = model_meta("Models/prop_crate.fbx", readable=1)

    # ------------------------------------------------------------------ resources
    write_png(root / "Assets/Resources/logo.png", 128, 128, (240, 240, 240))
    files["Resources/logo.png"] = tex_meta("Resources/logo.png", texture_type=3)

    # Unity 忽略点开头和 ~ 结尾的名字，它们永远不会有 .meta —— 不能报缺失。
    (root / "Assets/Art/.DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (root / "Assets/Art/Hidden~").mkdir(exist_ok=True)
    (root / "Assets/Art/Hidden~/notes.txt").write_text("ignored by unity\n", encoding="utf-8")
    (root / "Assets/Art/Samples~").mkdir(exist_ok=True)
    (root / "Assets/Art/Samples~/.sample.json").write_text('{"displayName": "Demo"}\n',
                                                           encoding="utf-8")

    # 只通过 Shader.Find 用名字引用的 shader（文件名 ≠ 声明名）
    (root / "Assets/Shaders/RimLight.shader").write_text(
        'Shader "Custom/RimGlow"\n{\n    SubShader\n    {\n        Pass\n        {\n'
        "            CGPROGRAM\n            #pragma vertex vert\n            ENDCG\n"
        "        }\n    }\n}\n", encoding="utf-8")
    files["Shaders/RimLight.shader"] = shader_meta("Shaders/RimLight.shader")

    # ------------------------------------------------------------------ settings refs
    (root / "ProjectSettings/EditorBuildSettings.asset").write_text(
        f"""%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1045 &1
EditorBuildSettings:
  m_ObjectHideFlags: 0
  serializedVersion: 2
  m_Scenes:
  - enabled: 1
    path: Assets/Scenes/Main.unity
    guid: {guid_for('Scenes/Main.unity')}
  m_configObjects: {{}}
""", encoding="utf-8")

    # ------------------------------------------------------------------ write metas
    for rel, content in files.items():
        rel_inside_assets = rel[len("Assets/"):] if rel.startswith("Assets/") else rel
        (root / "Assets" / (rel_inside_assets + ".meta")).write_text(content, encoding="utf-8")

    # folder metas
    for folder in ("Art", "Art/Textures", "Art/美术", "Art/美术/贴图", "Audio", "Audio/sfx",
                   "Materials", "Prefabs", "Scenes", "Scripts", "Resources", "Models",
                   "Shaders", "Dead", "Dup"):
        (root / "Assets" / (folder + ".meta")).write_text(folder_meta("Assets/" + folder),
                                                          encoding="utf-8")

    # 超过 240 字符的深路径：按当前根目录长度反推目录段，落在 241..252 之间，
    # 这样既会触发 240 警告，又不会撞上 Windows 260 硬上限（连 .meta 一起）。
    probe = str(root / "Assets" / "Deep" / "deep_texture.png")
    plan = None
    for count in range(6, 14):
        for extra in range(0, 24):
            name_len = 18 + extra
            total = len(probe) + count * (name_len + 1)
            if 241 <= total <= 252:
                plan = (count, name_len, total)
                break
        if plan:
            break

    if plan is None:
        print(f"跳过深路径样本：根目录过长（{len(probe)} 字符）", file=sys.stderr)
    else:
        count, name_len, total = plan
        deep_parts = ["Assets", "Deep"] + [
            ("%02d_" % index) + "p" * (name_len - 3) for index in range(count)
        ]
        deep_dir = root.joinpath(*deep_parts)
        try:
            deep_dir.mkdir(parents=True, exist_ok=True)
            deep_file = deep_dir / "deep_texture.png"
            write_png(deep_file, 32, 32, (1, 2, 3))
            rel = "/".join(deep_parts[1:] + ["deep_texture.png"])
            (deep_dir / "deep_texture.png.meta").write_text(tex_meta(rel), encoding="utf-8")
            assert len(str(deep_file)) == total
        except OSError as exc:  # pragma: no cover - depends on the host path length
            print(f"跳过深路径样本：{exc}", file=sys.stderr)

    total = sum(1 for _ in root.rglob("*"))
    print(f"已生成样例工程：{root}")
    print(f"条目数：{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
