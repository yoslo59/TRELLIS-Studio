Unicode true
ManifestDPIAware true
RequestExecutionLevel user
Name "TRELLIS Studio"
OutFile "${OUTPUT_EXE}"
InstallDir "$LOCALAPPDATA\Programs\TRELLIS Studio"
InstallDirRegKey HKCU "Software\TRELLIS Studio" "InstallDir"
SetCompressor /SOLID lzma
ShowInstDetails show
VIProductVersion "${STUDIO_VERSION}.0"
VIAddVersionKey "ProductName" "TRELLIS Studio"
VIAddVersionKey "FileDescription" "TRELLIS Studio Windows Setup"
VIAddVersionKey "FileVersion" "${STUDIO_VERSION}"
VIAddVersionKey "ProductVersion" "${STUDIO_VERSION}"
VIAddVersionKey "CompanyName" "TRELLIS Studio contributors"
VIAddVersionKey "LegalCopyright" "TRELLIS Studio contributors (MIT)"

!include "MUI2.nsh"
!include "x64.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"
!define ARP "Software\Microsoft\Windows\CurrentVersion\Uninstall\TRELLIS Studio"
!define MUI_ABORTWARNING
!define MUI_LANGDLL_REGISTRY_ROOT "HKCU"
!define MUI_LANGDLL_REGISTRY_KEY "Software\TRELLIS Studio"
!define MUI_LANGDLL_REGISTRY_VALUENAME "Installer Language"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION StartStudio
!define MUI_FINISHPAGE_RUN_TEXT "$(LaunchLabel)"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_ICON "${PAYLOAD_ROOT}\installer\windows\trellis-studio.ico"
!define MUI_UNICON "${PAYLOAD_ROOT}\installer\windows\trellis-studio.ico"

Var InstallFull
Var FullRadio
Var CreateDesktopShortcut
Var DesktopCheckbox

!insertmacro MUI_PAGE_WELCOME
Page custom ShowInstallMode SaveInstallMode
Page custom ShowShortcutOptions SaveShortcutOptions
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "French"

