// Optional editor helper shipped with Unity export packages. It runs only from the menu.
#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEngine;

namespace TrellisStudio
{
    public static class PackageImporter
    {
        [Serializable] public class Maps { public string BaseColor, MetallicSmoothness, NormalOpenGL, Occlusion, Emissive; }
        [Serializable] public class Surface { public string name, source_name, alpha_mode; public bool double_sided; public float[] base_color, emissive_factor; public float normal_scale=1; public Maps maps; }
        [Serializable] public class DetailLevel { public int level; public string export_path; }
        [Serializable] public class DetailSet { public DetailLevel[] levels; public float[] screen_heights; }
        [Serializable] public class PhysicsProxy { public string kind; }
        [Serializable] public class Package
        {
            public string target, name, id, unity_asset_id, unity_parent_id, motion_root;
            public int bones;
            public Surface[] materials;
            public DetailSet lods;
            public PhysicsProxy physics;
            public string StableId => string.IsNullOrEmpty(unity_asset_id) ? id : unity_asset_id;
        }

        const string ManifestName = "studio-export.json";
        const string ConfiguredMarker = ".trellis-unity-configured";
        static readonly Regex CardId = new Regex(@"\A[a-f0-9]{32}\z");
        static readonly Regex DetailId = new Regex(@"\A\.lod-[1-9][0-9]*\z");
        static readonly Regex MaterialSlot = new Regex(@"\ATS_Mat_[0-9]{3,}\z");
        static readonly Regex TexturePath = new Regex(@"\ATextures/TS_Mat_[0-9]{3,}_[A-Za-z]+\.png\z");
        const ImportAssetOptions Reimport = ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport;

        static void PreparedPrefab(string root,Package package)
        {
            if (File.Exists(ProjectFile(root + "/StudioAsset.prefab"))) return;
            bool hasLevels=package.lods!=null&&package.lods.levels!=null&&package.lods.levels.Length>0;
            if(!hasLevels&&(package.physics==null||string.IsNullOrEmpty(package.physics.kind)||package.physics.kind=="none"))return;
            var model=AssetDatabase.LoadAssetAtPath<GameObject>(root+"/model.fbx");if(model==null)return;
            var asset=new GameObject("StudioAsset");
            try {
                var first=(GameObject)PrefabUtility.InstantiatePrefab(model);first.name="LOD0";first.transform.SetParent(asset.transform,false);
                if(hasLevels) {
                    var group=asset.AddComponent<LODGroup>();var levels=new System.Collections.Generic.List<LOD>();
                    var skeleton=first.GetComponentsInChildren<Transform>().GroupBy(t=>t.name).ToDictionary(g=>g.Key,g=>g.First());
                    foreach(var level in package.lods.levels) {
                        GameObject instance=first;
                        if(level.level>0) {
                            if (level.export_path != "LODs/LOD" + level.level) continue;
                            var source=AssetDatabase.LoadAssetAtPath<GameObject>(root+"/"+level.export_path+"/model.fbx");if(source==null)continue;
                            instance=(GameObject)PrefabUtility.InstantiatePrefab(source);instance.name="LOD"+level.level;instance.transform.SetParent(asset.transform,false);
                            foreach(var skin in instance.GetComponentsInChildren<SkinnedMeshRenderer>()) {
                                if(skin.bones.All(b=>b!=null&&skeleton.ContainsKey(b.name)))skin.bones=skin.bones.Select(b=>skeleton[b.name]).ToArray();
                                if(skin.rootBone!=null&&skeleton.ContainsKey(skin.rootBone.name))skin.rootBone=skeleton[skin.rootBone.name];
                            }
                            foreach(var animator in instance.GetComponentsInChildren<Animator>())UnityEngine.Object.DestroyImmediate(animator);
                        }
                        float threshold=package.lods.screen_heights!=null&&level.level<package.lods.screen_heights.Length?package.lods.screen_heights[level.level]:Mathf.Max(.01f,.6f/(level.level+1));
                        levels.Add(new LOD(threshold,instance.GetComponentsInChildren<Renderer>()));
                    }
                    group.SetLODs(levels.ToArray());group.RecalculateBounds();
                }
                if(package.physics!=null&&package.physics.kind=="box") {
                    var renderers=first.GetComponentsInChildren<Renderer>();if(renderers.Length>0){var bounds=renderers[0].bounds;foreach(var r in renderers)bounds.Encapsulate(r.bounds);var collider=asset.AddComponent<BoxCollider>();collider.center=asset.transform.InverseTransformPoint(bounds.center);collider.size=bounds.size;}
                }
                if(package.physics!=null&&package.physics.kind=="convex") {
                    var hull=AssetDatabase.LoadAssetAtPath<GameObject>(root+"/Collisions/collision.fbx");
                    if(hull!=null){var collision=(GameObject)PrefabUtility.InstantiatePrefab(hull);collision.name="Collision";collision.transform.SetParent(asset.transform,false);foreach(var filter in collision.GetComponentsInChildren<MeshFilter>()){var collider=filter.gameObject.AddComponent<MeshCollider>();collider.sharedMesh=filter.sharedMesh;collider.convex=true;}foreach(var r in collision.GetComponentsInChildren<Renderer>())r.enabled=false;}
                }
                PrefabUtility.SaveAsPrefabAsset(asset,root+"/StudioAsset.prefab");
            } finally {UnityEngine.Object.DestroyImmediate(asset);}
        }

