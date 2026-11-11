param([Parameter(Mandatory=$true)][string]$ConfigPath, [Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
& (Join-Path $PSScriptRoot 'check-idle.ps1') -InstallRoot $taskRoot -IgnoreCurrentProcess
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$taskExisting = $null
if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
    $taskExisting = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
}
$taskDefaultData = if (Test-Path -LiteralPath (Join-Path $taskRoot 'bundled-web.json')) {
    Join-Path $env:LOCALAPPDATA 'TRELLIS Studio Server\data'
} else { Join-Path $taskRoot 'server-data' }
$taskForm = New-Object Windows.Forms.Form
$taskForm.Text = 'TRELLIS Studio · Serveur'
$taskForm.StartPosition = 'CenterScreen'
$taskForm.AutoScaleMode = 'Font'
$taskForm.ClientSize = New-Object Drawing.Size(710, 414)
$taskForm.FormBorderStyle = 'FixedDialog'
$taskForm.MaximizeBox = $false
$taskForm.MinimizeBox = $false

function Add-ServerLabel([string]$Text,[int]$X,[int]$Y,[int]$Width,[int]$Height=25) {
    $label = New-Object Windows.Forms.Label
    $label.Text = $Text
    $label.SetBounds($X,$Y,$Width,$Height)
    $taskForm.Controls.Add($label)
    return $label
}
function Add-ServerField([string]$Name,[string]$Value,[int]$Y,[string]$Picker='') {
    [void](Add-ServerLabel $Name 20 ($Y+4) 162)
    $field = New-Object Windows.Forms.TextBox
    $field.Text = $Value
    $width = if ($Picker) { 410 } else { 502 }
    $field.SetBounds(184,$Y,$width,25)
    $taskForm.Controls.Add($field)
    if ($Picker) {
        $browse = New-Object Windows.Forms.Button
        $browse.Text = 'Parcourir'
        $browse.SetBounds(601,$Y-1,85,26)
        $browse.Add_Click({
            if ($Picker -eq 'folder') {
                $dialog = New-Object Windows.Forms.FolderBrowserDialog
                $dialog.SelectedPath = $field.Text
                try { if ($dialog.ShowDialog() -eq 'OK') { $field.Text = $dialog.SelectedPath } }
                finally { $dialog.Dispose() }
            } else {
                $dialog = New-Object Windows.Forms.OpenFileDialog
                $dialog.Filter = 'Fichiers PEM (*.pem;*.crt;*.key)|*.pem;*.crt;*.key|Tous les fichiers (*.*)|*.*'
                try { if ($dialog.ShowDialog() -eq 'OK') { $field.Text = $dialog.FileName } }
                finally { $dialog.Dispose() }
            }
        }.GetNewClosure())
        $taskForm.Controls.Add($browse)
        if ($Picker -eq 'folder' -and $taskExisting) { $browse.Enabled = $false }
    }
    return $field
}

$title = Add-ServerLabel 'Configurer TRELLIS Server' 20 16 666 31
$title.Font = New-Object Drawing.Font($title.Font.FontFamily, 15, [Drawing.FontStyle]::Bold)
[void](Add-ServerLabel 'Le compte administrateur sera créé dans le navigateur avant le démarrage du serveur.' 20 52 666 30)
$data = Add-ServerField 'Dossier des bibliothèques' $(if ($taskExisting) { $taskExisting.data_dir } else { $taskDefaultData }) 87 'folder'
$data.ReadOnly = [bool]$taskExisting
$bind = Add-ServerField 'Adresse d’écoute IPv4' $(if ($taskExisting) { $taskExisting.host } else { '127.0.0.1' }) 120
$port = Add-ServerField 'Port' $(if ($taskExisting) { $taskExisting.port } else { '7862' }) 153
$url = Add-ServerField 'Adresse pour le navigateur' $(if ($taskExisting) { $taskExisting.public_url } else { 'http://127.0.0.1:7862' }) 186
$cert = Add-ServerField 'Certificat HTTPS (PEM)' $taskExisting.tls_cert 219 'file'
$key = Add-ServerField 'Clé privée (PEM)' $taskExisting.tls_key 252 'file'
[void](Add-ServerLabel '127.0.0.1 limite l’accès à ce PC. Pour le réseau, indiquez l’IP du serveur et une URL HTTPS compatible avec le certificat.' 20 287 666 40)
$autostart = New-Object Windows.Forms.CheckBox
$autostart.Text = 'Démarrer à l’ouverture de cette session Windows'
$autostart.Checked = [bool]$taskExisting.autostart
$autostart.SetBounds(20,332,666,27)
$taskForm.Controls.Add($autostart)
$cancel = New-Object Windows.Forms.Button
$cancel.Text = 'Annuler'
$cancel.DialogResult = 'Cancel'
$cancel.SetBounds(482,371,98,28)
$save = New-Object Windows.Forms.Button
$save.Text = 'Continuer'
$save.DialogResult = 'OK'
$save.SetBounds(588,371,98,28)
$taskForm.Controls.Add($cancel)
$taskForm.Controls.Add($save)
$taskForm.CancelButton = $cancel
$taskForm.AcceptButton = $save

try {
    if ($taskForm.ShowDialog() -ne 'OK') { throw 'Server setup was cancelled.' }
    $settings = @{
        data_dir = $data.Text; host = $bind.Text; port = $port.Text; public_url = $url.Text
        tls_cert = $cert.Text; tls_key = $key.Text; autostart = $autostart.Checked
    }
    $OutputEncoding = [Text.UTF8Encoding]::new($false)
    $settings | ConvertTo-Json -Compress | & $Python -B (Join-Path $taskRoot 'server_setup.py') --config $ConfigPath
    if ($LASTEXITCODE -ne 0) { throw 'The server configuration could not be completed. Review the message above and retry.' }
    & (Join-Path $PSScriptRoot 'Manage-Shortcuts.ps1') -InstallRoot $taskRoot -Action ServerStartup
} finally { $taskForm.Dispose() }
