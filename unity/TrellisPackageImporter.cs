// Optional editor helper shipped with Unity export packages. It runs only from the menu.
#if UNITY_EDITOR
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace TrellisStudio
{
    public static class PackageImporter
    {
        [Serializable] public class Maps { public string BaseColor, MetallicSmoothness, NormalOpenGL, Occlusion, Emissive; }
        [Serializable] public class Surface { public string name, alpha_mode; public bool double_sided; public float[] base_color, emissive_factor; public float normal_scale=1; public Maps maps; }
        [Serializable] public class DetailLevel { public int level; public string export_path; }
        [Serializable] public class DetailSet { public DetailLevel[] levels; public float[] screen_heights; }
        [Serializable] public class PhysicsProxy { public string kind; }
        [Serializable] public class Package { public string target,motion_root; public int bones; public Surface[] materials; public DetailSet lods; public PhysicsProxy physics; }

        static void PreparedPrefab(string root,Package package)
        {
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
            int count=0;
            foreach (string file in Directory.GetFiles(Application.dataPath,"studio-export.json",SearchOption.AllDirectories))
            {
                var package=JsonUtility.FromJson<Package>(File.ReadAllText(file));
                if (package==null||package.target!="unity") continue;
                string root=("Assets"+Path.GetDirectoryName(file).Substring(Application.dataPath.Length)).Replace('\\','/');
                string model=root+"/model.fbx";
                var importer=AssetImporter.GetAtPath(model) as ModelImporter;
                if (importer==null) continue;
                Shader shader=Shader.Find("Universal Render Pipeline/Lit")??Shader.Find("Standard");
                if (shader==null) throw new InvalidOperationException("Install URP or use a project with the Standard shader.");
                foreach (var surface in package.materials??Array.Empty<Surface>())
                {
                    string path=root+"/"+surface.name+".mat";
                    var material=AssetDatabase.LoadAssetAtPath<Material>(path);
                    if (material==null) { material=new Material(shader); AssetDatabase.CreateAsset(material,path); }
                    material.shader=shader;var maps=surface.maps??new Maps();
                    var colour=Colour(surface.base_color,Color.white);
                    if (material.HasProperty("_BaseColor")) material.SetColor("_BaseColor",colour);
                    if (material.HasProperty("_Color")) material.SetColor("_Color",colour);
                    var baseMap=Texture(root,maps.BaseColor,true);
                    material.SetTexture(material.HasProperty("_BaseMap")?"_BaseMap":"_MainTex",baseMap);
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
                    EditorUtility.SetDirty(material);
                    importer.AddRemap(new AssetImporter.SourceAssetIdentifier(typeof(Material),surface.name),material);
                }
                importer.useFileScale=true;importer.globalScale=1;importer.bakeAxisConversion=true;importer.preserveHierarchy=true;importer.optimizeGameObjects=false;importer.importCameras=false;importer.importLights=false;
                importer.importAnimation=package.bones>0;
                importer.animationCompression=ModelImporterAnimationCompression.Off;
                importer.animationType=package.bones>0?ModelImporterAnimationType.Generic:ModelImporterAnimationType.None;
                importer.avatarSetup=package.bones>0?ModelImporterAvatarSetup.CreateFromThisModel:ModelImporterAvatarSetup.NoAvatar;
                if(!string.IsNullOrEmpty(package.motion_root))importer.motionNodeName=package.motion_root;
                importer.SaveAndReimport();count++;
            }
            foreach(string file in Directory.GetFiles(Application.dataPath,"studio-export.json",SearchOption.AllDirectories)) {
                var package=JsonUtility.FromJson<Package>(File.ReadAllText(file));if(package==null||package.target!="unity")continue;
                string root=("Assets"+Path.GetDirectoryName(file).Substring(Application.dataPath.Length)).Replace('\\','/');PreparedPrefab(root,package);
            }
            AssetDatabase.SaveAssets();Debug.Log("TRELLIS Studio: configured "+count+" exported model(s). No scene was modified.");
        }
    }
}
#endif
