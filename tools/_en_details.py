"""English copy for the audit findings, in the same order as the extraction."""

EN = [
    # 00 addressables
    "Addressables 'address' keys are plain strings: renaming does not update them, "
    "so code that loads by address fails silently. Review the Addressable Groups after a rename.",
    # 01 reserved names
    "CON / PRN / AUX / NUL / COM1-9 / LPT1-9 cannot be read or written normally on Windows.",
    # 02 pcm
    "PCM is the raw WAV format - the whole waveform goes into the build. Vorbis usually gets it "
    "down to about 1/10 with almost no audible difference.",
    # 03 streaming sfx
    "Streaming hits the disk on every play, which is audible for footsteps and UI clicks.",
    # 04 binary serialization
    "Unity 5.x saves scenes and prefabs as binary blobs (not YAML), and external tools cannot "
    "reliably read the GUID references inside them. Any 'unused asset' conclusion would be wrong "
    "here, so this report skips that analysis and only checks .meta / naming / import settings / "
    "duplicate files.",
    # 05 no guid
    "Unity rewrites these .meta files with a fresh GUID, which invalidates every reference.",
    # 06 spaces
    "Unity itself is fine with spaces, but command line tools, shell scripts and CI break easily.",
    # 07 non ascii
    "Unity supports them, but some build pipelines, AssetBundle names, CI scripts and third-party "
    "tools assume ASCII.",
    # 08 invalid chars
    "Unity can create these, but moving to Windows, zipping, or committing to git will fail.",
    # 09 duplicate guid
    "When two .meta files share a GUID, Unity picks one non-deterministically (usually the first "
    "imported) and references silently point at the wrong asset. Typically caused by copying a "
    "folder together with its .meta files.",
    # 10 case collision
    "Windows and macOS are case-insensitive, so these paths overwrite each other on checkout or "
    "when zipping, and Unity only sees one of them.",
    # 11 dead scripts
    "Not necessarily dead code: [InitializeOnLoad], [MenuItem] and [CustomEditor] classes are "
    "loaded automatically and need no reference. Scripts under Editor/ are already excluded.",
    # 12 never referenced
    "Unreachable from the build scenes, Resources, ProjectSettings, and nothing points at it. "
    "Most likely leftover art; removing it shrinks the build and speeds up imports.",
    # 13 readable
    "Keeps a CPU-readable copy in memory, so memory roughly doubles once you have many textures. "
    "Only needed for GetPixels or runtime read/write.",
    # 14 code strings
    "For example Resources.Load / AssetDatabase.LoadAssetAtPath / Addressables addresses. After a "
    "rename these fail silently (null, no error), so they must be checked by hand.",
    # 15 double extension
    "e.g. Sword.png.png - Unity goes by the last extension.",
    # 16 never referenced hint
    "Confirm nothing loads it via a code string or Addressables, then move it to the quarantine folder.",
    # 17 unity open hint
    "Close Unity, run the audit again, then apply the changes.",
    # 18 embedded materials
    "Embedded materials are generated into a .fbm folder next to the FBX, which makes them hard to "
    "tune centrally and impossible to reuse.",
    # 19 duplicate guid hint
    "Delete the redundant .meta files, let Unity assign new GUIDs, then re-link the references.",
    # 20 normal type
    "Unity only applies DXT5nm compression and unpacking when textureType is NormalMap. Imported as "
    "Default, the normal directions come out wrong.",
    # 21 unity open
    "The read-only audit is unaffected. But any rename/move must happen with the editor closed: "
    "Unity would otherwise see 'old file deleted + new file created' and treat them as two assets.",
    # 22 nfd
    "The same characters have different bytes under macOS (NFD) and Windows (NFC), which produces "
    "'the file exists but cannot be found' and duplicated assets.",
    # 23 duplicate content
    "Shipping the same texture/audio several times inflates both build size and memory, and editing "
    "one copy does not update the others.",
    # 24 missing meta hint
    "If these assets were already in the project, check whether version control ignored them; if "
    "they were just copied in, restore the .meta files before importing.",
    # 25 unreachable
    "They may reference each other, forming an island (for example an abandoned material + its "
    "textures). Counting references alone makes them look used, so reachability analysis is needed.",
    # 26 orphan
    "The asset was deleted but its .meta stayed behind. In quantity this pollutes version control, "
    "and it can 'revive' an old GUID if you recreate a same-named file.",
    # 27 style hint
    "Pick one convention across the project (snake_case is safest on case-insensitive platforms) "
    "and convert in bulk from the preview.",
    # 28 binary hint
    "Set Edit > Project Settings > Editor > Asset Serialization to Force Text, re-save the scenes, "
    "then run the audit again - reference analysis only works on text assets.",
    # 29 srgb hint
    "Turn sRGB off in the TextureImporter (or apply a Data/Linear preset).",
    # 30 case hint
    "Keep one of them and add a suffix to the other (e.g. _a / _b).",
    # 31 decompress hint
    "The whole clip is decoded into memory at PCM size. BGM should use CompressedInMemory or Streaming.",
    # 32 model readable
    "Mesh data keeps a CPU copy in memory; projects with many meshes feel this immediately.",
    # 33 srgb linear
    "Normal / roughness / metallic / AO / mask maps are linear data. Marking them sRGB applies a "
    "gamma decode at sample time: lighting looks dirty and normals look soft, and it is very hard "
    "to track down.",
    # 34 version suffix
    "Version numbers belong in git, not in file names.",
    # 35 folder meta
    "Folders have a .meta too (holding the folder GUID). When renaming a folder, `Folder.meta` must "
    "be renamed with it or folder references break.",
    # 36 long path
    "Move the project to a shorter root first, otherwise the move fails outright.",
    # 37 oversized
    "4096 textures noticeably raise build size and VRAM peaks on mobile and low-end hardware.",
    # 38 gitignore meta
    "Remove *.meta from .gitignore now, and commit all existing .meta files.",
    # 39 missing meta
    "An asset without .meta is treated as new and gets a fresh GUID, so every scene/prefab "
    "reference to it breaks.",
    # 40 version upgrade
    "After a major upgrade (2021 -> 6) import settings and serialization can be rewritten; take a "
    "baseline snapshot before upgrading.",
    # 41 dangling
    "These are the red 'Missing (Object)' fields in the Inspector. Usual causes: asset deleted "
    "without removing the reference, .meta lost so the GUID changed, or an asset pack overwrote files.",
    # 42 meta ignored
    "The classic Unity disaster: .meta files hold the GUIDs, so losing them gives every asset a new "
    "identity and all scene/prefab references turn into Missing.",
    # 43 version noise
    "Names like this multiply every iteration; name by content or purpose instead.",
]
