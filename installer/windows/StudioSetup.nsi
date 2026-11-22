Unicode true
ManifestDPIAware true
RequestExecutionLevel user
!define GUIDED_MARKER ".trellis-guided-setup"
!define PREVIEW_MARKER ".trellis-setup-test"
!ifdef SETUP_PREVIEW
  !define SETUP_NAME "TRELLIS Studio Setup Test"
  !define SETUP_MARKER "${PREVIEW_MARKER}"
  !define SETUP_DESCRIPTION "TRELLIS Studio Setup Test for Windows"
!else
  !define SETUP_NAME "TRELLIS Studio"
  !define SETUP_MARKER "${GUIDED_MARKER}"
  !define SETUP_DESCRIPTION "TRELLIS Studio Windows Setup"
!endif
!define SETUP_KEY "Software\${SETUP_NAME}"
Name "${SETUP_NAME}"
OutFile "${OUTPUT_EXE}"
InstallDir "$LOCALAPPDATA\Programs\${SETUP_NAME}"
InstallDirRegKey HKCU "${SETUP_KEY}" "InstallDir"
SetCompressor /SOLID lzma
ShowInstDetails show
VIProductVersion "${STUDIO_VERSION}.0"
VIAddVersionKey "ProductName" "${SETUP_NAME}"
VIAddVersionKey "FileDescription" "${SETUP_DESCRIPTION}"
VIAddVersionKey "FileVersion" "${STUDIO_VERSION}"
VIAddVersionKey "ProductVersion" "${STUDIO_VERSION}"
VIAddVersionKey "CompanyName" "TRELLIS Studio contributors"
VIAddVersionKey "LegalCopyright" "TRELLIS Studio contributors (MIT)"

!include "MUI2.nsh"
!include "x64.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"
; The x86 NSIS stub otherwise launches SysWOW64 PowerShell, which cannot see the
; native NVIDIA utility through System32. Keep every setup helper in the native host.
!if ${NSIS_PTR_SIZE} > 4
  !define SETUP_SYSTEM_DIR "$SYSDIR"
!else
  !define SETUP_SYSTEM_DIR "$WINDIR\Sysnative"
!endif
!define ARP "Software\Microsoft\Windows\CurrentVersion\Uninstall\${SETUP_NAME}"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION StartStudio
!define MUI_FINISHPAGE_RUN_TEXT "$(LaunchLabel)"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_ICON "${PAYLOAD_ROOT}\installer\windows\trellis-studio.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${PAYLOAD_ROOT}\installer\windows\trellis-welcome.bmp"
!define MUI_UNICON "${PAYLOAD_ROOT}\installer\windows\trellis-studio.ico"

Var InstallTrellis
Var InstallPixal3D
Var InstallUniRig
Var TrellisCheckbox
Var Pixal3DCheckbox
Var UniRigCheckbox
Var GpuName
Var GpuMemory
Var VramNotice
Var GpuDriver
Var GpuCompute
Var GpuUuid
Var GpuProfile
Var GpuSupported
Var RigSupported
Var RigStatus
Var WslDistribution
Var WslReady
Var BlenderPath
Var BlenderReady
Var BlenderSummary
Var BlenderInput
Var BlenderStatusLabel
Var ComponentsArgument
Var HardwareArguments
Var PreflightRoot
Var InstallServer
Var ServerRadio
Var CreateDesktopShortcut
Var DesktopCheckbox
Var InstallHi3d
Var Hi3dSupported
Var SkipHi3d
Var Hi3dCheckbox
Var AcceptHi3dLicenses
Var Hi3dLicenseCheckbox
Var InstallMultiviewTexture
Var MultiviewTextureSupported
Var SkipMultiviewTexture
Var MultiviewTextureCheckbox
Var AcceptMultiviewTextureLicenses
Var MultiviewTextureLicenseCheckbox
Var InstallUltraTex
Var UltraTexSupported
Var SkipUltraTex
Var UltraTexCheckbox
Var AcceptUltraTexLicenses
Var UltraTexLicenseCheckbox
Var InstallNativeWorkflows
Var NativeWorkflowsDefault
Var NativeWorkflowsSupported
Var SkipNativeWorkflows
Var NativeWorkflowsCheckbox
Var AcceptNativeWorkflowLicenses
Var NativeWorkflowLicenseCheckbox
Var InstallKimodo
Var KimodoCheckbox
Var AcceptKimodoLicenses
Var KimodoLicenseCheckbox
Var KimodoArguments

!ifdef SETUP_PREVIEW
  !define MUI_WELCOMEPAGE_TITLE "Welcome to the TRELLIS Studio Setup Test"
!endif
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE ValidateInstallFolder
!insertmacro MUI_PAGE_DIRECTORY
Page custom ShowInstallMode SaveInstallMode
Page custom ShowHardware SaveHardware
Page custom ShowComponentMode SaveComponentMode
Page custom ShowNativeWorkflowOptions SaveNativeWorkflowOptions
Page custom ShowHi3dOptions SaveHi3dOptions
Page custom ShowMultiviewTextureOptions SaveMultiviewTextureOptions
Page custom ShowUltraTexOptions SaveUltraTexOptions
Page custom ShowKimodoOptions SaveKimodoOptions
Page custom ShowShortcutOptions SaveShortcutOptions
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

LangString LaunchLabel ${LANG_ENGLISH} "Open TRELLIS from its tray icon"
LangString WrongPlatform ${LANG_ENGLISH} "TRELLIS Studio requires Windows x64 on an Intel or AMD processor."
LangString DifferentInstall ${LANG_ENGLISH} "The selected folder already contains files and is not a recognized ${SETUP_NAME} installation. Choose an empty folder. Setup did not change the folder."
LangString DifferentUninstall ${LANG_ENGLISH} "This folder is not a recognized ${SETUP_NAME} installation. No files were removed."
LangString StudioRunning ${LANG_ENGLISH} "This copy of TRELLIS or its installer is still running. Stop Desktop / Server, then quit its tray icon before continuing. No process was stopped."
LangString MarkerFailed ${LANG_ENGLISH} "Setup could not prepare the installation folder: $INSTDIR. Check that the folder is writable and run Setup again."
LangString PayloadFailed ${LANG_ENGLISH} "Setup could not copy all application files to $INSTDIR. Close any program using those files and run Setup again."
LangString CloseForUninstall ${LANG_ENGLISH} "This copy of TRELLIS Studio is running in the background. Stop it and continue uninstalling? Your library will be kept."
LangString BusyUninstall ${LANG_ENGLISH} "Studio is running a generation or export. Let it finish before uninstalling."
LangString CannotCloseForUninstall ${LANG_ENGLISH} "Stop TRELLIS Studio or TRELLIS Server from the Start menu and close any MCP client or component installer, then try again."
LangString InstallModeTitle ${LANG_ENGLISH} "Choose how to use TRELLIS"
LangString InstallModeSubtitle ${LANG_ENGLISH} "Choose the edition before selecting the components."
LangString DesktopModeChoice ${LANG_ENGLISH} "TRELLIS Desktop (single user)"
LangString DesktopModeDescription ${LANG_ENGLISH} "Use your local library on this PC without creating an account."
LangString ServerChoice ${LANG_ENGLISH} "TRELLIS Server (multi-user)"
!ifdef SETUP_PREVIEW
  LangString ServerDescription ${LANG_ENGLISH} "Set up an isolated local server and its first administrator. This test uses its own data folder and a localhost address."
  LangString InstallingServer ${LANG_ENGLISH} "Preparing the isolated local server. Follow the instructions in the TRELLIS PowerShell window."