        static Texture2D Texture(string root, string relative, bool srgb, bool normal=false)
        {
            if (string.IsNullOrEmpty(relative)) return null;
            if (!TexturePath.IsMatch(relative)) throw new InvalidDataException("Invalid texture path: " + relative);
            string path=root+"/"+relative;
            AssetDatabase.ImportAsset(path,ImportAssetOptions.ForceUpdate);
            var importer=AssetImporter.GetAtPath(path) as TextureImporter;
            if (importer!=null) { importer.textureType=normal?TextureImporterType.NormalMap:TextureImporterType.Default; importer.sRGBTexture=srgb; importer.SaveAndReimport(); }
            return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
        }
        static Color Colour(float[] value, Color fallback) { return value!=null&&value.Length>=3?new Color(value[0],value[1],value[2],value.Length>3?value[3]:1):fallback; }

        [MenuItem("Tools/TRELLIS Studio/Configure exported models")]
        public static void Configure()
        {
            int count = 0;
            // Configure detail meshes before constructing a wrapper that instances them.
            foreach (var root in InstalledRoots(includeDetails: true).OrderByDescending(path => path.Length))
            {
                var package = ReadPackage(ProjectFile(root + "/" + ManifestName), allowDetail: true);
                var importer = AssetImporter.GetAtPath(root + "/model.fbx") as ModelImporter;
                if (importer==null) continue;
                var existing = importer.GetExternalObjectMap();
                var surfaces = package.materials ?? Array.Empty<Surface>();
                var marker = ProjectFile(root + "/" + ConfiguredMarker);
                bool configured = File.Exists(marker) || existing.Count > 0 ||
                    File.Exists(ProjectFile(root + "/StudioAsset.prefab")) ||
                    surfaces.Any(surface => MaterialSlot.IsMatch(surface?.name ?? "") &&
                        File.Exists(ProjectFile(root + "/" + surface.name + ".mat")));
                bool changed = false;
                foreach (var surface in surfaces)
                {
                    if (!MaterialSlot.IsMatch(surface?.name ?? "")) throw new InvalidDataException("Invalid Studio material slot.");
                    var identifier = new AssetImporter.SourceAssetIdentifier(typeof(Material), surface.name);
                    if (existing.TryGetValue(identifier, out var overrideAsset) && overrideAsset != null) continue;
                    string path = root + "/" + surface.name + ".mat";
                    var material = AssetDatabase.LoadAssetAtPath<Material>(path) ?? CreateMaterial(root, path, surface);
                    importer.AddRemap(identifier, material);
                    changed = true;
                }
                if (!configured)
                {
                    importer.useFileScale=true;importer.globalScale=1;importer.bakeAxisConversion=true;importer.preserveHierarchy=true;importer.optimizeGameObjects=false;importer.importCameras=false;importer.importLights=false;
                    importer.importAnimation=package.bones>0;
                    importer.animationCompression=ModelImporterAnimationCompression.Off;
                    importer.animationType=package.bones>0?ModelImporterAnimationType.Generic:ModelImporterAnimationType.None;
                    importer.avatarSetup=package.bones>0?ModelImporterAvatarSetup.CreateFromThisModel:ModelImporterAvatarSetup.NoAvatar;
                    if(!string.IsNullOrEmpty(package.motion_root))importer.motionNodeName=package.motion_root;
                    changed = true;
                }
                if (changed) { importer.SaveAndReimport(); count++; }
                PreparedPrefab(root, package);
                if (!File.Exists(marker)) File.WriteAllText(marker, package.StableId);
            }
            AssetDatabase.SaveAssets();Debug.Log("TRELLIS Studio: configured "+count+" exported model(s). No scene was modified.");
        }

