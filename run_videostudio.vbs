' VideoStudio Pro — Resilient Background Launcher
Option Explicit

Dim WshShell, fso, q, appDir, py, logPath, cmdLine, i, candidates, cand
q = Chr(34)
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
If Not fso.FileExists(appDir & "\app.py") Then
    candidates = Array( _
        "C:\Users\chkam\OneDrive\Desktop\BrandFinder\VideoStudio", _
        "C:\Users\chkam\OneDrive\Desktop\VideoStudio", _
        "C:\Users\chkam\Desktop\BrandFinder\VideoStudio" _
    )
    For Each cand In candidates
        If fso.FileExists(cand & "\app.py") Then
            appDir = cand
            Exit For
        End If
    Next
End If

logPath = appDir & "\outputs\logs\launch.log"
WshShell.CurrentDirectory = appDir

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

py = "C:\Users\chkam\AppData\Local\Programs\Python\Python314\python.exe"
If Not fso.FileExists(py) Then py = "python.exe"

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