LangString LaunchLabel ${LANG_ENGLISH} "Open TRELLIS Studio"
LangString LaunchLabel ${LANG_FRENCH} "Ouvrir TRELLIS Studio"
LangString WrongPlatform ${LANG_ENGLISH} "TRELLIS Studio requires Windows x64 on an Intel or AMD processor."
LangString WrongPlatform ${LANG_FRENCH} "TRELLIS Studio requiert Windows x64 sur processeur Intel ou AMD."
LangString DifferentInstall ${LANG_ENGLISH} "The selected folder already contains a source installation. Setup did not change it."
LangString DifferentInstall ${LANG_FRENCH} "Ce dossier contient déjà une installation depuis les sources. Aucun fichier n'a été modifié."
LangString StudioRunning ${LANG_ENGLISH} "This copy of TRELLIS Studio or its component installer is still running. Close it before continuing. No process was stopped."
LangString StudioRunning ${LANG_FRENCH} "Cette copie de TRELLIS Studio ou son gestionnaire de composants est en cours d'exécution. Fermez-la avant de continuer."
LangString MarkerFailed ${LANG_ENGLISH} "Setup could not prepare the installation folder: $INSTDIR. Check that the folder is writable and run Setup again."
LangString MarkerFailed ${LANG_FRENCH} "Impossible de préparer le dossier d'installation : $INSTDIR. Vérifiez que le dossier est accessible en écriture, puis relancez l'installateur."
LangString PayloadFailed ${LANG_ENGLISH} "Setup could not copy all application files to $INSTDIR. Close any program using those files and run Setup again."
LangString PayloadFailed ${LANG_FRENCH} "Impossible de copier tous les fichiers de l'application dans $INSTDIR. Fermez les programmes qui les utilisent, puis relancez l'installateur."
LangString CloseForUninstall ${LANG_ENGLISH} "This copy of TRELLIS Studio is running in the background. Stop it and continue uninstalling? Your library will be kept."
LangString CloseForUninstall ${LANG_FRENCH} "Cette copie de TRELLIS Studio tourne en arrière-plan. L'arrêter et poursuivre la désinstallation ? Votre bibliothèque sera conservée."
LangString BusyUninstall ${LANG_ENGLISH} "Studio is running a generation or export. Let it finish before uninstalling."
LangString BusyUninstall ${LANG_FRENCH} "Une génération ou un export est en cours. Attendez la fin de l'opération avant de désinstaller."
LangString CannotCloseForUninstall ${LANG_ENGLISH} "This copy could not be closed automatically. Use Stop TRELLIS Studio in the Start menu and close any MCP client or component installer, then try again."
LangString CannotCloseForUninstall ${LANG_FRENCH} "Cette copie n'a pas pu être arrêtée automatiquement. Utilisez Stop TRELLIS Studio dans le menu Démarrer, fermez les clients MCP ou l'installateur de composants, puis réessayez."
LangString InstallModeTitle ${LANG_ENGLISH} "Choose what to install"
LangString InstallModeTitle ${LANG_FRENCH} "Choisir le type d'installation"
LangString InstallModeSubtitle ${LANG_ENGLISH} "You can add the engines later if you start with the workshop."
LangString InstallModeSubtitle ${LANG_FRENCH} "Vous pourrez ajouter les moteurs plus tard si vous choisissez l'atelier."
LangString WorkshopChoice ${LANG_ENGLISH} "Workshop only"
LangString WorkshopChoice ${LANG_FRENCH} "Atelier seul"
LangString WorkshopDescription ${LANG_ENGLISH} "Library, 3D viewer, diagnostics and GLB import/export. The first launch downloads Python libraries. No model weights or NVIDIA GPU required."
LangString WorkshopDescription ${LANG_FRENCH} "Bibliothèque, vue 3D, diagnostics, import et export GLB. Le premier lancement télécharge les bibliothèques Python. Aucun modèle ni GPU NVIDIA requis."
LangString FullChoice ${LANG_ENGLISH} "Full Studio (TRELLIS.2, UniRig and Pixal3D)"
LangString FullChoice ${LANG_FRENCH} "Studio complet (TRELLIS.2, UniRig et Pixal3D)"
LangString FullDescription ${LANG_ENGLISH} "About 29 GiB of model data, plus dependencies. Requires an NVIDIA CUDA driver, Blender and a working Ubuntu 22.04 in WSL2."
LangString FullDescription ${LANG_FRENCH} "Environ 29 Gio de données de modèles, plus les dépendances. Requiert un pilote NVIDIA CUDA, Blender et Ubuntu 22.04 configuré dans WSL2."
LangString FullProgress ${LANG_ENGLISH} "A separate console shows download progress and asks you to accept the DINOv3 licence."
LangString FullProgress ${LANG_FRENCH} "Une console séparée affiche la progression et vous demande d'accepter la licence DINOv3."
LangString InstallingFull ${LANG_ENGLISH} "Installing the engines. Continue in the TRELLIS Studio PowerShell window."
LangString InstallingFull ${LANG_FRENCH} "Installation des moteurs. Poursuivez dans la fenêtre PowerShell de TRELLIS Studio."
LangString FullFailed ${LANG_ENGLISH} "The workshop is installed, but the full setup did not finish. See $INSTDIR\logs\full-setup.log and run Setup again to resume."
LangString FullFailed ${LANG_FRENCH} "L'atelier est installé, mais l'installation complète n'a pas abouti. Consultez $INSTDIR\logs\full-setup.log puis relancez le Setup pour reprendre."
LangString FullConsoleFailed ${LANG_ENGLISH} "The workshop is installed, but Setup could not open the console. Run Setup again or install the engines from the Start menu."
LangString FullConsoleFailed ${LANG_FRENCH} "L'atelier est installé, mais le Setup n'a pas pu ouvrir la console. Relancez le Setup ou installez les moteurs depuis le menu Démarrer."
LangString ShortcutOptionsTitle ${LANG_ENGLISH} "Shortcuts"
LangString ShortcutOptionsTitle ${LANG_FRENCH} "Raccourcis"
LangString ShortcutOptionsSubtitle ${LANG_ENGLISH} "Choose where to open TRELLIS Studio."
LangString ShortcutOptionsSubtitle ${LANG_FRENCH} "Choisissez comment ouvrir TRELLIS Studio."
LangString StartMenuDescription ${LANG_ENGLISH} "TRELLIS Studio will be added to the Start menu."
LangString StartMenuDescription ${LANG_FRENCH} "TRELLIS Studio sera ajouté au menu Démarrer."
LangString DesktopChoice ${LANG_ENGLISH} "Create a desktop shortcut"
LangString DesktopChoice ${LANG_FRENCH} "Créer un raccourci sur le Bureau"
LangString ShortcutError ${LANG_ENGLISH} "Studio files are installed, but one or more shortcuts could not be created. You can launch Studio with $INSTDIR\installer\windows\Launch.cmd."
LangString ShortcutError ${LANG_FRENCH} "Studio est installé, mais certains raccourcis n'ont pas pu être créés. Vous pouvez ouvrir Studio avec $INSTDIR\installer\windows\Launch.cmd."