!else
  LangString ServerDescription ${LANG_ENGLISH} "Choose local access, LAN access with your certificate, or Internet access with automatic HTTPS. Configure the data folder, optional startup and first administrator."
  LangString InstallingServer ${LANG_ENGLISH} "Preparing the server. Follow the instructions in the TRELLIS PowerShell window."
!endif
LangString HardwareTitle ${LANG_ENGLISH} "Check this PC"
LangString HardwareSubtitle ${LANG_ENGLISH} "Review the detected GPU and choose the Blender executable."
LangString ComponentModeTitle ${LANG_ENGLISH} "Choose the components"
LangString ComponentModeSubtitle ${LANG_ENGLISH} "Install the Workshop and select the additional engines for this PC."
LangString WorkshopDescription ${LANG_ENGLISH} "Workshop includes the 3D viewer, GLB tools and standard animation files. Blender applies clips to compatible rigs."
LangString ServerFailed ${LANG_ENGLISH} "Server setup did not finish. Review the console messages and run Setup again to resume."
LangString InstallingWorkshop ${LANG_ENGLISH} "Preparing the Workshop and tray application. Python dependencies are downloaded automatically."
LangString WorkshopFailed ${LANG_ENGLISH} "The application files are installed, but Workshop preparation did not finish. Review the console and run Setup again to resume."
LangString ComponentsNeedAttention ${LANG_ENGLISH} "Some components need attention. See the setup logs in $INSTDIR\logs, then open Repair or resume components in the ${SETUP_NAME} Start menu folder."
LangString ShortcutOptionsTitle ${LANG_ENGLISH} "Shortcuts"
LangString ShortcutOptionsSubtitle ${LANG_ENGLISH} "Choose where to open TRELLIS Studio."
LangString StartMenuDescription ${LANG_ENGLISH} "The Start menu opens a tray icon to start, stop and open TRELLIS. Quitting the icon keeps the application running."
LangString DesktopChoice ${LANG_ENGLISH} "Create a desktop shortcut"
LangString ShortcutError ${LANG_ENGLISH} "Studio files are installed, but one or more shortcuts could not be created. You can launch Studio with $INSTDIR\installer\windows\Launch.cmd."
LangString KimodoTitle ${LANG_ENGLISH} "Optional motion generation"
LangString KimodoSubtitle ${LANG_ENGLISH} "Available with either Desktop or Server."
LangString KimodoChoice ${LANG_ENGLISH} "Install Kimodo text-to-motion (optional)"
LangString KimodoDescription ${LANG_ENGLISH} "Create reusable humanoid animations from text on the CPU. About 9.3 GB of model weights plus portable build tools. No NVIDIA GPU or CUDA required."
LangString KimodoRequirements ${LANG_ENGLISH} "Needs WSL2 Linux x64, Python 3.10+, an AVX2/FMA/F16C CPU and 12 GiB free in WSL. Setup guides you if WSL is missing; Windows may require a restart."
LangString KimodoNvidiaLicense ${LANG_ENGLISH} "Read the NVIDIA Open Model License"
LangString KimodoLlamaLicense ${LANG_ENGLISH} "Read the Meta Llama 3 Community License (text encoder)"
LangString KimodoAccept ${LANG_ENGLISH} "I have read and accept both model licenses for the optional Kimodo installation."
LangString KimodoLicenseRequired ${LANG_ENGLISH} "Accept both model licenses to install Kimodo, or clear the optional Kimodo selection."

Function .onInit
  ; A PowerShell 7 parent can pass modules that Windows PowerShell 5.1 cannot load.
  System::Call 'kernel32::SetEnvironmentVariableW(w "PSModulePath", p 0)'
  ; Ignore language preferences saved by previous bilingual installers.
  StrCpy $LANGUAGE ${LANG_ENGLISH}
  StrCpy $InstallTrellis "0"
  StrCpy $InstallPixal3D "0"
  StrCpy $InstallUniRig "0"
  StrCpy $BlenderReady "0"
  StrCpy $InstallServer "0"
  IfFileExists "$INSTDIR\config\server.json" 0 +2
  StrCpy $InstallServer "1"
  StrCpy $CreateDesktopShortcut "0"
  StrCpy $InstallHi3d "0"
  StrCpy $SkipHi3d "0"
  StrCpy $AcceptHi3dLicenses "0"
  StrCpy $InstallMultiviewTexture "0"
  StrCpy $SkipMultiviewTexture "0"
  StrCpy $AcceptMultiviewTextureLicenses "0"
  StrCpy $InstallUltraTex "0"
  StrCpy $SkipUltraTex "0"
  StrCpy $AcceptUltraTexLicenses "0"
  StrCpy $InstallNativeWorkflows "0"
  StrCpy $NativeWorkflowsDefault "0"
  StrCpy $SkipNativeWorkflows "0"
  StrCpy $AcceptNativeWorkflowLicenses "0"
  StrCpy $InstallKimodo "0"
  StrCpy $AcceptKimodoLicenses "0"
  ${IfNot} ${IsNativeAMD64}
    MessageBox MB_ICONSTOP "$(WrongPlatform)" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
FunctionEnd

Function ValidateInstallFolder
  IfFileExists "$INSTDIR\.git" foreign
  IfFileExists "$INSTDIR\.git\*.*" foreign
!ifndef SETUP_PREVIEW
  IfFileExists "$INSTDIR\${PREVIEW_MARKER}" foreign
!endif
  ClearErrors
  FileOpen $0 "$INSTDIR\${SETUP_MARKER}" r
  IfErrors check_legacy
  StrCpy $1 ""
  FileRead $0 $1
  FileClose $0
  StrCmp $1 "${SETUP_NAME}" allowed

  check_legacy:
!ifndef SETUP_PREVIEW
    ; Only migrate an existing packaged installation at the registered path.
    IfFileExists "$INSTDIR\bundled-web.json" 0 check_directory
    ClearErrors
    ReadRegStr $1 HKCU "${SETUP_KEY}" "InstallDir"
    IfErrors check_directory
    StrCmp $1 "" check_directory
    GetFullPathName $0 "$INSTDIR"
    GetFullPathName $1 "$1"
    IfErrors check_directory
    StrCmp $0 $1 allowed
!endif
  check_directory:
    ClearErrors
    FindFirst $0 $1 "$INSTDIR\*.*"
    IfErrors allowed
  next_entry:
    StrCmp $1 "." ignore_entry
    StrCmp $1 ".." ignore_entry
    StrCmp $1 "" empty
    FindClose $0
  foreign:
    MessageBox MB_ICONSTOP "$(DifferentInstall)" /SD IDOK
    SetErrorLevel 2
    Abort
  ignore_entry:
    ClearErrors
    FindNext $0 $1
    IfErrors empty next_entry
  empty:
    FindClose $0
  allowed:
    ClearErrors
    SetErrorLevel 0
FunctionEnd

Function ShowInstallMode
  !insertmacro MUI_HEADER_TEXT "$(InstallModeTitle)" "$(InstallModeSubtitle)"
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateFirstRadioButton} 0 6u 100% 13u "$(DesktopModeChoice)"
  Pop $1
  ${NSD_CreateLabel} 13u 23u -13u 25u "$(DesktopModeDescription)"
  Pop $0
  ${NSD_CreateAdditionalRadioButton} 0 55u 100% 13u "$(ServerChoice)"
  Pop $ServerRadio
  ${NSD_CreateLabel} 13u 72u -13u 34u "$(ServerDescription)"
  Pop $0
  ${If} $InstallServer == "1"
    ${NSD_Check} $ServerRadio
  ${Else}
    ${NSD_Check} $1
  ${EndIf}
  nsDialogs::Show
