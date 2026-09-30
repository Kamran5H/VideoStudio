' VideoStudio Pro — Resilient Background Launcher
Option Explicit

Dim WshShell, fso, q, appDir, py, logPath, cmdLine, i
q = Chr(34)
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
If Not fso.FileExists(appDir & "\app.py") Then
    MsgBox "VideoStudio app.py was not found next to this launcher:" & vbCrLf & appDir, _
           vbExclamation, "VideoStudio Pro"
    WScript.Quit 1
End If

logPath = appDir & "\outputs\logs\launch.log"
WshShell.CurrentDirectory = appDir

' Clean stale lock if present
On Error Resume Next
If fso.FileExists(appDir & "\outputs\queue\worker.lock") Then
    fso.DeleteFile appDir & "\outputs\queue\worker.lock", True
End If
On Error GoTo 0

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

If ServerUp() Then
  WshShell.Run "http://127.0.0.1:7860/", 1, False
  WScript.Quit
End If

' Resolve Python executable
py = ""
If fso.FileExists(appDir & "\..\.venv\python.exe") Then
    py = appDir & "\..\.venv\python.exe"
ElseIf fso.FileExists(appDir & "\..\.venv\Scripts\python.exe") Then
    py = appDir & "\..\.venv\Scripts\python.exe"
ElseIf fso.FileExists(appDir & "\.venv\Scripts\python.exe") Then
    py = appDir & "\.venv\Scripts\python.exe"
ElseIf fso.FileExists(appDir & "\.venv\python.exe") Then
    py = appDir & "\.venv\python.exe"
Else
    py = "python.exe"
End If

If Not fso.FolderExists(appDir & "\outputs") Then fso.CreateFolder(appDir & "\outputs")
If Not fso.FolderExists(appDir & "\outputs\logs") Then fso.CreateFolder(appDir & "\outputs\logs")

WshShell.Environment("PROCESS")("VS_MANAGED_LAUNCH") = "1"

cmdLine = "cmd /c " & q & q & py & q & " app.py > " & q & logPath & q & " 2>&1" & q
WshShell.Run cmdLine, 0, False

For i = 1 To 120        ' up to 60s
  WScript.Sleep 500
  If ServerUp() Then Exit For
Next

If ServerUp() Then
  WshShell.Run "http://127.0.0.1:7860/", 1, False
Else
  MsgBox "VideoStudio Pro did not start in time." & vbCrLf & vbCrLf & _
         "Check log: " & logPath, vbExclamation, "VideoStudio Pro"
End If