Function .onInit
  StrCpy $InstallFull "0"
  StrCpy $CreateDesktopShortcut "0"
  ${IfNot} ${IsNativeAMD64}
    MessageBox MB_ICONSTOP "$(WrongPlatform)" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Function ShowInstallMode
  !insertmacro MUI_HEADER_TEXT "$(InstallModeTitle)" "$(InstallModeSubtitle)"
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateFirstRadioButton} 0 3u 100% 13u "$(WorkshopChoice)"
  Pop $1
  ${NSD_CreateLabel} 13u 19u -13u 32u "$(WorkshopDescription)"
  Pop $0
  ${NSD_CreateAdditionalRadioButton} 0 57u 100% 13u "$(FullChoice)"
  Pop $FullRadio
  ${NSD_CreateLabel} 13u 73u -13u 32u "$(FullDescription)"
  Pop $0
  ${NSD_CreateLabel} 13u 109u -13u 27u "$(FullProgress)"
  Pop $0
  ${If} $InstallFull == "1"
    ${NSD_Check} $FullRadio
  ${Else}
    ${NSD_Check} $1
  ${EndIf}
  nsDialogs::Show
FunctionEnd

Function SaveInstallMode
  ${NSD_GetState} $FullRadio $InstallFull
FunctionEnd

Function ShowShortcutOptions
  !insertmacro MUI_HEADER_TEXT "$(ShortcutOptionsTitle)" "$(ShortcutOptionsSubtitle)"
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 6u 100% 20u "$(StartMenuDescription)"
  Pop $0
  ${NSD_CreateCheckbox} 0 31u 100% 16u "$(DesktopChoice)"
  Pop $DesktopCheckbox
  ${If} $CreateDesktopShortcut == "1"
    ${NSD_Check} $DesktopCheckbox
  ${EndIf}
  nsDialogs::Show
FunctionEnd

Function SaveShortcutOptions
  ${NSD_GetState} $DesktopCheckbox $CreateDesktopShortcut
FunctionEnd

Function StartStudio
  ExecShell "open" "$INSTDIR\installer\windows\Launch.cmd"
FunctionEnd

!macro ReadStudioState ExtraArguments
  InitPluginsDir
  File /oname=$PLUGINSDIR\check-idle.ps1 "${PAYLOAD_ROOT}\installer\windows\check-idle.ps1"
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\check-idle.ps1" -InstallRoot "$INSTDIR" ${ExtraArguments}'
  Pop $0
  Pop $1
!macroend

!macro CheckIdle
  !insertmacro ReadStudioState ""
  ${If} $0 != "0"
    DetailPrint "$1"
    MessageBox MB_ICONSTOP "$(StudioRunning)" /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
!macroend

!macro ManageShortcuts Mode ExtraArguments
  InitPluginsDir
  File /oname=$PLUGINSDIR\Manage-Shortcuts.ps1 "${PAYLOAD_ROOT}\installer\windows\Manage-Shortcuts.ps1"
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\Manage-Shortcuts.ps1" -InstallRoot "$INSTDIR" -Action ${Mode} ${ExtraArguments}'
  Pop $0
  Pop $1
  ${If} $1 != ""
    DetailPrint "$1"
  ${EndIf}
!macroend

