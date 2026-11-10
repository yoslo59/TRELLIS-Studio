using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace TrellisStudio
{
    public static class ReimportRegression
    {
        const string Root = "Assets/Installed";
        const string Model = Root + "/model.fbx";
        const string MaterialPath = Root + "/TS_Mat_000.mat";
        const string TexturePath = Root + "/Textures/TS_Mat_000_BaseColor.png";
        const string Wrapper = Root + "/StudioAsset.prefab";
        const string Lod = Root + "/LODs/LOD1/model.fbx";
        static readonly string Project = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
        static readonly string Packages = Path.Combine(Path.GetDirectoryName(Project), "packages");

        public static void Run()
        {
            PackageImporter.Configure();
            var custom = AssetDatabase.LoadAssetAtPath<Material>(MaterialPath);
            Assert(custom != null, "initial configure creates a material");
            custom.color = Color.magenta;
            EditorUtility.SetDirty(custom);
            const string RemapPath = "Assets/ArtistRemap.mat";
            var remap = new Material(custom) { color = Color.cyan };
            AssetDatabase.CreateAsset(remap, RemapPath);
            var slot = new AssetImporter.SourceAssetIdentifier(typeof(Material), "TS_Mat_000");
            var importer = (ModelImporter)AssetImporter.GetAtPath(Model);
            importer.AddRemap(slot, remap);
            importer.globalScale = 1.25f;
            importer.SaveAndReimport();
            var wrapper = PrefabUtility.LoadPrefabContents(Wrapper);
            Assert(wrapper.GetComponent<LODGroup>().lodCount == 2, "legacy export configures both LOD levels");
            Assert(AssetDatabase.LoadAssetAtPath<Material>(Root + "/LODs/LOD1/TS_Mat_000.mat") != null, "legacy detail material is configured");
            new GameObject("Hand-authored part").transform.SetParent(wrapper.transform, false);
            PrefabUtility.SaveAsPrefabAsset(wrapper, Wrapper);
            PrefabUtility.UnloadPrefabContents(wrapper);
            AssetDatabase.SaveAssets();

            var mesh = AssetDatabase.LoadAllAssetsAtPath(Model).OfType<Mesh>().First();
            var bounds = mesh.bounds.size.x;
            var materialGuid = AssetDatabase.AssetPathToGUID(MaterialPath);
            var textureGuid = AssetDatabase.AssetPathToGUID(TexturePath);
            var modelGuid = AssetDatabase.AssetPathToGUID(Model);
            var modelMeta = Bytes(Model + ".meta");
            var prefabBytes = Bytes(Wrapper);
            var originalTexture = Bytes(TexturePath);
            var originalLod = Bytes(Lod);
            var lodGuid = AssetDatabase.AssetPathToGUID(Lod);
            Assert(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(mesh, out string _, out long meshId), "initial mesh identifier");
            const string Use = "Assets/UsesImportedMesh.prefab";
            var use = new GameObject("Uses installed model");
            use.AddComponent<MeshFilter>().sharedMesh = mesh;
            use.AddComponent<MeshRenderer>().sharedMaterial = custom;
            PrefabUtility.SaveAsPrefabAsset(use, Use);
            UnityEngine.Object.DestroyImmediate(use);

            // The original Configure action is idempotent after manual edits.
            PackageImporter.Configure();
            Assert(AssetDatabase.LoadAssetAtPath<Material>(MaterialPath).color == Color.magenta, "configure preserves colour");
            Assert(Bytes(Wrapper).SequenceEqual(prefabBytes), "configure preserves wrapper edits");
            Assert(((ModelImporter)AssetImporter.GetAtPath(Model)).GetExternalObjectMap()[slot] == remap, "configure preserves custom remap");
            string updated = Path.Combine(Packages, "Updated", "studio-export.json");
            Expect<InvalidDataException>(() => PackageImporter.PreviewUpdate(Root, updated, PackageImporter.UpdateParts.Model, false));
            var modelPlan = PackageImporter.PreviewUpdate(Root, updated, PackageImporter.UpdateParts.Model, true);
            Assert(modelPlan.Files.Count == 1 && modelPlan.Files[0].AssetPath == Model, "geometry-only preview");
            var incomingModel = modelPlan.Files[0].Source;
            var beforeChange = File.ReadAllBytes(incomingModel);
            File.AppendAllText(incomingModel, "changed after preview");
            Expect<IOException>(() => PackageImporter.ApplyUpdate(modelPlan));
            File.WriteAllBytes(incomingModel, beforeChange);
            PackageImporter.ApplyUpdate(modelPlan);
            Assert(AssetDatabase.AssetPathToGUID(Model) == modelGuid, "FBX GUID survived");
            Assert(Bytes(Model + ".meta").SequenceEqual(modelMeta), "FBX .meta survived");
            Assert(((ModelImporter)AssetImporter.GetAtPath(Model)).globalScale == 1.25f, "custom import scale survived");
            Assert(((ModelImporter)AssetImporter.GetAtPath(Model)).GetExternalObjectMap()[slot] == remap, "custom remap survived");
            Assert(Bytes(TexturePath).SequenceEqual(originalTexture), "geometry-only update keeps the texture bytes");
            Assert(Bytes(Lod).SequenceEqual(originalLod), "geometry-only update keeps the LOD file");
            Assert(Bytes(Wrapper).SequenceEqual(prefabBytes), "hand-edited wrapper survived");
            var used = AssetDatabase.LoadAssetAtPath<GameObject>(Use);
            var updatedMesh = used.GetComponent<MeshFilter>().sharedMesh;
            Assert(updatedMesh != null && updatedMesh.bounds.size.x > bounds * 1.5f, "prefab reference sees the changed geometry");
            Assert(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(updatedMesh, out string newGuid, out long newMeshId) &&
                newGuid == modelGuid && newMeshId == meshId, "mesh local id survived");
            Assert(AssetDatabase.AssetPathToGUID(MaterialPath) == materialGuid &&
                used.GetComponent<MeshRenderer>().sharedMaterial.color == Color.magenta, "custom material survived");

            var textures = PackageImporter.PreviewUpdate(Root, updated, PackageImporter.UpdateParts.Textures, true);
            Assert(textures.Files.Count == 2 && textures.Files[0].AssetPath == TexturePath &&
                textures.Files.All(file => file.AssetPath.EndsWith(".png")), "textures-only preview covers root and LOD");
            PackageImporter.ApplyUpdate(textures);
            Assert(!Bytes(TexturePath).SequenceEqual(originalTexture) &&
                AssetDatabase.AssetPathToGUID(TexturePath) == textureGuid, "texture bytes changed, GUID kept");
            Assert(AssetDatabase.LoadAssetAtPath<Material>(MaterialPath).color == Color.magenta, "texture update preserves material parameters");
            var auxiliary = PackageImporter.PreviewUpdate(Root, updated, PackageImporter.UpdateParts.AuxiliaryModels, true);
            Assert(auxiliary.Files.Count == 1 && auxiliary.Files[0].AssetPath == Lod, "LOD-only preview");
            PackageImporter.ApplyUpdate(auxiliary);
            Assert(!Bytes(Lod).SequenceEqual(originalLod) && AssetDatabase.AssetPathToGUID(Lod) == lodGuid &&
                Bytes(Wrapper).SequenceEqual(prefabBytes), "LOD update keeps GUID and wrapper");
            var stableModel = Bytes(Model);
            var incompatible = PackageImporter.PreviewUpdate(Root, Path.Combine(Packages, "Incompatible", "studio-export.json"),
                PackageImporter.UpdateParts.Model, true);
            Expect<InvalidDataException>(() => PackageImporter.ApplyUpdate(incompatible));
            Assert(Bytes(Model).SequenceEqual(stableModel) && Bytes(Model + ".meta").SequenceEqual(modelMeta), "failed update restores bytes and .meta");
            Assert(AssetDatabase.LoadAssetAtPath<GameObject>(Use).GetComponent<MeshFilter>().sharedMesh != null, "references survive rollback");
            Debug.Log("TRELLIS REIMPORT PASS");
        }

        static byte[] Bytes(string path) => File.ReadAllBytes(Path.Combine(Project, path));

        static void Assert(bool condition, string message)
        {
            if (!condition) throw new Exception("Unity reimport regression: " + message);
        }

        static void Expect<T>(Action act) where T : Exception
        {
            try { act(); }
            catch (T) { return; }
            throw new Exception("Unity reimport regression expected " + typeof(T).Name);
        }
    }
}
