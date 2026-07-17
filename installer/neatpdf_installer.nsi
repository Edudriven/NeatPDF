; installer/neatpdf_installer.nsi
; NSIS installer script for NeatPDF (Windows)
;
; Prerequisites:
;   - NSIS 3.x installed
;   - PyInstaller dist/NeatPDF/ folder exists
;
; Build:
;   makensis installer/neatpdf_installer.nsi
;
; Produces:
;   installer/NeatPDF-<version>-windows-installer.exe

!include "MUI2.nsh"
!include "FileFunc.nsh"

; ── Metadata ──────────────────────────────────────────────────────────────────
!define APP_NAME        "NeatPDF"
!define APP_VERSION     "0.1.0"
!define APP_PUBLISHER   "Edudriven"
!define APP_URL         "https://github.com/Edudriven/NeatPDF"
!define APP_EXE         "NeatPDF.exe"
!define INSTALL_DIR     "$PROGRAMFILES64\${APP_NAME}"
!define UNINSTALL_KEY   "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
!define MUI_ICON        "..\resources\icons\neatpdf.ico"
!define MUI_UNICON      "..\resources\icons\neatpdf.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "..\resources\icons\neatpdf_logo.png"
!define MUI_HEADERIMAGE_RIGHT

; ── General ───────────────────────────────────────────────────────────────────
Name            "${APP_NAME} ${APP_VERSION}"
OutFile         "NeatPDF-${APP_VERSION}-windows-installer.exe"
InstallDir      "${INSTALL_DIR}"
InstallDirRegKey HKLM "${UNINSTALL_KEY}" "InstallLocation"
RequestExecutionLevel admin
SetCompressor   /SOLID lzma
ShowInstDetails show

; ── Pages ─────────────────────────────────────────────────────────────────────
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ── Install section ───────────────────────────────────────────────────────────
Section "NeatPDF (required)" SecMain
    SectionIn RO

    SetOutPath "$INSTDIR"
    File /r "..\dist\NeatPDF\*.*"

    ; Write install mode sentinel for in-app updater
    FileOpen  $0 "$INSTDIR\install_mode.txt" w
    FileWrite $0 "installer"
    FileClose $0

    ; Start Menu shortcut
    CreateDirectory "$SMPROGRAMS\${APP_NAME}"
    CreateShortcut  "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" \
                    "$INSTDIR\${APP_EXE}" "" \
                    "$INSTDIR\${APP_EXE}" 0

    ; Desktop shortcut
    CreateShortcut  "$DESKTOP\${APP_NAME}.lnk" \
                    "$INSTDIR\${APP_EXE}" "" \
                    "$INSTDIR\${APP_EXE}" 0

    ; Uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    ; Add/Remove Programs entry
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "DisplayName"      "${APP_NAME}"
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "DisplayVersion"   "${APP_VERSION}"
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "Publisher"        "${APP_PUBLISHER}"
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "URLInfoAbout"     "${APP_URL}"
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "InstallLocation"  "$INSTDIR"
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "UninstallString"  "$INSTDIR\Uninstall.exe"
    WriteRegStr   HKLM "${UNINSTALL_KEY}" "DisplayIcon"      "$INSTDIR\${APP_EXE}"
    WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoModify"         1
    WriteRegDWORD HKLM "${UNINSTALL_KEY}" "NoRepair"         1

    ; Estimate install size
    ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
    IntFmt $0 "0x%08X" $0
    WriteRegDWORD HKLM "${UNINSTALL_KEY}" "EstimatedSize" "$0"
SectionEnd

; ── Uninstall section ─────────────────────────────────────────────────────────
Section "Uninstall"
    ; Remove shortcuts
    Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
    RMDir  "$SMPROGRAMS\${APP_NAME}"
    Delete "$DESKTOP\${APP_NAME}.lnk"

    ; Remove installed files
    RMDir /r "$INSTDIR"

    ; Remove registry entries
    DeleteRegKey HKLM "${UNINSTALL_KEY}"
SectionEnd