Section "TRELLIS Studio"
  IfFileExists "$INSTDIR\.trellis-managed-install" owned
  IfFileExists "$INSTDIR\bundled-web.json" owned
  IfFileExists "$INSTDIR\app.py" foreign
  ; The previous uninstaller removed the owner marker, but kept its runtime.
  IfFileExists "$INSTDIR\.studio-workshop-ready" 0 check_retained_data
  IfFileExists "$INSTDIR\.component-install.lock" owned
  check_retained_data:
  IfFileExists "$INSTDIR\outputs\*.*" foreign
  Goto owned
  foreign:
    MessageBox MB_ICONSTOP "$(DifferentInstall)" /SD IDOK
    SetErrorLevel 2
    Abort
  owned:
  !insertmacro CheckIdle
  ClearErrors
  SetOutPath "$INSTDIR"
  IfErrors marker_failed
  ; NSIS errors are sticky. A stale flag from an earlier check must not
  ; turn a successful open into an abort with an empty ownership marker.
  ClearErrors
  FileOpen $0 "$INSTDIR\.trellis-managed-install" w
  IfErrors marker_failed
  FileWrite $0 "TRELLIS Studio managed installation"
  IfErrors marker_write_failed
  FileClose $0
  IfErrors marker_failed
  ClearErrors
  !include "${INSTALL_FILES}"
  IfErrors payload_failed
  SetOutPath "$INSTDIR"
  IfErrors payload_failed
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  IfErrors payload_failed
  WriteRegStr HKCU "Software\TRELLIS Studio" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${ARP}" "DisplayName" "TRELLIS Studio"
  WriteRegStr HKCU "${ARP}" "DisplayVersion" "${STUDIO_VERSION}"
  WriteRegStr HKCU "${ARP}" "Publisher" "TRELLIS Studio contributors"
  WriteRegStr HKCU "${ARP}" "DisplayIcon" "$INSTDIR\installer\windows\trellis-studio.ico"
  WriteRegStr HKCU "${ARP}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${ARP}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${ARP}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${ARP}" "NoModify" 1
  WriteRegDWORD HKCU "${ARP}" "NoRepair" 1
  !insertmacro ManageShortcuts "Install" "-DesktopShortcut $CreateDesktopShortcut"
  ${If} $0 != "0"
    MessageBox MB_ICONEXCLAMATION "$(ShortcutError)" /SD IDOK
  ${EndIf}
  ${If} $InstallFull == "1"
    DetailPrint "$(InstallingFull)"
    ClearErrors
    ExecWait '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\installer\windows\Launch-Installed.ps1" -Mode Full -NoBrowser' $0
    IfErrors full_console_failed
    ${If} $0 != "0"
      DetailPrint "$(FullFailed)"
      MessageBox MB_ICONEXCLAMATION "$(FullFailed)" /SD IDOK
      SetErrorLevel 3
      Abort
    ${EndIf}
  ${EndIf}
  Goto install_done
  full_console_failed:
    DetailPrint "$(FullConsoleFailed)"
    MessageBox MB_ICONEXCLAMATION "$(FullConsoleFailed)" /SD IDOK
    SetErrorLevel 3
    Abort
  marker_write_failed:
    FileClose $0
  marker_failed:
    DetailPrint "$(MarkerFailed)"
    MessageBox MB_ICONSTOP "$(MarkerFailed)" /SD IDOK
    SetErrorLevel 2
    Abort
  payload_failed:
    DetailPrint "$(PayloadFailed)"
    MessageBox MB_ICONSTOP "$(PayloadFailed)" /SD IDOK
    SetErrorLevel 2
    Abort
  install_done:
SectionEnd

Function un.onInit
  !insertmacro MUI_UNGETLANGUAGE
FunctionEnd

Section "Uninstall"
  !insertmacro ReadStudioState "-Action ProbeUninstall"
  ${If} $0 == "10"
    MessageBox MB_ICONQUESTION|MB_YESNO|MB_DEFBUTTON2 "$(CloseForUninstall)" /SD IDYES IDYES close_studio
    SetErrorLevel 2
    Abort
    close_studio:
    !insertmacro ReadStudioState "-Action StopForUninstall"
  ${EndIf}
  ${If} $0 != "0"
    DetailPrint "$1"
    ${If} $0 == "11"
      MessageBox MB_ICONSTOP "$(BusyUninstall)" /SD IDOK
    ${Else}
      MessageBox MB_ICONSTOP "$(CannotCloseForUninstall)" /SD IDOK
    ${EndIf}
    SetErrorLevel 2
    Abort
  ${EndIf}
  !insertmacro ManageShortcuts "Uninstall" ""
  !include "${UNINSTALL_FILES}"
  Delete "$INSTDIR\Uninstall.exe"
  ; Keep the owner marker with the retained library for a later reinstall.
  ReadRegStr $0 HKCU "Software\TRELLIS Studio" "InstallDir"
  ${If} $0 == "$INSTDIR"
    DeleteRegKey HKCU "${ARP}"
    DeleteRegKey HKCU "Software\TRELLIS Studio"
  ${EndIf}
SectionEnd