FunctionEnd

Function SaveInstallMode
  ${NSD_GetState} $ServerRadio $InstallServer
FunctionEnd

Function RunHardwareProbe
  InitPluginsDir
  File /oname=$PLUGINSDIR\Setup-Preflight.ps1 "${PAYLOAD_ROOT}\installer\windows\Setup-Preflight.ps1"
  File /oname=$PLUGINSDIR\gpu-runtime-profiles.json "${PAYLOAD_ROOT}\installer\gpu-runtime-profiles.json"
  Delete "$PLUGINSDIR\preflight.ini"
  nsExec::ExecToStack /TIMEOUT=30000 '"${SETUP_SYSTEM_DIR}\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\Setup-Preflight.ps1" -Action Probe -InstallRoot "$INSTDIR" -OutputPath "$PLUGINSDIR\preflight.ini"'
  Pop $0
  Pop $1
FunctionEnd

Function ShowHardware
  Call RunHardwareProbe
  ${If} $PreflightRoot != $INSTDIR
    StrCpy $PreflightRoot "$INSTDIR"
    StrCpy $BlenderSummary ""
    ReadINIStr $InstallTrellis "$PLUGINSDIR\preflight.ini" "Plan" "Trellis"
    ReadINIStr $InstallPixal3D "$PLUGINSDIR\preflight.ini" "Plan" "Pixal3D"
    ReadINIStr $InstallUniRig "$PLUGINSDIR\preflight.ini" "Plan" "UniRig"
    ReadINIStr $InstallHi3d "$PLUGINSDIR\preflight.ini" "Plan" "Hi3D"
    ReadINIStr $AcceptHi3dLicenses "$PLUGINSDIR\preflight.ini" "Plan" "Hi3DLicenseAccepted"
    ReadINIStr $InstallMultiviewTexture "$PLUGINSDIR\preflight.ini" "Plan" "MultiviewTexture"
    ReadINIStr $AcceptMultiviewTextureLicenses "$PLUGINSDIR\preflight.ini" "Plan" "MultiviewTextureLicenseAccepted"
    ReadINIStr $InstallUltraTex "$PLUGINSDIR\preflight.ini" "Plan" "UltraTex"
    ReadINIStr $AcceptUltraTexLicenses "$PLUGINSDIR\preflight.ini" "Plan" "UltraTexLicenseAccepted"
    ReadINIStr $InstallNativeWorkflows "$PLUGINSDIR\preflight.ini" "Plan" "NativeWorkflows"
    ReadINIStr $NativeWorkflowsDefault "$PLUGINSDIR\preflight.ini" "Plan" "NativeWorkflowsDefault"
    ReadINIStr $AcceptNativeWorkflowLicenses "$PLUGINSDIR\preflight.ini" "Plan" "NativeWorkflowsLicenseAccepted"
    StrCpy $SkipNativeWorkflows "0"
    ${If} $InstallNativeWorkflows != "1"
      StrCpy $InstallNativeWorkflows "0"
    ${EndIf}
    ${If} $AcceptNativeWorkflowLicenses != "1"
      StrCpy $AcceptNativeWorkflowLicenses "0"
    ${EndIf}
    ${If} $InstallUltraTex != "1"
      StrCpy $InstallUltraTex "0"
    ${EndIf}
    ${If} $AcceptUltraTexLicenses != "1"
      StrCpy $AcceptUltraTexLicenses "0"
    ${EndIf}
    ${If} $InstallMultiviewTexture != "1"
      StrCpy $InstallMultiviewTexture "0"
    ${EndIf}
    ${If} $AcceptMultiviewTextureLicenses != "1"
      StrCpy $AcceptMultiviewTextureLicenses "0"
    ${EndIf}
    ${If} $InstallHi3d != "1"
      StrCpy $InstallHi3d "0"
    ${EndIf}
    ${If} $AcceptHi3dLicenses != "1"
      StrCpy $AcceptHi3dLicenses "0"
    ${EndIf}
    ${If} $InstallTrellis != "1"
      StrCpy $InstallTrellis "0"
    ${EndIf}
    ${If} $InstallPixal3D != "1"
      StrCpy $InstallPixal3D "0"
    ${EndIf}
    ${If} $InstallUniRig != "1"
      StrCpy $InstallUniRig "0"
    ${EndIf}
  ${EndIf}
  !insertmacro MUI_HEADER_TEXT "$(HardwareTitle)" "$(HardwareSubtitle)"
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ReadINIStr $GpuName "$PLUGINSDIR\preflight.ini" "GPU" "Name"
  ReadINIStr $GpuMemory "$PLUGINSDIR\preflight.ini" "GPU" "MemoryMiB"
  ReadINIStr $VramNotice "$PLUGINSDIR\preflight.ini" "GPU" "VramNotice"
  ReadINIStr $GpuDriver "$PLUGINSDIR\preflight.ini" "GPU" "Driver"
  ReadINIStr $GpuCompute "$PLUGINSDIR\preflight.ini" "GPU" "Compute"
  ReadINIStr $GpuUuid "$PLUGINSDIR\preflight.ini" "GPU" "Uuid"
  ReadINIStr $GpuProfile "$PLUGINSDIR\preflight.ini" "GPU" "Profile"
  ReadINIStr $GpuSupported "$PLUGINSDIR\preflight.ini" "GPU" "Supported"
  ReadINIStr $RigSupported "$PLUGINSDIR\preflight.ini" "GPU" "UniRigSupported"
  ReadINIStr $RigStatus "$PLUGINSDIR\preflight.ini" "GPU" "UniRigStatus"
  ReadINIStr $2 "$PLUGINSDIR\preflight.ini" "GPU" "Status"
  ${If} $GpuName != ""
    StrCpy $3 "$GpuName ($GpuMemory MiB VRAM)"
    StrCpy $4 "Compute capability $GpuCompute  |  Driver $GpuDriver"
  ${Else}
    StrCpy $3 "NVIDIA GPU unavailable"
    StrCpy $4 "Workshop remains available."
  ${EndIf}
  ${If} $GpuProfile != ""
    StrCpy $4 "$4  |  Profile $GpuProfile"
  ${EndIf}
  ${If} $2 == ""
    StrCpy $2 "Hardware check could not complete. You can continue with Workshop."
  ${EndIf}
  ${NSD_CreateLabel} 0 0u 100% 11u "$3"
  Pop $0
  ${NSD_CreateLabel} 0 12u 100% 11u "$4"
  Pop $0
  ${NSD_CreateLabel} 0 23u 100% 20u "$2"
  Pop $0
  ReadINIStr $WslDistribution "$PLUGINSDIR\preflight.ini" "WSL" "Distribution"
  ReadINIStr $WslReady "$PLUGINSDIR\preflight.ini" "WSL" "Ready"
  ReadINIStr $2 "$PLUGINSDIR\preflight.ini" "WSL" "Status"
  ${If} $2 == ""
    StrCpy $2 "WSL2 could not be verified."
  ${EndIf}
  ${NSD_CreateLabel} 0 43u 100% 20u "$2"
  Pop $0
  ReadINIStr $2 "$PLUGINSDIR\preflight.ini" "Disk" "Status"
  ${NSD_CreateLabel} 0 63u 100% 16u "$2"
  Pop $0
  ${If} $BlenderSummary == ""
    ReadINIStr $BlenderPath "$PLUGINSDIR\preflight.ini" "Blender" "Path"
    ReadINIStr $BlenderReady "$PLUGINSDIR\preflight.ini" "Blender" "Ready"
    ReadINIStr $BlenderSummary "$PLUGINSDIR\preflight.ini" "Blender" "Status"
  ${EndIf}
  ${NSD_CreateLabel} 0 80u 100% 11u "Blender executable"
  Pop $0
  ${NSD_CreateText} 0 92u -58u 13u "$BlenderPath"
  Pop $BlenderInput
  ${NSD_CreateBrowseButton} -53u 92u 53u 13u "Browse..."
  Pop $0
  ${NSD_OnClick} $0 BrowseBlender
  ${NSD_CreateLabel} 0 108u 100% 19u "$BlenderSummary"
  Pop $BlenderStatusLabel
  ${NSD_CreateLabel} 0 128u 100% 11u "Leave blank to configure Blender later."
  Pop $0
  nsDialogs::Show
