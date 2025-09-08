import uuid
import json
from datetime import datetime
from typing import Dict, Optional, List, Tuple
from pathlib import Path


class DeviceState:
    """单个设备的状态管理"""
    
    def __init__(self, device_id: str):
        self.id = device_id
        self.current_state = "idle"  # 默认状态
        self.session_id: Optional[str] = None
        self.session_start: Optional[float] = None
        self.scan_count = 0  # 当前会计的扫描次数
        self.last_update: Optional[float] = None
        self.state_history: List[Tuple[float, str]] = []
        
    def reset(self):
        """会计结束后重置会话数据"""
        self.session_id = None
        self.session_start = None
        self.scan_count = 0
        self.state_history.clear()
        
    def add_to_history(self, timestamp: float, state: str):
        """添加状态到历史记录"""
        self.state_history.append((timestamp, state))
        # 限制历史记录长度，避免内存问题
        if len(self.state_history) > 1000:
            self.state_history = self.state_history[-500:]


class DeviceStateManager:
    """所有设备的状态管理器"""
    
    def __init__(self, log_dir: str = "log", logger=None):
        self.devices: Dict[str, DeviceState] = {}
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.logger = logger  # 可选的外部日志器
        
    def update_state(self, device_id: str, detected_state: str, confidence: float, timestamp: float) -> Dict:  # confidence 参数保留以保持API一致性
        """
        更新设备状态
        
        Returns:
            Dict: 状态变化信息，如果没有变化则返回空字典
        """
        # 初始化设备状态
        if device_id not in self.devices:
            self.devices[device_id] = DeviceState(device_id)
            self._log_state_message(device_id, "初始化设备状态管理")
        
        device = self.devices[device_id]
        
        # 核心逻辑：如果和上一次状态相同，直接忽略
        if device.current_state == detected_state:
            return {}
        
        # 状态发生变化时才处理
        old_state = device.current_state
        device.current_state = detected_state
        device.last_update = timestamp
        device.add_to_history(timestamp, detected_state)
        
        # 处理状态转换并返回事件信息
        return self._handle_state_transition(device, old_state, detected_state, timestamp)
    
    def _handle_state_transition(self, device: DeviceState, old_state: str, new_state: str, timestamp: float) -> Dict:
        """
        处理状态转换
        
        Returns:
            Dict: 包含状态转换的详细信息
        """
        event = {
            'device_id': device.id,
            'old_state': old_state,
            'new_state': new_state,
            'timestamp': timestamp,
            'event_type': None,
            'details': {}
        }
        
        # idle -> start: 会计开始
        if old_state == "idle" and new_state == "start":
            device.session_id = str(uuid.uuid4())
            device.session_start = timestamp
            device.scan_count = 0  # 重置扫描计数
            
            event['event_type'] = 'session_start'
            event['details'] = {
                'session_id': device.session_id,
                'start_time': datetime.fromtimestamp(timestamp).isoformat()
            }
            
            self._log_state_message(device.id, f"===== 会计开始 ===== Session: {device.session_id[:8]}...")
            
        # * -> scan: 扫描商品（只记录状态变化时的一次）
        elif new_state == "scan" and old_state != "scan":
            device.scan_count += 1
            
            event['event_type'] = 'product_scan'
            event['details'] = {
                'scan_number': device.scan_count,
                'session_id': device.session_id
            }
            
            self._log_state_message(device.id, f"扫描商品 #{device.scan_count}")
            
        # scan -> list: 从扫描切换到查看列表
        elif old_state == "scan" and new_state == "list":
            event['event_type'] = 'view_list'
            self._log_state_message(device.id, "查看商品列表")
            
        # list -> scan: 从列表切换回扫描
        elif old_state == "list" and new_state == "scan":
            device.scan_count += 1
            
            event['event_type'] = 'product_scan'
            event['details'] = {
                'scan_number': device.scan_count,
                'session_id': device.session_id
            }
            
            self._log_state_message(device.id, f"扫描商品 #{device.scan_count}")
            
        # * -> idle: 会计结束
        elif new_state == "idle" and old_state != "idle":
            if device.session_start:
                duration = timestamp - device.session_start
                
                event['event_type'] = 'session_end'
                event['details'] = {
                    'session_id': device.session_id,
                    'duration_seconds': duration,
                    'total_scans': device.scan_count,
                    'end_time': datetime.fromtimestamp(timestamp).isoformat()
                }
                
                self._log_state_message(device.id, f"===== 会计结束 ===== 时长: {duration:.1f}秒, 扫描: {device.scan_count}次")
                
                # 重置设备状态
                device.reset()
        
        # 其他状态转换
        else:
            event['event_type'] = 'state_change'
            self._log_state_message(device.id, f"状态变化: {old_state} -> {new_state}")
        
        return event
    
    def get_device_state(self, device_id: str) -> Optional[DeviceState]:
        """获取设备当前状态"""
        return self.devices.get(device_id)
    
    def get_all_devices_status(self) -> Dict:
        """获取所有设备的状态摘要"""
        status = {}
        for device_id, device in self.devices.items():
            status[device_id] = {
                'current_state': device.current_state,
                'session_id': device.session_id,
                'session_active': device.session_id is not None,
                'scan_count': device.scan_count,
                'last_update': device.last_update
            }
        return status
    
    def get_device_session_info(self, device_id: str) -> Dict:
        """获取设备当前会话信息"""
        device = self.devices.get(device_id)
        if not device or not device.session_id:
            return {}
        
        return {
            'session_id': device.session_id,
            'start_time': device.session_start,
            'current_state': device.current_state,
            'scan_count': device.scan_count,
            'state_history': device.state_history[-10:]  # 最近10个状态
        }
    
    def _log_state_message(self, device_id: str, message: str):
        """记录状态消息到日志"""
        timestamp = datetime.now()
        
        # 如果有外部logger，使用它来记录到主日志
        if self.logger:
            log_data = {
                "timestamp": timestamp.timestamp(),
                "type": "state_message",
                "message": message
            }
            self.logger.log(device_id, log_data)
        
        # 同时也写入状态日志文件（便于单独查看）
        log_entry = {
            "timestamp": timestamp.isoformat(),
            "device_id": device_id,
            "type": "state_message",
            "message": message
        }
        
        today = timestamp.strftime("%Y-%m-%d")
        state_log_file = self.log_dir / f"{today}_states.log"
        
        try:
            with open(state_log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except Exception as e:
            # 降级到标准输出
            print(f"[{device_id}] {message}")
            print(f"Error writing to state log: {e}")