        static Material CreateMaterial(string root, string path, Surface surface)
        {
            Shader shader=Shader.Find("Universal Render Pipeline/Lit")??Shader.Find("Standard");
            if (shader==null) throw new InvalidOperationException("Install URP or use a project with the Standard shader.");
            var material=new Material(shader);
            var maps=surface.maps??new Maps();
            var colour=Colour(surface.base_color,Color.white);
            if (material.HasProperty("_BaseColor")) material.SetColor("_BaseColor",colour);
            if (material.HasProperty("_Color")) material.SetColor("_Color",colour);
            material.SetTexture(material.HasProperty("_BaseMap")?"_BaseMap":"_MainTex",Texture(root,maps.BaseColor,true));
            material.SetTexture("_MetallicGlossMap",Texture(root,maps.MetallicSmoothness,false));
            material.EnableKeyword("_METALLICGLOSSMAP");material.EnableKeyword("_METALLICSPECGLOSSMAP");
            foreach (string property in new[]{"_Smoothness","_GlossMapScale","_Glossiness"}) if (material.HasProperty(property)) material.SetFloat(property,1);
            var normal=Texture(root,maps.NormalOpenGL,false,true);
            if (normal!=null) { material.SetTexture("_BumpMap",normal);material.SetFloat("_BumpScale",surface.normal_scale);material.EnableKeyword("_NORMALMAP"); }
            material.SetTexture("_OcclusionMap",Texture(root,maps.Occlusion,false));
            material.SetTexture("_EmissionMap",Texture(root,maps.Emissive,true));
            var emission=Colour(surface.emissive_factor,Color.black);material.SetColor("_EmissionColor",emission);
            if (emission.maxColorComponent>0) material.EnableKeyword("_EMISSION");
            if (surface.alpha_mode=="MASK") { material.SetFloat("_AlphaClip",1);material.SetFloat("_Cutoff",.5f);material.EnableKeyword("_ALPHATEST_ON"); }
            if (surface.alpha_mode=="BLEND") { material.SetFloat("_Surface",1);material.SetFloat("_SrcBlend",5);material.SetFloat("_DstBlend",10);material.SetFloat("_ZWrite",0);material.renderQueue=3000;material.EnableKeyword("_SURFACE_TYPE_TRANSPARENT"); }
            if (material.HasProperty("_Cull")) material.SetFloat("_Cull",surface.double_sided?0:2);
            AssetDatabase.CreateAsset(material,path);
            return material;
        }

        static string ProjectFile(string path) => Path.GetFullPath(Path.Combine(Application.dataPath, "..", path));

        static Package ReadPackage(string path, bool allowDetail = false)
        {
            var package = JsonUtility.FromJson<Package>(File.ReadAllText(path));
            var id = package?.StableId ?? "";
            if (package == null || package.target != "unity" ||
                !(CardId.IsMatch(id) || (allowDetail && DetailId.IsMatch(id))))
                throw new InvalidDataException("Choose a Unity export's studio-export.json at the top of its model folder.");
            return package;
        }

        static IEnumerable<string> InstalledRoots(bool includeDetails = false)
        {
            foreach (var path in AssetDatabase.GetAllAssetPaths())
            {
                if (!path.StartsWith("Assets/", StringComparison.Ordinal) ||
                    !path.EndsWith("/" + ManifestName, StringComparison.Ordinal)) continue;
                try { ReadPackage(ProjectFile(path), allowDetail: includeDetails); }
                catch (Exception e) when (e is IOException || e is ArgumentException) { continue; }
                yield return path.Substring(0, path.Length - ManifestName.Length - 1);
            }
        }