FunctionEnd

Function BrowseBlender
  Pop $0
  ${NSD_GetText} $BlenderInput $1
  nsDialogs::SelectFileDialog open "$1" "Blender executable|blender.exe"
  Pop $0
  ${If} $0 != ""
    ${NSD_SetText} $BlenderInput "$0"
    ${NSD_SetText} $BlenderStatusLabel "The selected Blender will be checked when you continue."
  ${EndIf}
FunctionEnd

Function SaveHardware
  ${NSD_GetText} $BlenderInput $BlenderPath
  StrCpy $BlenderReady "0"
  ${If} $BlenderPath == ""
    StrCpy $BlenderSummary "Blender will be configured later."
    Return
  ${EndIf}
  IfFileExists "$BlenderPath" validate_blender
    ${NSD_SetText} $BlenderStatusLabel "Choose an existing blender.exe file."
    Abort
  validate_blender:
  Delete "$PLUGINSDIR\blender.ini"
  nsExec::ExecToStack /TIMEOUT=20000 '"${SETUP_SYSTEM_DIR}\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\Setup-Preflight.ps1" -Action ValidateBlender -BlenderPath "$BlenderPath" -OutputPath "$PLUGINSDIR\blender.ini"'
  Pop $0
  Pop $1
  ReadINIStr $BlenderReady "$PLUGINSDIR\blender.ini" "Blender" "Ready"
  ReadINIStr $BlenderSummary "$PLUGINSDIR\blender.ini" "Blender" "Status"
  ${If} $BlenderReady != "1"
    ${If} $BlenderSummary == ""
      StrCpy $BlenderSummary "Blender could not be verified. Choose another file or clear the path."
    ${EndIf}
    ${NSD_SetText} $BlenderStatusLabel "$BlenderSummary"
    Abort
  ${EndIf}
  ReadINIStr $BlenderPath "$PLUGINSDIR\blender.ini" "Blender" "Path"
FunctionEnd

Function ShowComponentMode
  !insertmacro MUI_HEADER_TEXT "$(ComponentModeTitle)" "$(ComponentModeSubtitle)"
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 1u 100% 22u "$(WorkshopDescription)"
  Pop $0
  ${NSD_CreateCheckbox} 0 27u 100% 13u "TRELLIS.2 image-to-3D  (about 15 GiB of models)"
  Pop $TrellisCheckbox
  ${NSD_CreateCheckbox} 0 45u 100% 13u "Pixal3D compatibility generation  (about 8.25 GiB more)"
  Pop $Pixal3DCheckbox
  ${NSD_CreateCheckbox} 0 63u 100% 13u "UniRig skeleton and skinning  (WSL2 and Blender)"
  Pop $UniRigCheckbox
  ${If} $GpuSupported != "1"
    StrCpy $InstallTrellis "0"
    StrCpy $InstallPixal3D "0"
    EnableWindow $TrellisCheckbox 0
    EnableWindow $Pixal3DCheckbox 0
    StrCpy $1 "GPU generation needs a supported NVIDIA card and driver."
  ${Else}
    StrCpy $1 "The detected GPU matches the $GpuProfile dependency profile."
  ${EndIf}
  ${NSD_SetState} $TrellisCheckbox $InstallTrellis
  ${NSD_SetState} $Pixal3DCheckbox $InstallPixal3D
  ${NSD_SetState} $UniRigCheckbox $InstallUniRig
  ${NSD_OnClick} $TrellisCheckbox UpdateGenerationChoices
  ${NSD_OnClick} $Pixal3DCheckbox SelectTrellisDependency
  ${If} $GpuSupported != "1"
  ${OrIf} $RigSupported != "1"
    StrCpy $2 "UniRig is unavailable for this GPU profile."
    ${If} $RigStatus != ""
      StrCpy $2 "$RigStatus"
    ${EndIf}
  ${ElseIf} $WslReady != "1"
    StrCpy $2 "UniRig can be added after a compatible WSL2 distribution is ready."
  ${ElseIf} $BlenderReady != "1"
    StrCpy $2 "UniRig can be added after selecting a working Blender."
  ${Else}
    StrCpy $2 "The UniRig prerequisites are available. Setup will test the runtime."
    ${If} $RigStatus != ""
      StrCpy $2 "$RigStatus"
    ${EndIf}
  ${EndIf}
  ${If} $GpuSupported != "1"
  ${OrIf} $RigSupported != "1"
  ${OrIf} $WslReady != "1"
  ${OrIf} $BlenderReady != "1"
    ${NSD_Uncheck} $UniRigCheckbox
    EnableWindow $UniRigCheckbox 0
  ${EndIf}
  ${NSD_CreateLabel} 0 84u 100% 18u "$1"
  Pop $0
  ${NSD_CreateLabel} 0 104u 100% 22u "$2"
  Pop $0
  ${If} $VramNotice != ""
    ${NSD_CreateLabel} 0 126u 100% 20u "$VramNotice"
  ${Else}
    ${NSD_CreateLabel} 0 126u 100% 13u "Setup checks each selected engine before marking it ready."
  ${EndIf}
  Pop $0
  nsDialogs::Show
FunctionEnd

Function UpdateGenerationChoices
  Pop $0
  ${NSD_GetState} $TrellisCheckbox $0
  ${If} $0 != "1"
    ${NSD_Uncheck} $Pixal3DCheckbox
  ${EndIf}
FunctionEnd

Function SelectTrellisDependency
  Pop $0
  ${NSD_GetState} $0 $1
  ${If} $1 == "1"
    ${NSD_Check} $TrellisCheckbox
  ${EndIf}
FunctionEnd

Function SaveComponentMode
  ${NSD_GetState} $TrellisCheckbox $InstallTrellis
  ${NSD_GetState} $Pixal3DCheckbox $InstallPixal3D
  ${NSD_GetState} $UniRigCheckbox $InstallUniRig
  ${If} $InstallPixal3D == "1"
    StrCpy $InstallTrellis "1"
  ${EndIf}
FunctionEnd

Function NativeWorkflowChoiceChanged
  Pop $0
  ${NSD_GetState} $NativeWorkflowsCheckbox $0
  EnableWindow $NativeWorkflowLicenseCheckbox $0
FunctionEnd

Function OpenNativeWorkflowMeshLibLicense
  Pop $0
  ReadINIStr $1 "$PLUGINSDIR\preflight.ini" "NativeWorkflows" "MeshLibLicenseUrl"
  ${If} $1 != ""
    ExecShell "open" "$1"
  ${EndIf}
