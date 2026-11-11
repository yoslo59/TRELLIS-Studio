# TRELLIS Server pour Windows

TRELLIS Server donne accès au Studio depuis le navigateur des postes du réseau. Chaque compte dispose de sa bibliothèque. Avec le Studio complet, les générations sont mises en file et traitées par le GPU du serveur.

## Installer et créer le premier administrateur

1. Lancez l’installateur Windows et choisissez **TRELLIS Server (multi-utilisateurs)**, puis **Atelier seul** ou **Studio complet**. L’atelier comprend la bibliothèque, la vue 3D, l’import et l’export GLB. Le Studio complet ajoute TRELLIS.2, UniRig et Pixal3D. Il requiert un GPU NVIDIA compatible CUDA, Blender et Ubuntu 22.04 dans WSL2.
2. Choisissez un dossier de données sur un disque local. Le dossier proposé sur une installation Windows est `%LOCALAPPDATA%\TRELLIS Studio Server\data`. Gardez ce dossier accessible uniquement au compte Windows qui exécute TRELLIS et aux administrateurs de la machine.
3. Choisissez l’adresse d’écoute, le port et l’adresse à ouvrir dans le navigateur. Pour un premier essai sur ce PC, conservez `127.0.0.1`, le port `7862` et `http://127.0.0.1:7862`. Pour ouvrir l’accès sur le réseau privé, indiquez l’IPv4 du serveur et une adresse HTTPS avec le même port. Indiquez le certificat et la clé privée au format PEM. Le nom d’hôte ou l’adresse saisis dans l’URL doivent être couverts par le certificat et reconnus par les postes clients.
4. Lors de la première configuration, le navigateur affiche l’assistant local de création du premier administrateur. Renseignez ses prénom, nom, mail et mot de passe. Ce compte a un quota disque et des crédits illimités. Le serveur démarre ensuite avec l’adresse choisie. Une installation sur les mêmes données réutilise l’administrateur existant.
5. Pour un accès depuis un autre poste, vérifiez la résolution du nom et la confiance dans le certificat. Si le pare-feu Windows filtre le port, ajoutez une règle entrante TCP limitée à ce port, au profil **Privé** et au sous-réseau local depuis `wf.msc`. Communiquez l’adresse finale aux utilisateurs.

Le démarrage automatique est facultatif et se fait à l’ouverture de la session Windows qui a installé le serveur. Il utilise cette session pour accéder au GPU et à ses composants. Le compte administrateur TRELLIS est géré dans l’application.

Depuis une copie des sources dont les dépendances sont déjà installées, utilisez `Start-Server.ps1 -Configure`. Le dossier proposé est alors `server-data`. Pour changer l’adresse, le port ou le certificat, arrêtez le serveur et utilisez **Configure TRELLIS Server**. L’adresse et le dossier sont enregistrés dans `config/server.json`.

## Gérer l’accès et les quotas

Sur la page de connexion, un utilisateur peut remplir **Demander un compte**. La demande est visible dans **Administration → Users**. Ouvrez la fiche avec **Review**, fixez le quota disque et attribuez les crédits, puis activez le compte. Transmettez la décision à l’utilisateur selon votre canal habituel.

Les utilisateurs consultent leur utilisation dans leur profil. La file du serveur organise l’accès au moteur. La désactivation d’un compte protège son accès et conserve sa bibliothèque. Les quotas et les crédits se modifient depuis l’administration.

## Connecter un script ou un client MCP

Dans **Sécurité → Clés API**, créez une clé personnelle pour votre client. Elle s’affiche à la création. Les opérations utilisent votre bibliothèque, votre quota disque et vos crédits. Vous pouvez révoquer la clé depuis le même écran.

Pour l’API REST, utilisez l’adresse HTTPS du serveur, les routes `/api/v1` et l’en-tête `Authorization: Bearer <clé>`. Pour MCP, lancez le pont en STDIO sur le poste qui exécute le client. Renseignez `TRELLIS_API_URL` et `TRELLIS_API_TOKEN` dans son environnement. Si le serveur utilise une autorité de certification privée, indiquez son fichier PEM avec `TRELLIS_API_CA_BUNDLE`. [Configuration MCP](MCP.md#connect-to-trellis-server)

## Exploiter le serveur

- Double-cliquez sur `Open TRELLIS Server.cmd` ou utilisez le raccourci **TRELLIS Server** pour démarrer le serveur si nécessaire et ouvrir son adresse dans le navigateur. `Open TRELLIS Server.cmd -NoBrowser` le lance en arrière-plan.
- **Stop TRELLIS Server** arrête le serveur au repos. Attendez la fin des calculs actifs, puis lancez le raccourci. Les travaux en attente restent disponibles à la reprise. Les journaux sont dans le dossier `logs` des données serveur.
- Avant une mise à jour ou une désinstallation, arrêtez le serveur. L’installeur conserve le dossier des données et les comptes. Relancez la configuration si vous avez choisi un nouvel emplacement d’installation.
- Pour sauvegarder, arrêtez le serveur et copiez ensemble le dossier de données et `config/server.json` sur un support protégé. Conservez séparément les certificats et clés pointés par la configuration. Pour restaurer, remettez la copie à l’emplacement indiqué par la configuration pendant que le serveur est arrêté. Une sauvegarde de bibliothèque exportée depuis le Studio reste un transfert de modèles, distinct d’une sauvegarde du serveur et de ses comptes.

Pour récupérer un compte administrateur, arrêtez le serveur et ouvrez PowerShell dans le dossier d’installation avec le compte Windows qui gère les données. Saisissez la commande ci-dessous avec l’adresse du compte. Le nouveau mot de passe est demandé dans le terminal. Réactivez la double authentification à la connexion suivante.

```powershell
& .\.venv\Scripts\python.exe -B .\server_app.py --config .\config\server.json --recover-admin admin@example.com
```
