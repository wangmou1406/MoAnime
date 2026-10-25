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
!define APP_VERSION     "3.2.1"
!define APP_EXE         "AnimeDownloader.exe"
; 后端 exe 也要结束，否则覆盖 backend\ 下的文件会失败
!define BACKEND_EXE     "动漫下载机.exe"
!define APP_PUBLISHER   "Anime Downloader"
!define APP_ID          "AnimeDownloader"
!define UNINST_KEY      "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}"

; PyInstaller 的 onedir 产物目录（相对本文件）
!define SRCDIR          "dist\动漫下载机"
; 输出到 dist/desktop/
!define OUTFILE         "dist\${APP_NAME}-${APP_VERSION}-Setup.exe"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "${OUTFILE}"

; 默认装到 C:\Program Files\AnimeDownloader（用户要求）。
; 装 Program Files 必须提权，所以这里要 admin —— 之前是 user（按用户装到
; %LOCALAPPDATA%\Programs），那个目录普通进程能写，但用户想装到 Program Files
; 就得提升权限，否则 CreateDirectory 直接失败。
;
; 用户数据不受影响：程序把数据库/设置/日志放在 %LOCALAPPDATA%\AnimeDownloader\data，
; 安装目录里不写任何运行数据，所以提权安装也不会造成"后面跑起来写不了"的问题。
InstallDir "$PROGRAMFILES64\${APP_ID}"
InstallDirRegKey HKLM "Software\${APP_ID}" "InstallDir"
RequestExecutionLevel admin
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

  ; 先结束正在运行的实例，否则文件被占用、覆盖会失败。
  ; 注意：nsExec::Exec 会把退出码**压栈**，不 Pop 掉会一直堆在栈上；
  ; 而且它失败时会置错误标志，影响后面命令的判断。所以逐个 Pop 掉。
  nsExec::Exec 'taskkill /IM "${APP_EXE}" /F'
  Pop $0
  nsExec::Exec 'taskkill /IM "${BACKEND_EXE}" /F'
  Pop $0
  Sleep 600

  ; ---------------------------------------------------------------- 目标可写性检查
  ; 目的：把「写不进去」变成一句人话，而不是让用户在几十个文件之后突然收到
  ; "无法打开要写入的文件：...\Accessibility.dll"（那是按字母序的第一个文件，
  ; 出现在它身上只说明「一个字节都没写进去」）。
  ;
  ; 两个坑都踩过，所以写法很保守：
  ;   1. 不用 `IfErrors 0 +N` 相对跳转 —— 极易算错一格，会让「成功」走失败分支
  ;      （之前就因此谎报过"无法创建安装目录"）。全部改成标签。
  ;   2. **不检查 CreateDirectory 的错误标志** —— 前面 taskkill 失败留下的
  ;      旧错误标志会污染判断。直接清错误标志后用 FileOpen 探测：
  ;      目录建不出来的话，FileOpen 同样会失败，一种检查覆盖两种情况。
  ClearErrors
  CreateDirectory "$INSTDIR"
  ClearErrors
  FileOpen $0 "$INSTDIR\.write-test" w
  IfErrors WriteFail WriteOk

  WriteOk:
  FileWrite $0 "ok"
  FileClose $0
  Delete "$INSTDIR\.write-test"

  ; 清掉旧版本残留：避免旧文件是只读的/被锁住，导致 File 写不进去。
  ; 用户数据不在这里（在 %LOCALAPPDATA%\AnimeDownloader\data），所以放心清。
  RMDir /r "$INSTDIR"
  CreateDirectory "$INSTDIR"
  SetOutPath "$INSTDIR"

  ; 排除运行期产物：data\（数据库/设置/日志）和 webview2\（浏览器缓存）。
  ; 它们是程序第一次运行时自己建的，不能打进安装包 ——
  ; 否则会把开发机上的一份配置带给所有用户，而且程序正在跑时
  ; SQLite 被占用，NSIS 直接 "failed opening file ...db" 构建失败（实测踩过）。
  File /r /x "data\*" /x "webview2\*" "${SRCDIR}\*.*"
  Goto InstallDone

  WriteFail:
    MessageBox MB_ICONSTOP \
      "无法写入安装目录：$\r$\n$INSTDIR$\r$\n$\r$\n常见原因：$\r$\n  · 没有以管理员身份运行（Program Files 需要提权）$\r$\n  · 安全软件 / 受控文件夹访问 拦截了写入$\r$\n  · 该目录被设为只读，或属于其他用户$\r$\n$\r$\n请点「上一步」换一个目录（例如 D:\AnimeDownloader），或者改用免安装版。"
    Goto AbortInstall

  AbortInstall:
    SetErrorLevel 1
    Quit

  InstallDone:

  ; 记录安装目录，供卸载和升级用（整机安装 → 写 HKLM，卸载时同样需要提权）
  WriteRegStr HKLM "Software\${APP_ID}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\${APP_ID}" "Version" "${APP_VERSION}"

  ; 卸载程序 + 「应用和功能」条目
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr   HKLM "${UNINST_KEY}" "DisplayName"     "${APP_NAME} ${APP_NAME_EN}"
  WriteRegStr   HKLM "${UNINST_KEY}" "DisplayVersion"  "${APP_VERSION}"
  WriteRegStr   HKLM "${UNINST_KEY}" "Publisher"       "${APP_PUBLISHER}"
  WriteRegStr   HKLM "${UNINST_KEY}" "DisplayIcon"     "$INSTDIR\${APP_EXE}"
  WriteRegStr   HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr   HKLM "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr   HKLM "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "EstimatedSize" 716800   ; KB ≈ 700MB

  ; 快捷方式：整机安装 → 放在「所有用户」的桌面和开始菜单（SetShellVarContext all），
  ; 这样任何账户都能看到。如果保持 current，提权后 $DESKTOP 会指向提权账户的桌面，
  ; 用另一个管理员账户安装时快捷方式就会跑到别人桌面上。
  SetShellVarContext all
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe"
  SetShellVarContext current
SectionEnd

; ---------------- 卸载 ----------------
Section "Uninstall"
  nsExec::Exec 'taskkill /IM "${APP_EXE}" /F'
  Pop $0
  nsExec::Exec 'taskkill /IM "${BACKEND_EXE}" /F'
  Pop $0
  Sleep 600

  SetShellVarContext all
  Delete "$DESKTOP\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk"
  RMDir  "$SMPROGRAMS\${APP_NAME}"
  SetShellVarContext current

  DeleteRegKey HKLM "${UNINST_KEY}"
  DeleteRegKey HKLM "Software\${APP_ID}"

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
VIProductVersion "3.2.1.0"
VIAddVersionKey "ProductName"     "${APP_NAME_EN}"
VIAddVersionKey "FileDescription" "${APP_NAME} 安装程序"
VIAddVersionKey "FileVersion"     "${APP_VERSION}"
VIAddVersionKey "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey "LegalCopyright"  "${APP_PUBLISHER}"