FunctionEnd

Function ShowNativeWorkflowOptions
  ${If} $InstallTrellis != "1"
    StrCpy $SkipNativeWorkflows "0"
    Abort
  ${EndIf}
  !insertmacro MUI_HEADER_TEXT "High Detail and Game Asset" "Install the native workflows with UltraTex for TRELLIS Studio."
  Call ExtractUltraTexLicense
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ReadINIStr $NativeWorkflowsSupported "$PLUGINSDIR\preflight.ini" "NativeWorkflows" "Supported"
  ReadINIStr $1 "$PLUGINSDIR\preflight.ini" "NativeWorkflows" "Status"
  ${If} $1 == ""
    StrCpy $1 "NVIDIA hardware could not be checked. You can continue with the other components."
  ${EndIf}
  ${NSD_CreateCheckbox} 0 1u 100% 15u "Install High Detail and Game Asset (recommended)"
  Pop $NativeWorkflowsCheckbox
  ${NSD_OnClick} $NativeWorkflowsCheckbox NativeWorkflowChoiceChanged
  ${NSD_CreateLabel} 13u 19u -13u 23u "Setup downloads native geometry, mesh finishing and UltraTex. Read the non-commercial evaluation terms before continuing."
  Pop $0
  ${NSD_CreateLabel} 13u 43u -13u 22u "$1"
  Pop $0
  ${If} $BlenderReady == "1"
    StrCpy $2 "Game Asset uses the Blender selected on Check this PC."
  ${Else}
    StrCpy $2 "Setup can prepare a private portable Blender for Game Asset."
  ${EndIf}
  ${NSD_CreateLabel} 13u 67u -13u 14u "$2"
  Pop $0
  ReadINIStr $2 "$PLUGINSDIR\preflight.ini" "NativeWorkflows" "Guidance"
  ${NSD_CreateLabel} 13u 83u -13u 14u "$2"
  Pop $0
  ${NSD_CreateLink} 13u 99u 52% 11u "FLUX.2 [klein] 9B terms"
  Pop $0
  ${NSD_OnClick} $0 OpenUltraTexLicense
  ${NSD_CreateLink} 58% 99u 35% 11u "MeshLib terms"
  Pop $0
  ${NSD_OnClick} $0 OpenNativeWorkflowMeshLibLicense
  ${If} $NativeWorkflowsSupported != "1"
    EnableWindow $0 0
  ${EndIf}
  ${NSD_CreateCheckbox} 0 112u 100% 22u "I have read and accept the FLUX.2 [klein] 9B and MeshLib terms."
  Pop $NativeWorkflowLicenseCheckbox
  StrCpy $0 $InstallNativeWorkflows
  ${If} $NativeWorkflowsSupported != "1"
    StrCpy $0 "0"
    EnableWindow $NativeWorkflowsCheckbox 0
  ${ElseIf} $NativeWorkflowsDefault == "1"
    StrCpy $0 "1"
  ${EndIf}
  ${NSD_SetState} $NativeWorkflowsCheckbox $0
  ${NSD_SetState} $NativeWorkflowLicenseCheckbox $AcceptNativeWorkflowLicenses
  EnableWindow $NativeWorkflowLicenseCheckbox $0
  nsDialogs::Show
FunctionEnd

Function SaveNativeWorkflowOptions
  ${If} $NativeWorkflowsSupported != "1"
    Return
  ${EndIf}
  ${NSD_GetState} $NativeWorkflowsCheckbox $InstallNativeWorkflows
  ${NSD_GetState} $NativeWorkflowLicenseCheckbox $AcceptNativeWorkflowLicenses
  StrCpy $NativeWorkflowsDefault "0"
  StrCpy $SkipNativeWorkflows "0"
  ${If} $InstallNativeWorkflows == "0"
    StrCpy $SkipNativeWorkflows "1"
  ${ElseIf} $AcceptNativeWorkflowLicenses != "1"
    MessageBox MB_ICONEXCLAMATION "Accept both the FLUX.2 [klein] 9B and MeshLib terms, or clear the native workflows selection." /SD IDOK
    Abort
  ${EndIf}
FunctionEnd

Function Hi3dChoiceChanged
  ${NSD_GetState} $Hi3dCheckbox $InstallHi3d
  EnableWindow $Hi3dLicenseCheckbox $InstallHi3d
FunctionEnd

Function OpenHi3dLicense
  ExecShell "open" "https://github.com/visualbruno/ComfyUI-Hi3D/blob/03dea7e366ba951d7475f3f1c8a71596a87f913c/LICENSE"
FunctionEnd

Function OpenHi3dDinoTerms
  ExecShell "open" "https://huggingface.co/facebook/dinov2-large/tree/47b73eefe95e8d44ec3623f8890bd894b6ea2d6c"
FunctionEnd

Function OpenHi3dClipTerms
  ExecShell "open" "https://huggingface.co/openai/clip-vit-large-patch14/tree/32bd64288804d66eefd0ccbe215aa642df71cc41"
FunctionEnd

Function ShowHi3dOptions
  !insertmacro MUI_HEADER_TEXT "Optional Hi3D Detail Refinement" "Available with Desktop or Server on supported NVIDIA hardware."
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateCheckbox} 0 3u 100% 15u "Install Hi3D Detail Refinement (optional)"
  Pop $Hi3dCheckbox
  ${NSD_OnClick} $Hi3dCheckbox Hi3dChoiceChanged
  ${NSD_CreateLabel} 13u 20u -13u 24u "Refine native TRELLIS.2 meshes in 512 or 1024. Requires TRELLIS.2 generation and downloads about 10.7 GB of weights."
  Pop $0
  ReadINIStr $1 "$PLUGINSDIR\preflight.ini" "Hi3D" "Status"
  ReadINIStr $2 "$PLUGINSDIR\preflight.ini" "Hi3D" "VramNotice"
  ReadINIStr $Hi3dSupported "$PLUGINSDIR\preflight.ini" "Hi3D" "Supported"
  ${If} $1 == ""
    StrCpy $1 "NVIDIA hardware could not be checked. You can continue with the other components."
  ${EndIf}
  ${NSD_CreateLabel} 13u 44u -13u 22u "$1"
  Pop $0
  ${NSD_CreateLabel} 13u 66u -13u 25u "$2"
  Pop $0
  ${NSD_CreateLink} 13u 94u 30% 11u "Hi3D license"
  Pop $0
  ${NSD_OnClick} $0 OpenHi3dLicense
  ${NSD_CreateLink} 34% 94u 29% 11u "DINOv2 terms"
  Pop $0
  ${NSD_OnClick} $0 OpenHi3dDinoTerms
  ${NSD_CreateLink} 66% 94u 28% 11u "CLIP terms"
  Pop $0
  ${NSD_OnClick} $0 OpenHi3dClipTerms
  ${NSD_CreateCheckbox} 0 108u 100% 20u "I have read and accept the Hi3D, DINOv2 and CLIP terms."
  Pop $Hi3dLicenseCheckbox
  ${If} $Hi3dSupported != "1"
    StrCpy $InstallHi3d "0"
    EnableWindow $Hi3dCheckbox 0
  ${EndIf}
  ${NSD_SetState} $Hi3dCheckbox $InstallHi3d
  ${NSD_SetState} $Hi3dLicenseCheckbox $AcceptHi3dLicenses
  EnableWindow $Hi3dLicenseCheckbox $InstallHi3d
  nsDialogs::Show
