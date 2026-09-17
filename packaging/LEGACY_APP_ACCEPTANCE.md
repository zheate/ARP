# 旧 NI 电脑：应用安装和启动试验

这是发布工程试装包，不是最终全驱动安装 EXE。适用于本次已经完成
`runtime-compatibility.json` 检查的备用电脑。检测结果为 DAQmx 9.7.0
加载成功、内置串口组件加载成功；实际设备采集和原 LabVIEW 程序仍未验收。

## 包内文件

- `PowerTest-Application.msi`：Power Test 0.1.2 应用，包括本次检查的 Python sidecar。
- `MicrosoftEdgeWebView2RuntimeInstallerX64.exe`：WebView2 完整离线安装器。
- `SHA256SUMS.txt`：上述文件的校验值。

包内没有 NI 安装程序；本轮只安装应用和 WebView2，不升级或卸载 NI。
CH341、Ocean、Ophir、Coherent、FTDI 等驱动未包含在这个试装包中。

## 操作步骤

1. 将整个 ZIP 复制到备用电脑，解压到本地目录。断开网络后试装。
   如果原 LabVIEW 程序能找到，先记录它是否可以正常使用；不清楚就记录“待确认”。
2. 右键 `MicrosoftEdgeWebView2RuntimeInstallerX64.exe`，以管理员身份运行，
   等待安装完成。若失败，记录完整错误内容，先不要继续应用启动测试。
3. 安装应用并指定路径：在解压目录的文件资源管理器地址栏输入 `cmd` 回车，
   执行下面一行（D 盘必须存在，也可改成其他本地目录）：

   ```bat
   msiexec.exe /i "%CD%\PowerTest-Application.msi" INSTALLDIR="D:\Apps\Power Test" /norestart /L*v "%CD%\PowerTest-install.log"
   ```

   按安装界面完成操作。若电脑已经安装同版本 Power Test，要改变路径时，
   先在 Windows 应用列表中仅卸载 Power Test；不要卸载 NI、LabVIEW 或共享运行库。
4. 安装完成后，检查实际目录中是否存在 `tauri-ui.exe` 和 `arp-python.exe`。
   路径与指定目录不一致时记录失败，不以安装界面完成代替路径验证。
5. 打开 Power Test，确认主界面能够显示且无后端启动错误，然后关闭。
   本轮不点击“识别”“开始采集”或电源输出控制，不要求连接仪器。
6. 如原 LabVIEW 程序可用，再检查一次原程序；否则继续标记“待确认”。

## 返回结果

返回 `PowerTest-install.log`，并说明：WebView2 是否成功、Power Test 是否
能打开、实际安装目录、原程序状态（正常 / 异常 / 待确认）。出现错误时
保留完整提示或截图。没有采集卡或不确定旧程序时，不需要猜测验收结果。

这些结果用于推进最终安装器验收，不代表所有驱动、硬件、升级、修复、
重启及网络行为已经通过验证。
