' ===========================================================================
' VideoStudio Pro — robust one-click launcher (no console window).
' Behaviour:
'   1. If the studio is already running, just open the browser.
'   2. Otherwise start it (hidden), capturing all output to outputs\logs\launch.log
'   3. Wait until the server is GENUINELY ready (first boot can be slow), then
'      open the browser exactly once.
'   4. If it never comes up, pop a message box pointing at the error log.
' This replaces the old version that used pythonw on PATH and opened the browser
' after a fixed 10s — which raced the server and hid all startup errors.
' ===========================================================================
Option Explicit

Dim WshShell, fso, q, appDir, py, logPath, cmdLine, i
q = Chr(34)
appDir = "C:\Users\chkam\OneDrive\Desktop\BrandFinder\VideoStudio"
logPath = appDir & "\outputs\logs\launch.log"

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
WshShell.CurrentDirectory = appDir

' ---- helper: is the server answering on 7860? -----------------------------
Function ServerUp()
  Dim h
  ServerUp = False
  On Error Resume Next
  Set h = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  h.setTimeouts 1500, 1500, 1500, 1500
  h.Open "GET", "http://127.0.0.1:7860/", False
  h.Send
  If Err.Number = 0 And h.Status = 200 Then ServerUp = True
  On Error GoTo 0
End Function

' ---- 1) already running -> open browser and stop ---------------------------
If ServerUp() Then
  WshShell.Run "http://127.0.0.1:7860/", 1, False
  WScript.Quit
End If

' ---- 2) resolve a real Python executable (no PATH dependency) ---------------
py = "C:\Users\chkam\AppData\Local\Programs\Python\Python314\python.exe"
If Not fso.FileExists(py) Then py = "python.exe"   ' fall back to PATH

' ensure the log folder exists
If Not fso.FolderExists(appDir & "\outputs") Then fso.CreateFolder(appDir & "\outputs")
If Not fso.FolderExists(appDir & "\outputs\logs") Then fso.CreateFolder(appDir & "\outputs\logs")

' tell app.py that WE will open the browser once it is truly ready
WshShell.Environment("PROCESS")("VS_MANAGED_LAUNCH") = "1"

' ---- 3) start the server hidden, capturing output to the log ---------------
cmdLine = "cmd /c " & q & q & py & q & " app.py > " & q & logPath & q & " 2>&1" & q
WshShell.Run cmdLine, 0, False

' ---- 4) wait for genuine readiness (up to ~150s: first-run imports are slow)
For i = 1 To 300
  WScript.Sleep 500
  If ServerUp() Then Exit For
Next

' ---- 5) open the browser when ready, else surface the error log ------------
If ServerUp() Then
  WshShell.Run "http://127.0.0.1:7860/", 1, False
Else
  MsgBox "VideoStudio Pro did not start within ~2.5 minutes." & vbCrLf & vbCrLf & _
         "Open this log to see what went wrong:" & vbCrLf & logPath, _
         vbExclamation, "VideoStudio Pro — startup problem"
End If