FunctionEnd

Function SaveHi3dOptions
  ${NSD_GetState} $Hi3dCheckbox $InstallHi3d
  ${NSD_GetState} $Hi3dLicenseCheckbox $AcceptHi3dLicenses
  StrCpy $SkipHi3d "0"
  ${If} $Hi3dSupported == "1"
  ${AndIf} $InstallHi3d == "0"
    StrCpy $SkipHi3d "1"
  ${EndIf}
  ${If} $InstallHi3d == "1"
  ${AndIf} $AcceptHi3dLicenses != "1"
    MessageBox MB_ICONEXCLAMATION "Accept all three model terms, or clear the optional Hi3D selection." /SD IDOK
    Abort
  ${EndIf}
FunctionEnd

Function MultiviewTextureChoiceChanged
  ${NSD_GetState} $MultiviewTextureCheckbox $InstallMultiviewTexture
  EnableWindow $MultiviewTextureLicenseCheckbox $InstallMultiviewTexture
FunctionEnd

Function OpenMultiviewTextureBaseLicense
  ExecShell "open" "$PLUGINSDIR\Stable-Diffusion-2.1-OpenRAIL++-M.txt"
FunctionEnd

Function OpenMultiviewTextureAdapterLicense
  ExecShell "open" "https://github.com/huanngzh/MV-Adapter/blob/4277e0018232bac82bb2c103caf0893cedb711be/LICENSE"
FunctionEnd

Function ShowMultiviewTextureOptions
  !insertmacro MUI_HEADER_TEXT "Optional multiview texture refinement" "Available with Desktop or Server on supported NVIDIA hardware."
  InitPluginsDir
  File /oname=$PLUGINSDIR\Stable-Diffusion-2.1-OpenRAIL++-M.txt "${PAYLOAD_ROOT}\licenses\Stable-Diffusion-2.1-OpenRAIL++-M.txt"
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateCheckbox} 0 3u 100% 15u "Install multiview texture refinement (optional)"
  Pop $MultiviewTextureCheckbox
  ${NSD_OnClick} $MultiviewTextureCheckbox MultiviewTextureChoiceChanged
  ${NSD_CreateLabel} 13u 20u -13u 25u "Refine baked colors from six generated views. Requires TRELLIS.2 generation and downloads about 3.3 GB of model files."
  Pop $0
  ReadINIStr $1 "$PLUGINSDIR\preflight.ini" "MultiviewTexture" "Status"
  ReadINIStr $2 "$PLUGINSDIR\preflight.ini" "MultiviewTexture" "VramNotice"
  ReadINIStr $MultiviewTextureSupported "$PLUGINSDIR\preflight.ini" "MultiviewTexture" "Supported"
  ${If} $1 == ""
    StrCpy $1 "NVIDIA hardware could not be checked. You can continue with the other components."
  ${EndIf}
  ${NSD_CreateLabel} 13u 47u -13u 22u "$1"
  Pop $0
  ${NSD_CreateLabel} 13u 70u -13u 25u "$2"
  Pop $0
  ${NSD_CreateLink} 13u 96u 50% 11u "Stable Diffusion 2.1 terms"
  Pop $0
  ${NSD_OnClick} $0 OpenMultiviewTextureBaseLicense
  ${NSD_CreateLink} 65% 96u 34% 11u "MV-Adapter terms"
  Pop $0
  ${NSD_OnClick} $0 OpenMultiviewTextureAdapterLicense
  ${NSD_CreateCheckbox} 0 110u 100% 20u "I have read and accept the Open RAIL++-M and MV-Adapter terms."
  Pop $MultiviewTextureLicenseCheckbox
  ${If} $MultiviewTextureSupported != "1"
    StrCpy $InstallMultiviewTexture "0"
    EnableWindow $MultiviewTextureCheckbox 0
  ${EndIf}
  ${NSD_SetState} $MultiviewTextureCheckbox $InstallMultiviewTexture
  ${NSD_SetState} $MultiviewTextureLicenseCheckbox $AcceptMultiviewTextureLicenses
  EnableWindow $MultiviewTextureLicenseCheckbox $InstallMultiviewTexture
  nsDialogs::Show
FunctionEnd

Function SaveMultiviewTextureOptions
  ${NSD_GetState} $MultiviewTextureCheckbox $InstallMultiviewTexture
  ${NSD_GetState} $MultiviewTextureLicenseCheckbox $AcceptMultiviewTextureLicenses
  StrCpy $SkipMultiviewTexture "0"
  ${If} $MultiviewTextureSupported == "1"
  ${AndIf} $InstallMultiviewTexture == "0"
    StrCpy $SkipMultiviewTexture "1"
  ${EndIf}
  ${If} $InstallMultiviewTexture == "1"
  ${AndIf} $AcceptMultiviewTextureLicenses != "1"
    MessageBox MB_ICONEXCLAMATION "Accept both model terms or clear the optional multiview texture refinement selection." /SD IDOK
    Abort
  ${EndIf}
FunctionEnd

Function UltraTexChoiceChanged
  Pop $0
  ${NSD_GetState} $UltraTexCheckbox $InstallUltraTex
  EnableWindow $UltraTexLicenseCheckbox $InstallUltraTex
FunctionEnd

Function OpenUltraTexLicense
  Pop $0
  ExecShell "open" "$PLUGINSDIR\FLUX.2-klein-base-9B-LICENSE.txt"
FunctionEnd

Function ExtractUltraTexLicense
  InitPluginsDir
  File /oname=$PLUGINSDIR\FLUX.2-klein-base-9B-LICENSE.txt "${PAYLOAD_ROOT}\licenses\FLUX.2-klein-base-9B-LICENSE.md"
FunctionEnd

Function ShowUltraTexOptions
  !insertmacro MUI_HEADER_TEXT "Optional UltraTex 2K preview" "Six-view compatibility component for local evaluation."
  Call ExtractUltraTexLicense
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateCheckbox} 0 3u 100% 15u "Install UltraTex 2K preview (optional)"
  Pop $UltraTexCheckbox
  ${NSD_OnClick} $UltraTexCheckbox UltraTexChoiceChanged
  ${NSD_CreateLabel} 13u 20u -13u 30u "Six-view compatibility component. High Detail and Game Asset install their own UltraTex runtime. This preview adds about 14 GB of model downloads."
  Pop $0
  ReadINIStr $1 "$PLUGINSDIR\preflight.ini" "UltraTex" "Status"
  ReadINIStr $2 "$PLUGINSDIR\preflight.ini" "UltraTex" "Guidance"
  ReadINIStr $UltraTexSupported "$PLUGINSDIR\preflight.ini" "UltraTex" "Supported"
  ${If} $1 == ""
    StrCpy $1 "NVIDIA hardware could not be checked. You can continue with the other components."
  ${EndIf}
  ${NSD_CreateLabel} 13u 50u -13u 23u "$1"
  Pop $0
  ${NSD_CreateLabel} 13u 75u -13u 21u "$2"
  Pop $0
  ${NSD_CreateLink} 13u 98u 90% 11u "Read the FLUX.2 [klein] 9B model terms"
  Pop $0
  ${NSD_OnClick} $0 OpenUltraTexLicense
  ${NSD_CreateCheckbox} 0 111u 100% 22u "I have read and accept the FLUX.2 [klein] 9B model terms."
  Pop $UltraTexLicenseCheckbox
  ${If} $UltraTexSupported != "1"
    StrCpy $InstallUltraTex "0"
    EnableWindow $UltraTexCheckbox 0
  ${EndIf}
  ${NSD_SetState} $UltraTexCheckbox $InstallUltraTex
  ${NSD_SetState} $UltraTexLicenseCheckbox $AcceptUltraTexLicenses
  EnableWindow $UltraTexLicenseCheckbox $InstallUltraTex
  nsDialogs::Show
