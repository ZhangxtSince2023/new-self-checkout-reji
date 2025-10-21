import requests
import json
import time
from datetime import datetime
from pathlib import Path
import paho.mqtt.client as mqtt


class DeviceNotifier:
    """处理向设备发送状态变更通知"""
    
    def __init__(self, config_loader, logger=None):
        self.config_loader = config_loader
        self.logger = logger
        self.timeout = 5  # HTTP请求超时时间（秒）
        self.log_dir = Path("log")
        self.log_dir.mkdir(exist_ok=True)
        
    def notify_device(self, device_id: str, code: int, message: str) -> bool:
        """
        向设备发送状态通知
        
        Args:
            device_id: 设备ID
            code: 命令代码
            message: 消息内容
            
        Returns:
            bool: 是否发送成功
        """
        try:
            # 获取设备配置
            devices_config = self.config_loader.config.get('device', {})
            device_config = devices_config.get(device_id)
            
            if not device_config:
                print(f"[DeviceNotifier] Device {device_id} not found in config")
                return False
            
            # 获取设备IP
            host_ip = device_config.get('hostip')
            if not host_ip:
                print(f"[DeviceNotifier] No hostip found for device {device_id}")
                return False
            
            # 构建请求URL（使用端口9999）
            url = f"http://{host_ip}:9999/selfregistration/"
            
            # 构建请求体
            payload = {
                "code": code,
                "message": message
            }
            
            # 构建请求头
            headers = {
                "Content-Type": "application/json",
                "X-Device-ID": device_id
            }
            
            # 发送请求
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout
            )
            
            # 记录到日志
            self._log_notification(device_id, code, message, response.status_code, response.text)
            
            if response.status_code == 200:
                print(f"[DeviceNotifier] Successfully notified {device_id} with code {code}")
                return True
            else:
                print(f"[DeviceNotifier] Failed to notify {device_id}: HTTP {response.status_code}")
                return False
                
        except requests.exceptions.Timeout:
            print(f"[DeviceNotifier] Timeout notifying device {device_id}")
            self._log_notification(device_id, code, message, 0, "Timeout")
            return False
        except requests.exceptions.ConnectionError:
            print(f"[DeviceNotifier] Connection error notifying device {device_id}")
            self._log_notification(device_id, code, message, 0, "Connection Error")
            return False
        except Exception as e:
            print(f"[DeviceNotifier] Error notifying device {device_id}: {e}")
            self._log_notification(device_id, code, message, 0, str(e))
            return False
    
    def _log_notification(self, device_id: str, code: int, message: str, status_code: int, response: str):
        """记录通知到日志文件"""
        timestamp = datetime.now()
        
        # 如果有外部logger，使用它
        if self.logger:
            log_data = {
                "timestamp": timestamp.timestamp(),
                "type": "device_notification",
                "code": code,
                "message": message,
                "status_code": status_code,
                "response": response
            }
            self.logger.log(device_id, log_data)
        
        # 同时写入专门的通知日志文件
        log_entry = {
            "timestamp": timestamp.isoformat(),
            "device_id": device_id,
            "type": "notification_sent",
            "code": code,
            "message": message,
            "status_code": status_code,
            "response": response
        }
        
        today = timestamp.strftime("%Y-%m-%d")
        notification_log_file = self.log_dir / f"{today}_notifications.log"
        
        try:
            with open(notification_log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except Exception as e:
            print(f"Error writing to notification log: {e}")
    
    def send_session_start(self, device_id: str) -> bool:
        """发送会计开始通知（code 101）"""
        return self.notify_device(
            device_id,
            101,
            "スキャンチェック開始"
        )
    
    def send_session_end(self, device_id: str) -> bool:
        """发送会计结束通知（code 106）"""
        return self.notify_device(
            device_id,
            106,
            "スキャンチェック終了"
        )
    
    def send_product_scan(self, device_id: str) -> bool:
        """发送扫描商品通知（code 102）"""
        return self.notify_device(
            device_id,
            102,
            "商品スキャン"
        )

    def send_mqtt_dismiss(self, device_id: str) -> bool:
        """
        向设备发送MQTT dismiss命令

        Args:
            device_id: 设备ID

        Returns:
            bool: 是否发送成功
        """
        try:
            # 获取设备配置
            devices_config = self.config_loader.config.get('device', {})
            device_config = devices_config.get(device_id)

            if not device_config:
                print(f"[DeviceNotifier] Device {device_id} not found in config")
                return False

            # 获取目标设备ID
            target_device_id = device_config.get('target_device_id')
            if not target_device_id:
                print(f"[DeviceNotifier] No target_device_id found for device {device_id}")
                return False

            # 获取MQTT配置
            mqtt_config = self.config_loader.config.get('alter_neko', {})
            mqtt_broker = mqtt_config.get('mqtt_broker')
            mqtt_port = mqtt_config.get('mqtt_port', 1883)
            mqtt_topic = mqtt_config.get('mqtt_topic', 'display/alert')

            if not mqtt_broker:
                print(f"[DeviceNotifier] No MQTT broker configured")
                return False

            # 创建MQTT客户端
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"reji_{device_id}")

            try:
                # 连接到MQTT broker
                client.connect(mqtt_broker, mqtt_port, 60)

                # 构建消息
                message = {
                    "text": "",
                    "command": "dismiss",
                    "targetDeviceId": target_device_id,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ")
                }

                # 发送消息
                result = client.publish(mqtt_topic, json.dumps(message, ensure_ascii=False))

                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    print(f"[DeviceNotifier] ✅ Dismiss 命令已发送到 {target_device_id}")
                    self._log_mqtt_dismiss(device_id, target_device_id, "Success")
                    return True
                else:
                    print(f"[DeviceNotifier] ❌ Dismiss 命令发送失败: {result.rc}")
                    self._log_mqtt_dismiss(device_id, target_device_id, f"Failed: {result.rc}")
                    return False

            finally:
                client.disconnect()

        except Exception as e:
            print(f"[DeviceNotifier] Error sending MQTT dismiss: {e}")
            self._log_mqtt_dismiss(device_id, target_device_id if 'target_device_id' in locals() else "unknown", str(e))
            return False

    def _log_mqtt_dismiss(self, device_id: str, target_device_id: str, status: str):
        """记录MQTT dismiss命令到日志文件"""
        timestamp = datetime.now()

        # 如果有外部logger，使用它
        if self.logger:
            log_data = {
                "timestamp": timestamp.timestamp(),
                "type": "mqtt_dismiss",
                "target_device_id": target_device_id,
                "status": status
            }
            self.logger.log(device_id, log_data)

        # 同时写入专门的通知日志文件
        log_entry = {
            "timestamp": timestamp.isoformat(),
            "device_id": device_id,
            "type": "mqtt_dismiss",
            "target_device_id": target_device_id,
            "status": status
        }

        today = timestamp.strftime("%Y-%m-%d")
        notification_log_file = self.log_dir / f"{today}_notifications.log"

        try:
            with open(notification_log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except Exception as e:
            print(f"Error writing to notification log: {e}")