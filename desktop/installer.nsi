; ============================================================
;  动漫下载机 Anime Downloader —— NSIS 安装包脚本
;
;  特点：
;    * **按用户安装**（$LOCALAPPDATA\Programs\...），不需要管理员权限，
;      也避免装到 Program Files 后写不了数据目录的问题
;    * 自带卸载程序，注册到「应用和功能」
;    * 卸载时询问是否一并删除用户数据（数据库/设置/日志）
;
;  构建：makensis installer.nsi
;  输出：dist/desktop/动漫下载机-<版本>-Setup.exe
; ============================================================
Unicode true
SetCompressor /SOLID lzma

!define APP_NAME        "动漫下载机"
!define APP_NAME_EN     "Anime Downloader"
!define APP_VERSION     "3.1.0"
!define APP_EXE         "AnimeDownloader.exe"
; 后端 exe 也要结束，否则覆盖 backend\ 下的文件会失败
!define BACKEND_EXE     "动漫下载机.exe"
!define APP_PUBLISHER   "Anime Downloader"
!define APP_ID          "AnimeDownloader"
!define UNINST_KEY      "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}"

; PyInstaller 的 onedir 产物目录（相对本文件）
!define SRCDIR          "..\dist\desktop\动漫下载机"
; 输出到 dist/desktop/
!define OUTFILE         "..\dist\desktop\${APP_NAME}-${APP_VERSION}-Setup.exe"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\${APP_ID}"
InstallDirRegKey HKCU "Software\${APP_ID}" "InstallDir"
RequestExecutionLevel user      ; 按用户安装，不弹 UAC
BrandingText "${APP_NAME_EN} ${APP_VERSION}"

; ---------------- 图标 ----------------
; app.ico 由 desktop\make_icon.py 生成（多尺寸：16~256）
; Icon / UninstallIcon 决定 setup.exe Uninstall.exe 的文件图标；
; MUI_ICON / MUI_UNICON 是安装向导里显示的图标。
Icon "app.ico"
UninstallIcon "app.ico"

; ---------------- 界面 ----------------
!include "MUI2.nsh"
!define MUI_ABORTWARNING
!define MUI_ICON "app.ico"
!define MUI_UNICON "app.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动 ${APP_NAME}"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; ---------------- 安装 ----------------
Section "主程序" SecMain
  SectionIn RO

  ; 先结束正在运行的实例，否则文件被占用、覆盖会失败
  nsExec::Exec 'taskkill /IM "${APP_EXE}" /F'
  nsExec::Exec 'taskkill /IM "${BACKEND_EXE}" /F'
  Sleep 600

  SetOutPath "$INSTDIR"
  File /r "${SRCDIR}\*.*"

  ; 记录安装目录，供卸载和升级用
  WriteRegStr HKCU "Software\${APP_ID}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\${APP_ID}" "Version" "${APP_VERSION}"

  ; 卸载程序 + 「应用和功能」条目
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayName"     "${APP_NAME} ${APP_NAME_EN}"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayVersion"  "${APP_VERSION}"
  WriteRegStr   HKCU "${UNINST_KEY}" "Publisher"       "${APP_PUBLISHER}"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayIcon"     "$INSTDIR\${APP_EXE}"
  WriteRegStr   HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr   HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr   HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" 716800   ; KB ≈ 700MB

  ; 快捷方式
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe"
SectionEnd

; ---------------- 卸载 ----------------
Section "Uninstall"
  nsExec::Exec 'taskkill /IM "${APP_EXE}" /F'
  nsExec::Exec 'taskkill /IM "${BACKEND_EXE}" /F'
  Sleep 600

  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk"
  RMDir  "$SMPROGRAMS\${APP_NAME}"

  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\${APP_ID}"

  ; 程序本体整个删掉
  RMDir /r "$INSTDIR"

  ; 用户数据单独问一句 —— 里面是数据库、设置、下载记录，删了就没了
  MessageBox MB_YESNO|MB_ICONQUESTION \
    "是否一并删除用户数据？$\r$\n$\r$\n包含：数据库、设置、检索历史、日志。$\r$\n已下载的视频**不在**里面，不会被删。$\r$\n$\r$\n位置：$LOCALAPPDATA\${APP_ID}" \
    /SD IDNO IDNO SkipData
    RMDir /r "$LOCALAPPDATA\${APP_ID}"
  SkipData:
SectionEnd

; ---------------- 版本信息 ----------------
VIProductVersion "3.1.0.0"
VIAddVersionKey "ProductName"     "${APP_NAME_EN}"
VIAddVersionKey "FileDescription" "${APP_NAME} 安装程序"
VIAddVersionKey "FileVersion"     "${APP_VERSION}"
VIAddVersionKey "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey "LegalCopyright"  "${APP_PUBLISHER}"
