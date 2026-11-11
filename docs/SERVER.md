# TRELLIS Server for Windows

TRELLIS Server lets people on your network use the Studio in a browser. Each
account has its own library. With Full Studio installed, generation jobs enter
a shared queue and run on the server's GPU.

## Install and create the first administrator

1. Run the Windows installer and choose **TRELLIS Server (multi-user)**. Then
   select **Workshop only** for the library, 3D viewer, and GLB import and export,
   or **Full Studio** to add TRELLIS.2, UniRig, and Pixal3D. Full Studio requires a
   CUDA-compatible NVIDIA GPU, Blender, and Ubuntu 22.04 under WSL2.
2. Choose a data folder on a local drive. The default for the Windows installer
   is `%LOCALAPPDATA%\TRELLIS Studio Server\data`. Restrict access to the Windows
   account running TRELLIS and to the machine's administrators.
3. Set the listen address, port, and browser URL. To test on the server itself,
   use `127.0.0.1`, port `7862`, and `http://127.0.0.1:7862`. For access from a
   private network, enter the server's IPv4 address and an HTTPS URL on the same
   port. Provide the certificate and private key as PEM files. Client computers
   must recognise the URL's hostname or IP address, and the certificate must
   cover it.
4. The local setup wizard opens in your browser. Enter the first administrator's
   first name, last name, email address, and password. The administrator has
   unlimited storage and generation credits. Once the wizard closes, open the
   **TRELLIS Server** shortcut or go to the configured browser URL. Complete
   two-factor setup on first sign-in. An installation pointed at the same data
   folder reuses the existing account.
5. On each client, check that the server address resolves and that the certificate
   is trusted. If Windows Firewall blocks the connection, use `wf.msc` to add an
   inbound TCP rule limited to the selected port, the **Private** profile, and
   the local subnet. Share the browser URL with users.

Optional autostart runs when the Windows account that installed TRELLIS signs
in. This gives the server access to the GPU and installed components. Manage the
TRELLIS administrator account within the Studio.

From a source checkout with the dependencies already installed, run
`.\Start-Server.ps1 -Configure`. The default data folder is `server-data`. To
change the address, port, or certificate, stop the server and open **Configure
TRELLIS Server**. Server settings are saved in `config/server.json`.

## Manage access and quotas

Users can select **Request an account** on the sign-in page. Open
**Administration → Users**, select **Review**, set
the disk quota, and select **Activate account**. Use **Adjust credits** to
grant generation credits. Let the user know once their account is active.

Users can select **Usage** from the account menu. The server queue schedules
jobs across accounts. Disabling an account blocks sign-in and keeps its library.
Administrators can adjust quotas and credits from **Administration**.

## Connect a script or MCP client

Create a personal key in **My account → Security → API keys**. The key
is shown when you create it. Requests use your account's library, disk quota,
and generation credits. Revoke keys from the same page.

For REST, use the server's HTTPS URL, the `/api/v1` routes, and the
`Authorization: Bearer <key>` header. For MCP, run the bridge in STDIO mode on
the computer that runs your MCP client. Set `TRELLIS_API_URL` and
`TRELLIS_API_TOKEN` in the bridge's environment. If the server uses a private
certificate authority, set `TRELLIS_API_CA_BUNDLE` to its PEM file.
[MCP configuration](MCP.md#connect-to-trellis-server)

## Run and maintain the server

- Double-click `Open TRELLIS Server.cmd` or use the **TRELLIS Server** shortcut
  to start the server and open its URL in your browser. In PowerShell, run
  `& '.\Open TRELLIS Server.cmd' -NoBrowser` to start it without opening a tab.
- Wait for active jobs to finish, then use **Stop TRELLIS Server**. Pending jobs
  are available when the server restarts. Logs are in the `logs` folder under
  the server data directory.
- Stop the server before upgrading or uninstalling. The installer preserves
  the data directory and accounts. Run server configuration again if you chose
  a new installation folder.
- To back up the server, stop it and copy the data directory and
  `config/server.json` to protected storage. Store the certificates and keys
  referenced by the configuration separately. To restore, put the files back
  at the configured paths while the server is stopped. Use Studio's library
  export to transfer models between installations.

To recover an administrator account, stop the server and open PowerShell in the
installation folder as the Windows user who manages the data. Run the command
with the administrator's email address. Enter a new password at the prompt.
Recovery revokes the account's sessions and API keys. Set up two-factor
authentication again at the next sign-in and create new API keys if needed.

```powershell
& .\.venv\Scripts\python.exe -B .\server_app.py --config .\config\server.json --recover-admin admin@example.com
```
