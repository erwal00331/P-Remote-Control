import subprocess
import threading
import logging
import base64

logger = logging.getLogger(__name__)

class NotificationManager:
    """Windows 通知管理器 (使用 PowerShell)"""
    
    @staticmethod
    def show_toast(title: str, message: str):
        """显示 Windows Toast 通知"""
        def _run_toast():
            try:
                # 转义 PowerShell 字符串中的特殊字符
                safe_title = title.replace("'", "''").replace('"', '`"')
                safe_message = message.replace("'", "''").replace('"', '`"')
                
                # PowerShell 脚本
                ps_script = f"""
                [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
                [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] | Out-Null
                $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
                $textNodes = $template.GetElementsByTagName("text")
                $textNodes.Item(0).AppendChild($template.CreateTextNode('{safe_title}')) | Out-Null
                $textNodes.Item(1).AppendChild($template.CreateTextNode('{safe_message}')) | Out-Null
                # 注意：AppId 需要尽可能唯一，或者使用系统的 explorer
                $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("AI Agent")
                $notifier.Show($template)
                """
                
                # 使用 Base64 编码方式运行，避免命令行引号逃逸问题
                encoded_command = base64.b64encode(ps_script.encode('utf-16le')).decode('ascii')
                
                # 使用 CREATE_NO_WINDOW 避免弹出黑框
                creationflags = 0x08000000 # CREATE_NO_WINDOW
                
                subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_command],
                    creationflags=creationflags,
                    check=False
                )
            except Exception as e:
                logger.error(f"发送通知失败: {e}")

        # 在新线程中运行，避免阻塞
        threading.Thread(target=_run_toast, daemon=True).start()

# 全局实例
notification_manager = NotificationManager()