FunctionEnd

Function SaveUltraTexOptions
  ${NSD_GetState} $UltraTexCheckbox $InstallUltraTex
  ${NSD_GetState} $UltraTexLicenseCheckbox $AcceptUltraTexLicenses
  StrCpy $SkipUltraTex "0"
  ${If} $UltraTexSupported == "1"
  ${AndIf} $InstallUltraTex == "0"
    StrCpy $SkipUltraTex "1"
  ${EndIf}
  ${If} $InstallUltraTex == "1"
  ${AndIf} $AcceptUltraTexLicenses != "1"
    MessageBox MB_ICONEXCLAMATION "Accept the FLUX.2 [klein] 9B model terms or clear the optional UltraTex selection." /SD IDOK
    Abort
  ${EndIf}
FunctionEnd

Function OpenKimodoNvidiaLicense
  ExecShell "open" "https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/"
FunctionEnd

Function OpenKimodoLlamaLicense
  ExecShell "open" "https://huggingface.co/LocalAI-io/Llama-3-Kimodo-GGML/blob/f583f3ee2b093f6d9ec524463b4152622abf11a6/LICENSE-META-LLAMA-3.txt"
FunctionEnd

Function KimodoChoiceChanged
  ${NSD_GetState} $KimodoCheckbox $InstallKimodo
  EnableWindow $KimodoLicenseCheckbox $InstallKimodo
FunctionEnd

Function KimodoLicenseChanged
  ${NSD_GetState} $KimodoLicenseCheckbox $AcceptKimodoLicenses
FunctionEnd

Function ShowKimodoOptions
  !insertmacro MUI_HEADER_TEXT "$(KimodoTitle)" "$(KimodoSubtitle)"
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}
  ${NSD_CreateCheckbox} 0 3u 100% 15u "$(KimodoChoice)"
  Pop $KimodoCheckbox
  ${NSD_OnClick} $KimodoCheckbox KimodoChoiceChanged
  ${NSD_CreateLabel} 13u 19u -13u 24u "$(KimodoDescription)"
  Pop $0
  ${NSD_CreateLabel} 13u 45u -13u 27u "$(KimodoRequirements)"
  Pop $0
  ${NSD_CreateLink} 13u 76u -13u 10u "$(KimodoNvidiaLicense)"
  Pop $0
  ${NSD_OnClick} $0 OpenKimodoNvidiaLicense
  ${NSD_CreateLink} 13u 90u -13u 10u "$(KimodoLlamaLicense)"
  Pop $0
  ${NSD_OnClick} $0 OpenKimodoLlamaLicense
  ${NSD_CreateCheckbox} 0 104u 100% 22u "$(KimodoAccept)"
  Pop $KimodoLicenseCheckbox
  ${NSD_OnClick} $KimodoLicenseCheckbox KimodoLicenseChanged
  ${If} $InstallKimodo == "1"
    ${NSD_Check} $KimodoCheckbox
  ${EndIf}
  ${If} $AcceptKimodoLicenses == "1"
    ${NSD_Check} $KimodoLicenseCheckbox
  ${EndIf}
  EnableWindow $KimodoLicenseCheckbox $InstallKimodo
  nsDialogs::Show
FunctionEnd

Function SaveKimodoOptions
  ${NSD_GetState} $KimodoCheckbox $InstallKimodo
  ${NSD_GetState} $KimodoLicenseCheckbox $AcceptKimodoLicenses
  ${If} $InstallKimodo == "1"
  ${AndIf} $AcceptKimodoLicenses != "1"
    MessageBox MB_ICONEXCLAMATION "$(KimodoLicenseRequired)" /SD IDOK
    Abort
  ${EndIf}
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
  ${If} $InstallServer == "1"
    ExecShell "open" "$INSTDIR\TRELLIS Studio Tray.exe" "--mode server --start"
  ${Else}
    ExecShell "open" "$INSTDIR\TRELLIS Studio Tray.exe" "--mode desktop --start"
  ${EndIf}
FunctionEnd

!macro ReadStudioState ExtraArguments
  InitPluginsDir
  File /oname=$PLUGINSDIR\check-idle.ps1 "${PAYLOAD_ROOT}\installer\windows\check-idle.ps1"
  nsExec::ExecToStack '"${SETUP_SYSTEM_DIR}\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\check-idle.ps1" -InstallRoot "$INSTDIR" ${ExtraArguments}'
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
  nsExec::ExecToStack '"${SETUP_SYSTEM_DIR}\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\Manage-Shortcuts.ps1" -InstallRoot "$INSTDIR" -Action ${Mode} ${ExtraArguments}'
  Pop $0
  Pop $1
  ${If} $1 != ""
    DetailPrint "$1"
  ${EndIf}
!macroend

!macro WriteSetupMarker Marker Label
  ClearErrors
  FileOpen $0 "$INSTDIR\${Marker}" r
  ${IfNot} ${Errors}
    StrCpy $1 ""
    FileRead $0 $1
    FileClose $0
    StrCmp $1 "${SETUP_NAME}" marker_ready_${Label}
  ${EndIf}
  ClearErrors
  StrCpy $2 "$INSTDIR\${Marker}"
  FileOpen $0 "$2" w
  IfErrors marker_failed
  FileWrite $0 "${SETUP_NAME}"
  IfErrors marker_write_failed
  FileClose $0
  IfErrors marker_close_failed
  marker_ready_${Label}:
!macroend

Section "${SETUP_NAME}"
  Call ValidateInstallFolder
  !insertmacro CheckIdle
  ClearErrors
  SetOutPath "$INSTDIR"
  IfErrors marker_failed
  !insertmacro WriteSetupMarker "${SETUP_MARKER}" owner
!ifdef SETUP_PREVIEW
  !insertmacro WriteSetupMarker "${GUIDED_MARKER}" guided
!endif
  ClearErrors
  !include "${INSTALL_FILES}"
  IfErrors payload_failed
  SetOutPath "$INSTDIR"
  IfErrors payload_failed
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  IfErrors payload_failed
  WriteRegStr HKCU "${SETUP_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${ARP}" "DisplayName" "${SETUP_NAME}"
  WriteRegStr HKCU "${ARP}" "DisplayVersion" "${STUDIO_VERSION}"
  WriteRegStr HKCU "${ARP}" "Publisher" "TRELLIS Studio contributors"
  WriteRegStr HKCU "${ARP}" "DisplayIcon" "$INSTDIR\installer\windows\trellis-studio.ico"
  WriteRegStr HKCU "${ARP}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${ARP}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${ARP}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${ARP}" "NoModify" 1
  WriteRegDWORD HKCU "${ARP}" "NoRepair" 1
!ifndef SETUP_PREVIEW
  IfSilent 0 prepare_shortcuts
  StrCpy $InstallServer "0"
  IfFileExists "$INSTDIR\config\server.json" 0 prepare_shortcuts
  StrCpy $InstallServer "1"
  prepare_shortcuts:
