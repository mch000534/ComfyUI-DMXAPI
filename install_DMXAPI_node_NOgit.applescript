-- ComfyUI-DMXAPI 安裝啟動器
-- 請雙擊「install_DMXAPI_node_NOgit.app」。
-- 若用「腳本編輯器」開啟本檔，請按工具列的 ▶ 執行。
-- 這個啟動器用「zsh 腳本路徑」執行，不需要 .command 先有執行權限。

property commandName : "install_DMXAPI_node_NOgit.command"
property commandURL : "https://raw.githubusercontent.com/mch000534/ComfyUI-DMXAPI/main/install_DMXAPI_node_NOgit.command"

on run
	my launchInstall()
end run

on open droppedItems
	try
		set theItem to item 1 of droppedItems
		set posixPath to POSIX path of theItem
		if posixPath ends with "/" then set posixPath to text 1 thru -2 of posixPath
		if posixPath ends with ".command" then
			my repairAndLaunch(posixPath)
		else
			my launchInstall()
		end if
	on error errMsg
		display dialog "無法啟動安裝：" & return & errMsg buttons {"確定"} default button 1 with icon stop
	end try
end open

on launchInstall()
	try
		set installerPath to my locateInstaller()
		my repairAndLaunch(installerPath)
	on error errMsg
		display dialog "無法啟動安裝：" & return & errMsg buttons {"確定"} default button 1 with icon stop
	end try
end launchInstall

on locateInstaller()
	set candidates to {}
	
	try
		set end of candidates to my siblingOfMe()
	end try
	
	try
		set end of candidates to my siblingOfScriptEditorDocument()
	end try
	
	set end of candidates to (POSIX path of (path to downloads folder)) & commandName
	set end of candidates to (POSIX path of (path to desktop folder)) & commandName
	
	repeat with cand in candidates
		set candPath to cand as string
		if my posixFileExists(candPath) then return candPath
	end repeat
	
	return my downloadInstaller()
end locateInstaller

on siblingOfMe()
	set mePath to POSIX path of (path to me)
	if mePath ends with "/" then set mePath to text 1 thru -2 of mePath
	set parentPath to do shell script "/usr/bin/dirname " & quoted form of mePath
	return parentPath & "/" & commandName
end siblingOfMe

on siblingOfScriptEditorDocument()
	tell application "Script Editor"
		if (count of documents) is 0 then error "no document"
		set docPath to POSIX path of (path of front document as alias)
	end tell
	set parentPath to do shell script "/usr/bin/dirname " & quoted form of docPath
	return parentPath & "/" & commandName
end siblingOfScriptEditorDocument

on posixFileExists(posixPath)
	try
		do shell script "/bin/test -f " & quoted form of posixPath
		return true
	on error
		return false
	end try
end posixFileExists

on downloadInstaller()
	set tmpPath to do shell script "/usr/bin/mktemp /tmp/dmxapi-install.XXXXXX.command"
	try
		do shell script "/usr/bin/curl -fL --retry 3 --connect-timeout 20 --silent --show-error -o " & quoted form of tmpPath & " " & quoted form of commandURL
	on error errMsg
		error "找不到 " & commandName & "，也無法從 GitHub 下載。" & return & "請把安裝檔放到「下載」資料夾，或檢查網路。" & return & errMsg
	end try
	return tmpPath
end downloadInstaller

on repairAndLaunch(installerPath)
	do shell script "/bin/chmod +x " & quoted form of installerPath
	try
		do shell script "/usr/bin/xattr -cr " & quoted form of installerPath
	end try
	
	tell application "Terminal"
		activate
		do script "exec /bin/zsh " & quoted form of installerPath
	end tell
end repairAndLaunch