        [Flags] internal enum UpdateParts { Model = 1, Textures = 2, AuxiliaryModels = 4 }

        internal class UpdateFile
        {
            public string AssetPath, Source, Before, After;
        }

        internal class UpdatePlan
        {
            public readonly List<UpdateFile> Files = new List<UpdateFile>();
            public string InstalledName, IncomingName, InstalledId, IncomingId;
        }

        static string Fingerprint(string path)
        {
            using (var sha = SHA256.Create())
            using (var stream = File.OpenRead(path))
                return Convert.ToBase64String(sha.ComputeHash(stream));
        }

        static string CheckedFile(string directory, string relative)
        {
            var path = directory;
            foreach (var component in relative.Split('/'))
            {
                path = Path.Combine(path, component);
                if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException("Package files must not use links: " + relative);
            }
            return path;
        }

        static string[] Slots(Package package) => (package.materials ?? Array.Empty<Surface>())
            .Select(surface => surface.name + "\n" + surface.source_name).ToArray();

        static IEnumerable<string> MapsFor(Package package)
        {
            foreach (var surface in package.materials ?? Array.Empty<Surface>())
            {
                if (surface.maps == null) continue;
                foreach (var path in new[] { surface.maps.BaseColor, surface.maps.MetallicSmoothness,
                    surface.maps.NormalOpenGL, surface.maps.Occlusion, surface.maps.Emissive })
                {
                    if (string.IsNullOrEmpty(path)) continue;
                    if (!TexturePath.IsMatch(path) || !path.StartsWith("Textures/" + surface.name + "_", StringComparison.Ordinal))
                        throw new InvalidDataException("Invalid Studio texture path: " + path);
                    yield return path;
                }
            }
        }

        static IEnumerable<string> DetailFolders(Package package) => (package.lods?.levels ?? Array.Empty<DetailLevel>())
            .Where(level => level.level > 0 && level.export_path == "LODs/LOD" + level.level)
            .Select(level => level.export_path).Distinct().OrderBy(path => path, StringComparer.Ordinal);