!endif
  !insertmacro ManageShortcuts "Install" "-DesktopShortcut $CreateDesktopShortcut -ServerMode $InstallServer"
  ${If} $0 != "0"
    MessageBox MB_ICONEXCLAMATION "$(ShortcutError)" /SD IDOK
  ${EndIf}
  StrCpy $KimodoArguments ""
  ${If} $InstallKimodo == "1"
  ${AndIf} $AcceptKimodoLicenses == "1"
    StrCpy $KimodoArguments "-WithKimodo -AcceptKimodoModelLicenses"
  ${EndIf}
!ifdef SETUP_PREVIEW
  IfSilent install_done
!else
  IfSilent 0 assemble_selection
  ; Silent reinstalls never open the server configuration dialog.
  StrCmp $InstallServer "1" install_done
  assemble_selection:
!endif
  StrCpy $ComponentsArgument ""
  StrCpy $HardwareArguments ""
  ${If} $InstallTrellis == "1"
    StrCpy $ComponentsArgument "trellis"
    ${If} $InstallNativeWorkflows == "1"
    ${AndIf} $NativeWorkflowsSupported == "1"
      StrCpy $ComponentsArgument "$ComponentsArgument,native-workflows"
    ${EndIf}
  ${EndIf}
  ${If} $InstallPixal3D == "1"
    StrCpy $ComponentsArgument "$ComponentsArgument,pixal3d"
  ${EndIf}
  ${If} $InstallUniRig == "1"
    ${If} $ComponentsArgument == ""
      StrCpy $ComponentsArgument "unirig"
    ${Else}
      StrCpy $ComponentsArgument "$ComponentsArgument,unirig"
    ${EndIf}
  ${EndIf}
  ${If} $InstallHi3d == "1"
    ${If} $ComponentsArgument == ""
      StrCpy $ComponentsArgument "hi3d"
    ${Else}
      StrCpy $ComponentsArgument "$ComponentsArgument,hi3d"
    ${EndIf}
  ${EndIf}
  ${If} $InstallMultiviewTexture == "1"
    ${If} $ComponentsArgument == ""
      StrCpy $ComponentsArgument "multiview-texture"
    ${Else}
      StrCpy $ComponentsArgument "$ComponentsArgument,multiview-texture"
    ${EndIf}
  ${EndIf}
  ${If} $InstallUltraTex == "1"
    ${If} $ComponentsArgument == ""
      StrCpy $ComponentsArgument "ultratex"
    ${Else}
      StrCpy $ComponentsArgument "$ComponentsArgument,ultratex"
    ${EndIf}
  ${EndIf}
  ${If} $ComponentsArgument != ""
    StrCpy $HardwareArguments '-Components "$ComponentsArgument"'
    ${If} $GpuUuid != ""
      StrCpy $HardwareArguments '$HardwareArguments -GpuUuid "$GpuUuid"'
    ${EndIf}
    ${If} $InstallUniRig == "1"
    ${AndIf} $WslDistribution != ""
      StrCpy $HardwareArguments '$HardwareArguments -WslDistribution "$WslDistribution"'
    ${EndIf}
  ${EndIf}
  ${If} $InstallTrellis == "1"
  ${AndIf} $InstallNativeWorkflows == "1"
  ${AndIf} $NativeWorkflowsSupported == "1"
  ${AndIf} $AcceptNativeWorkflowLicenses == "1"
    StrCpy $HardwareArguments "$HardwareArguments -AcceptNativeWorkflowModelLicenses"
  ${ElseIf} $SkipNativeWorkflows == "1"
    StrCpy $HardwareArguments "$HardwareArguments -SkipNativeWorkflows"
  ${EndIf}
  ${If} $InstallHi3d == "1"
  ${AndIf} $AcceptHi3dLicenses == "1"
    StrCpy $HardwareArguments "$HardwareArguments -AcceptHi3dModelLicenses"
  ${ElseIf} $SkipHi3d == "1"
    StrCpy $HardwareArguments "$HardwareArguments -SkipHi3d"
  ${EndIf}
  ${If} $InstallMultiviewTexture == "1"
  ${AndIf} $AcceptMultiviewTextureLicenses == "1"
    StrCpy $HardwareArguments "$HardwareArguments -AcceptMultiviewTextureModelLicenses"
  ${ElseIf} $SkipMultiviewTexture == "1"
    StrCpy $HardwareArguments "$HardwareArguments -SkipMultiviewTexture"
  ${EndIf}
  ${If} $InstallUltraTex == "1"
  ${AndIf} $AcceptUltraTexLicenses == "1"
    StrCpy $HardwareArguments "$HardwareArguments -AcceptUltraTexModelLicenses"
  ${ElseIf} $SkipUltraTex == "1"
    StrCpy $HardwareArguments "$HardwareArguments -SkipUltraTex"
  ${EndIf}
  ${If} $BlenderReady == "1"
  ${AndIf} $BlenderPath != ""
    StrCpy $HardwareArguments '$HardwareArguments -BlenderPath "$BlenderPath"'
  ${EndIf}

  ${If} $InstallServer == "1"
    DetailPrint "$(InstallingServer)"
    StrCpy $1 "ServerSetup"
  ${Else}
    DetailPrint "$(InstallingWorkshop)"
    StrCpy $1 "PrepareWorkshop"
  ${EndIf}
  ClearErrors
  ExecWait '"${SETUP_SYSTEM_DIR}\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\installer\windows\Launch-Installed.ps1" -Mode $1 -Guided -NoBrowser $HardwareArguments $KimodoArguments' $0
  IfErrors setup_failed
  ${If} $0 == "3"
    DetailPrint "$(ComponentsNeedAttention)"
    MessageBox MB_ICONEXCLAMATION "$(ComponentsNeedAttention)" /SD IDOK
    SetErrorLevel 3
  ${ElseIf} $0 != "0"
    Goto setup_failed
  ${EndIf}
  Goto install_done

  setup_failed:
    ${If} $InstallServer == "1"
      DetailPrint "$(ServerFailed)"
      MessageBox MB_ICONEXCLAMATION "$(ServerFailed)" /SD IDOK
    ${Else}
      DetailPrint "$(WorkshopFailed)"
      MessageBox MB_ICONEXCLAMATION "$(WorkshopFailed)" /SD IDOK
    ${EndIf}
    SetErrorLevel 3
    Abort
  marker_write_failed:
    FileClose $0
  marker_close_failed:
    Delete "$2"
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
  System::Call 'kernel32::SetEnvironmentVariableW(w "PSModulePath", p 0)'
  StrCpy $LANGUAGE ${LANG_ENGLISH}
  IfFileExists "$INSTDIR\.git" foreign
  IfFileExists "$INSTDIR\.git\*.*" foreign
!ifndef SETUP_PREVIEW
  IfFileExists "$INSTDIR\${PREVIEW_MARKER}" foreign
!endif
  ClearErrors
  FileOpen $0 "$INSTDIR\${SETUP_MARKER}" r
  IfErrors foreign
  StrCpy $1 ""
  FileRead $0 $1
  FileClose $0
  StrCmp $1 "${SETUP_NAME}" allowed
  foreign:
    MessageBox MB_ICONSTOP "$(DifferentUninstall)" /SD IDOK
    SetErrorLevel 2
    Abort
  allowed:
    ClearErrors
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
  ReadRegStr $0 HKCU "${SETUP_KEY}" "InstallDir"
  ${If} $0 == "$INSTDIR"
    DeleteRegKey HKCU "${ARP}"
    DeleteRegKey HKCU "${SETUP_KEY}"
  ${EndIf}
SectionEnd
