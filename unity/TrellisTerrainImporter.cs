#if UNITY_EDITOR
using System;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace TrellisLocal.Editor
{
    public static class TrellisTerrainImporter
    {
        [Serializable] public class Manifest
        {
            public string name, raw;
            public int resolution, seed;
            public float size_m, height_m;
        }

        [MenuItem("Outils/TRELLIS/Importer les terrains Unity")]
        public static void ImportAll()
        {
            int count=0;
            foreach(string file in Directory.GetFiles("Assets","terrain-manifest.json",SearchOption.AllDirectories))
            { CreateTerrain(file.Replace('\\','/')); count++; }
            Debug.Log("TRELLIS : "+count+" terrain(s) importes.");
        }

        public static string CreateTerrain(string manifestPath)
        {
            var info=JsonUtility.FromJson<Manifest>(File.ReadAllText(manifestPath));
            string folder=Path.GetDirectoryName(manifestPath).Replace('\\','/');
            var bytes=File.ReadAllBytes(folder+"/"+info.raw);
            if(bytes.Length != info.resolution*info.resolution*2) throw new InvalidDataException("RAW incomplet.");
            var heights=new float[info.resolution,info.resolution];
            for(int z=0;z<info.resolution;z++) for(int x=0;x<info.resolution;x++)
            { int i=2*(z*info.resolution+x); heights[z,x]=(bytes[i]|bytes[i+1]<<8)/65535f; }
            string dataPath=folder+"/"+info.name+"-TerrainData.asset";
            var data=AssetDatabase.LoadAssetAtPath<TerrainData>(dataPath);
            if(data==null) { data=new TerrainData(); AssetDatabase.CreateAsset(data,dataPath); }
            data.heightmapResolution=info.resolution;
            data.size=new Vector3(info.size_m,info.height_m,info.size_m);
            data.SetHeights(0,0,heights);
            var layerNames=new[]{"Soil","Rock","Grass"};
            var layers=new TerrainLayer[3];
            for(int i=0;i<3;i++)
            {
                string layerPath=folder+"/"+layerNames[i]+".terrainlayer";
                var layer=AssetDatabase.LoadAssetAtPath<TerrainLayer>(layerPath);
                if(layer==null) { layer=new TerrainLayer(); AssetDatabase.CreateAsset(layer,layerPath); }
                layer.diffuseTexture=LoadTexture(folder+"/"+layerNames[i]+".png",true,false);
                layer.normalMapTexture=LoadTexture(folder+"/"+layerNames[i]+"Normal.png",false,false,true);
                layer.tileSize=new Vector2(6,6); layer.smoothness=.05f; layer.metallic=0;
                layers[i]=layer; EditorUtility.SetDirty(layer);
            }
            data.terrainLayers=layers;
            data.alphamapResolution=256;
            var splat=LoadTexture(folder+"/Splatmap.png",false,true);
            var alpha=new float[256,256,3];
            for(int z=0;z<256;z++) for(int x=0;x<256;x++)
            {
                Color c=splat.GetPixelBilinear(x/255f,1-z/255f);
                float total=Mathf.Max(c.r+c.g+c.b,.001f);
                alpha[z,x,0]=c.r/total;alpha[z,x,1]=c.g/total;alpha[z,x,2]=c.b/total;
            }
            data.SetAlphamaps(0,0,alpha);
            var root=Terrain.CreateTerrainGameObject(data);
            try
            {
                root.name=info.name;
                var terrain=root.GetComponent<Terrain>();
                var shader=Shader.Find("Universal Render Pipeline/Terrain/Lit");
                if(shader!=null)
                {
                    string matPath=folder+"/"+info.name+"-Terrain.mat";
                    var mat=AssetDatabase.LoadAssetAtPath<Material>(matPath);
                    if(mat==null) {mat=new Material(shader);AssetDatabase.CreateAsset(mat,matPath);}
                    terrain.materialTemplate=mat;
                }
                terrain.drawInstanced=true;
                terrain.heightmapPixelError=5;
                string prefab=folder+"/"+info.name+".prefab";
                PrefabUtility.SaveAsPrefabAsset(root,prefab);
                EditorUtility.SetDirty(data);AssetDatabase.SaveAssets();return prefab;
            }
            finally { UnityEngine.Object.DestroyImmediate(root); }
        }

        static Texture2D LoadTexture(string path,bool srgb,bool readable,bool normal=false)
        {
            AssetDatabase.ImportAsset(path,ImportAssetOptions.ForceSynchronousImport);
            var importer=AssetImporter.GetAtPath(path) as TextureImporter;
            if(importer!=null)
            { importer.textureType=normal?TextureImporterType.NormalMap:TextureImporterType.Default;
              importer.sRGBTexture=srgb;importer.isReadable=readable;importer.wrapMode=readable?TextureWrapMode.Clamp:TextureWrapMode.Repeat;
              importer.textureCompression=TextureImporterCompression.Uncompressed;importer.SaveAndReimport(); }
            return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
        }
    }
}
#endif