        internal static UpdatePlan PreviewUpdate(string root, string manifestPath, UpdateParts parts, bool allowVariant)
        {
            root = root?.TrimEnd('/');
            if (string.IsNullOrEmpty(root) || !AssetDatabase.IsValidFolder(root) || !root.StartsWith("Assets/", StringComparison.Ordinal))
                throw new InvalidDataException("Select the installed Studio model folder under Assets.");
            var installedRoot = ProjectFile(root);
            var assetsPrefix = Path.GetFullPath(Application.dataPath).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (!installedRoot.StartsWith(assetsPrefix, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Select the installed Studio model folder under Assets.");
            var relativeRoot = installedRoot.Substring(assetsPrefix.Length).Replace('\\', '/');
            CheckedFile(Application.dataPath, relativeRoot);
            manifestPath = Path.GetFullPath(manifestPath);
            var incomingRoot = Path.GetDirectoryName(manifestPath);
            var projectPrefix = ProjectFile(".").TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (manifestPath.StartsWith(projectPrefix, StringComparison.OrdinalIgnoreCase) ||
                Path.GetFileName(manifestPath) != ManifestName)
                throw new InvalidDataException("Select the new studio-export.json extracted outside this Unity project.");

            var installed = ReadPackage(CheckedFile(installedRoot, ManifestName));
            var incoming = ReadPackage(CheckedFile(incomingRoot, ManifestName));
            var installedParent = installed.unity_parent_id;
            var incomingParent = incoming.unity_parent_id;
            bool related = incomingParent == installed.StableId || installedParent == incoming.StableId ||
                (CardId.IsMatch(installedParent ?? "") && installedParent == incomingParent);
            if (installed.StableId != incoming.StableId && (!allowVariant || !related))
                throw new InvalidDataException("This package is for a different Studio card. A direct parent, child or sibling needs explicit variant selection.");
            var plan = new UpdatePlan
            {
                InstalledName = installed.name, IncomingName = incoming.name,
                InstalledId = installed.StableId, IncomingId = incoming.StableId
            };
            void Add(string relative)
            {
                var assetPath = root + "/" + relative;
                var source = CheckedFile(incomingRoot, relative);
                var before = Fingerprint(CheckedFile(installedRoot, relative));
                var after = Fingerprint(source);
                if (before == after) return;
                CheckedFile(installedRoot, relative + ".meta");
                if (AssetImporter.GetAtPath(assetPath) == null || !AssetDatabase.CanOpenForEdit(assetPath))
                    throw new InvalidDataException("The installed asset must be imported and writable: " + assetPath);
                plan.Files.Add(new UpdateFile { AssetPath = assetPath, Source = source, Before = before, After = after });
            }

            bool updateModel = parts.HasFlag(UpdateParts.Model);
            bool updateTextures = parts.HasFlag(UpdateParts.Textures);
            bool updateDetails = parts.HasFlag(UpdateParts.AuxiliaryModels);
            void AddChanges(Package previous, Package next, string prefix, bool model)
            {
                if (!Slots(previous).SequenceEqual(Slots(next)))
                    throw new InvalidDataException("The ordered material slots differ in " + prefix + "model.fbx. Import it separately to review its assignments.");
                if (model)
                {
                    if (previous.bones != next.bones || (previous.motion_root ?? "") != (next.motion_root ?? ""))
                        throw new InvalidDataException("The skeleton size or root-motion setup differs. Import this model separately to review its rig.");
                    Add(prefix + "model.fbx");
                }
                if (updateTextures)
                    foreach (var texture in MapsFor(previous).Intersect(MapsFor(next), StringComparer.Ordinal)) Add(prefix + texture);
            }
            if (updateModel || updateTextures) AddChanges(installed, incoming, "", updateModel);
            if (updateDetails || updateTextures)
            {
                var detail = DetailFolders(installed).ToArray();
                var incomingDetail = DetailFolders(incoming).ToArray();
                if (updateDetails && (!detail.SequenceEqual(incomingDetail) ||
                    (installed.physics?.kind ?? "none") != (incoming.physics?.kind ?? "none")))
                    throw new InvalidDataException("The LOD or collision layout differs. Review it manually before replacing an existing prefab's meshes.");
                foreach (var folder in detail.Intersect(incomingDetail, StringComparer.Ordinal))
                {
                    var relativeManifest = folder + "/" + ManifestName;
                    var previous = ReadPackage(CheckedFile(installedRoot, relativeManifest), allowDetail: true);
                    var next = ReadPackage(CheckedFile(incomingRoot, relativeManifest), allowDetail: true);
                    AddChanges(previous, next, folder + "/", updateDetails);
                }
                if (updateDetails && installed.physics?.kind == "convex") Add("Collisions/collision.fbx");
            }
            return plan;
        }

        static HashSet<string> ReferencesAt(string path)
        {
            var assets = AssetDatabase.LoadAllAssetsAtPath(path);
            var references = new HashSet<string>(StringComparer.Ordinal);
            void Add(UnityEngine.Object asset)
            {
                if (asset != null && AssetDatabase.TryGetGUIDAndLocalFileIdentifier(asset, out string guid, out long id))
                    references.Add(guid + ":" + id + ":" + asset.GetType().FullName + ":" + asset.name);
            }
            foreach (var asset in assets) Add(asset);
            if (AssetDatabase.LoadAssetAtPath<GameObject>(path) is GameObject model)
            {
                foreach (var node in model.GetComponentsInChildren<Transform>(true))
                {
                    Add(node.gameObject);
                    foreach (var component in node.GetComponents<Component>()) Add(component);
                }
            }
            return references;
        }

        static Dictionary<AssetImporter.SourceAssetIdentifier, UnityEngine.Object> MaterialRemaps(string path) =>
            (AssetImporter.GetAtPath(path) as ModelImporter)?.GetExternalObjectMap();

        static void ImportUpdatedFile(string path)
        {
            string error = null;
            void RecordError(string message, string stack, LogType type)
            {
                if (type == LogType.Error || type == LogType.Exception || type == LogType.Assert)
                    error ??= message;
            }
            Application.logMessageReceived += RecordError;
            try { AssetDatabase.ImportAsset(path, Reimport); }
            finally { Application.logMessageReceived -= RecordError; }
            if (error != null) throw new InvalidDataException("Unity could not import " + path + ". " + error);
        }

        internal static void ApplyUpdate(UpdatePlan plan)
        {
            var backup = Path.Combine(Path.GetTempPath(), "TrellisStudio-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(backup);
            var references = new List<HashSet<string>>();
            var remaps = new List<Dictionary<AssetImporter.SourceAssetIdentifier, UnityEngine.Object>>();
            int started = 0;
            bool keepBackup = false;
            AssetDatabase.DisallowAutoRefresh();
            try
            {
                for (int i = 0; i < plan.Files.Count; i++)
                {
                    var file = plan.Files[i];
                    var path = ProjectFile(file.AssetPath);
                    File.Copy(path, Path.Combine(backup, i + ".old"));
                    File.Copy(path + ".meta", Path.Combine(backup, i + ".meta"));
                    File.Copy(file.Source, Path.Combine(backup, i + ".new"));
                    if (Fingerprint(Path.Combine(backup, i + ".old")) != file.Before ||
                        Fingerprint(Path.Combine(backup, i + ".new")) != file.After)
                        throw new IOException("A package file changed after preview. Preview it again.");
                    var current = ReferencesAt(file.AssetPath);
                    if (current.Count == 0) throw new InvalidDataException("Unity could not read the imported asset: " + file.AssetPath);
                    references.Add(current);
                    remaps.Add(MaterialRemaps(file.AssetPath));
                }
                for (int i = 0; i < plan.Files.Count; i++)
                {
                    var file = plan.Files[i];
                    var path = ProjectFile(file.AssetPath);
                    started = i + 1;
                    File.Copy(Path.Combine(backup, i + ".new"), path, true);
                    ImportUpdatedFile(file.AssetPath);
                    if (!references[i].IsSubsetOf(ReferencesAt(file.AssetPath)))
                        throw new InvalidDataException("Unity changed an existing object reference in " + file.AssetPath + ". The update was reverted.");
                    var beforeRemaps = remaps[i];
                    var afterRemaps = MaterialRemaps(file.AssetPath);
                    if (beforeRemaps != null && (afterRemaps == null || beforeRemaps.Any(pair =>
                        !afterRemaps.TryGetValue(pair.Key, out var mapped) || mapped != pair.Value)))
                        throw new InvalidDataException("Unity changed a material assignment in " + file.AssetPath + ". The update was reverted.");
                }
            }
            catch (Exception error)
            {
                try
                {
                    for (int i = 0; i < started; i++)
                    {
                        var file = plan.Files[i];
                        var path = ProjectFile(file.AssetPath);
                        File.Copy(Path.Combine(backup, i + ".old"), path, true);
                        File.Copy(Path.Combine(backup, i + ".meta"), path + ".meta", true);
                        ImportUpdatedFile(file.AssetPath);
                        if (!references[i].IsSubsetOf(ReferencesAt(file.AssetPath)))
                            throw new IOException("Unity did not restore all original references in " + file.AssetPath + ".");
                    }
                }
                catch (Exception restoreError)
                {
                    keepBackup = true;
                    throw new IOException("Unity could not restore the update. The original files are backed up at " + backup,
                        new AggregateException(error, restoreError));
                }
                throw;
            }
            finally
            {
                AssetDatabase.AllowAutoRefresh();
                if (!keepBackup)
                {
                    try
                    {
                        foreach (var path in Directory.GetFiles(backup)) File.Delete(path);
                        Directory.Delete(backup);
                    }
                    catch (IOException) { Debug.LogWarning("TRELLIS Studio: temporary update files are in " + backup); }
                }
            }
        }

        [MenuItem("Tools/TRELLIS Studio/Preview model update")]
        static void OpenUpdater()
        {
            var window = EditorWindow.GetWindow<UpdateWindow>("TRELLIS Studio update");
            var selected = AssetDatabase.GetAssetPath(Selection.activeObject);
            window.destination = InstalledRoots().Where(root => selected == root ||
                (selected ?? "").StartsWith(root + "/", StringComparison.Ordinal)).OrderByDescending(root => root.Length).FirstOrDefault();
            window.plan = null;
            window.Show();
        }

        internal sealed class UpdateWindow : EditorWindow
        {
            internal string destination;
            internal UpdatePlan plan;
            string source, error, status;
            UpdateParts parts = UpdateParts.Model;
            bool allowVariant;
            Vector2 scroll;

            void OnGUI()
            {
                EditorGUILayout.LabelField("Update an imported Studio model", EditorStyles.boldLabel);
                EditorGUILayout.HelpBox("Keep the installed .meta files, materials and prefab edits. Extract the new export outside the project.", MessageType.Info);
                EditorGUI.BeginChangeCheck();
                var folder = AssetDatabase.LoadAssetAtPath<DefaultAsset>(destination ?? "");
                folder = (DefaultAsset)EditorGUILayout.ObjectField("Installed model folder", folder, typeof(DefaultAsset), false);
                destination = AssetDatabase.GetAssetPath(folder);
                allowVariant = EditorGUILayout.ToggleLeft("Allow a related Studio variant", allowVariant);
                parts = Toggle("Model FBX (geometry and embedded rig/clips)", UpdateParts.Model, parts);
                parts = Toggle("Replace existing matching texture images", UpdateParts.Textures, parts);
                parts = Toggle("Refresh existing LOD and collision FBX files", UpdateParts.AuxiliaryModels, parts);
                if (EditorGUI.EndChangeCheck()) ResetPreview();
                if (parts.HasFlag(UpdateParts.Model) && !parts.HasFlag(UpdateParts.Textures))
                    EditorGUILayout.HelpBox("For a model with rebaked UVs, select matching texture updates. Review box colliders after a size change.", MessageType.Info);

                if (GUILayout.Button("Choose incoming studio-export.json…"))
                {
                    var picked = EditorUtility.OpenFilePanel("Choose the new Unity model export manifest", "", "json");
                    if (!string.IsNullOrEmpty(picked))
                    {
                        source = picked;
                        ResetPreview();
                        if (string.IsNullOrEmpty(destination)) TryAutoSelect();
                    }
                }
                if (!string.IsNullOrEmpty(source)) EditorGUILayout.SelectableLabel(source, GUILayout.Height(EditorGUIUtility.singleLineHeight));
                using (new EditorGUI.DisabledScope(string.IsNullOrEmpty(destination) || string.IsNullOrEmpty(source) || parts == 0))
                    if (GUILayout.Button("Preview selected changes"))
                    {
                        ResetPreview();
                        try { plan = PreviewUpdate(destination, source, parts, allowVariant); }
                        catch (Exception e) { error = e.Message; }
                    }
                if (!string.IsNullOrEmpty(error)) EditorGUILayout.HelpBox(error, MessageType.Error);
                if (!string.IsNullOrEmpty(status)) EditorGUILayout.HelpBox(status, MessageType.Info);
                if (plan == null) return;
                EditorGUILayout.LabelField("Installed", (plan.InstalledName ?? "Studio model") + " [" + plan.InstalledId.Substring(0, 8) + "]");
                EditorGUILayout.LabelField("Incoming", (plan.IncomingName ?? "Studio model") + " [" + plan.IncomingId.Substring(0, 8) + "]");
                if (plan.Files.Count == 0)
                {
                    EditorGUILayout.HelpBox("No matching files would change with these options.", MessageType.Info);
                    return;
                }
                scroll = EditorGUILayout.BeginScrollView(scroll);
                foreach (var file in plan.Files) EditorGUILayout.LabelField(file.AssetPath);
                EditorGUILayout.EndScrollView();
                if (GUILayout.Button("Apply " + plan.Files.Count + " file update(s)"))
                {
                    try
                    {
                        ApplyUpdate(plan);
                        status = "Updated " + plan.Files.Count + " file(s). Inspect the model in its existing scene or prefab.";
                    }
                    catch (Exception e) { error = e.Message; }
                    plan = null;
                }
            }

            static UpdateParts Toggle(string label, UpdateParts flag, UpdateParts selected) =>
                EditorGUILayout.ToggleLeft(label, selected.HasFlag(flag)) ? selected | flag : selected & ~flag;

            void ResetPreview()
            {
                plan = null;
                status = error = null;
            }

            void TryAutoSelect()
            {
                try
                {
                    var incoming = ReadPackage(source);
                    var matches = InstalledRoots().Where(root =>
                        ReadPackage(ProjectFile(root + "/" + ManifestName)).StableId == incoming.StableId).ToArray();
                    if (matches.Length == 1) destination = matches[0];
                }
                catch (Exception e) { error = e.Message; }
            }
        }
    }
}
#endif
