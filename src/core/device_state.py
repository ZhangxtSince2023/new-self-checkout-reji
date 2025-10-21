import uuid
import json
from datetime import datetime
from typing import Dict, Optional, List, Tuple
from pathlib import Path
from .device_notifier import DeviceNotifier


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
        # 状态稳定性验证相关
        self.pending_state: Optional[str] = None  # 待确认的状态
        self.pending_count = 0  # 待确认状态的连续检测次数
        self.pending_timestamps: List[float] = []  # 记录检测时间
        
    def reset(self):
        """会计结束后重置会话数据"""
        self.session_id = None
        self.session_start = None
        self.scan_count = 0
        self.state_history.clear()
        # 重置待确认状态
        self.pending_state = None
        self.pending_count = 0
        self.pending_timestamps = []
        
    def add_to_history(self, timestamp: float, state: str):
        """添加状态到历史记录"""
        self.state_history.append((timestamp, state))
        # 限制历史记录长度，避免内存问题
        if len(self.state_history) > 1000:
            self.state_history = self.state_history[-500:]


class DeviceStateManager:
    """所有设备的状态管理器"""

    # 定义合法的状态转换规则
    VALID_TRANSITIONS = {
        "idle": ["start"],                # 空闲只能到开始
        "start": ["scan", "idle"],        # 开始后可以扫描或放弃
        "scan": ["list", "over", "idle"], # 扫描可以查看列表、结束或放弃
        "list": ["scan", "over", "idle"], # 列表可以继续扫描、结束或放弃
        "over": ["idle"]                  # 结束只能回到空闲
    }

    # 转换到idle需要连续确认的次数（over除外）
    IDLE_CONFIRMATION_COUNT = 5  # 需要连续5次确认
    CONFIRMATION_TIME_WINDOW = 3.0  # 确认时间窗口（秒）

    def __init__(self, log_dir: str = "log", logger=None, config_loader=None):
        self.devices: Dict[str, DeviceState] = {}
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.logger = logger  # 可选的外部日志器
        self.notifier = DeviceNotifier(config_loader, logger) if config_loader else None  # 设备通知器
        
    def update_state(self, device_id: str, detected_state: str, confidence: float, timestamp: float) -> Dict:
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
            # 如果当前已经是目标状态，清理待确认状态
            if device.pending_state:
                device.pending_state = None
                device.pending_count = 0
                device.pending_timestamps = []
            return {}

        # 验证状态转换合法性
        if not self._is_valid_transition(device.current_state, detected_state):
            self._log_state_message(
                device_id,
                f"拒绝非法状态转换: {device.current_state} -> {detected_state} (置信度: {confidence:.2%})"
            )
            return {}

        # 检查是否需要状态稳定性验证（转换到idle，但不是从over转换）
        needs_confirmation = (detected_state == "idle" and device.current_state != "over")

        if needs_confirmation:
            # 需要连续确认才能转换到idle
            if device.pending_state == detected_state:
                device.pending_count += 1
                device.pending_timestamps.append(timestamp)

                if device.pending_count >= self.IDLE_CONFIRMATION_COUNT:
                    # 检查时间跨度
                    time_span = timestamp - device.pending_timestamps[0]
                    if time_span <= self.CONFIRMATION_TIME_WINDOW:
                        # 确认状态变化
                        old_state = device.current_state
                        device.current_state = detected_state
                        device.last_update = timestamp
                        device.add_to_history(timestamp, detected_state)
                        # 清理待确认状态
                        device.pending_state = None
                        device.pending_count = 0
                        device.pending_timestamps = []
                        # 处理状态转换
                        return self._handle_state_transition(device, old_state, detected_state, timestamp)
                    else:
                        # 时间跨度太长，重新开始计数
                        device.pending_state = detected_state
                        device.pending_count = 1
                        device.pending_timestamps = [timestamp]
                        self._log_state_message(
                            device_id,
                            f"确认超时重置: {device.current_state} -> {detected_state}"
                        )
                else:
                    # 继续等待确认
                    self._log_state_message(
                        device_id,
                        f"等待确认: {device.current_state} -> {detected_state} "
                        f"({device.pending_count}/{self.IDLE_CONFIRMATION_COUNT})"
                    )
            else:
                # 新的待确认状态
                device.pending_state = detected_state
                device.pending_count = 1
                device.pending_timestamps = [timestamp]
                self._log_state_message(
                    device_id,
                    f"检测到潜在状态变化: {device.current_state} -> {detected_state} "
                    f"(1/{self.IDLE_CONFIRMATION_COUNT})"
                )
            return {}
        else:
            # 不需要确认，直接转换（包括over->idle）
            old_state = device.current_state
            device.current_state = detected_state
            device.last_update = timestamp
            device.add_to_history(timestamp, detected_state)
            # 清理任何待确认状态
            device.pending_state = None
            device.pending_count = 0
            device.pending_timestamps = []
            # 处理状态转换
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
        
        # 会计开始: 从 idle 或 list 转换到 start 都算开始
        # 只有在没有活跃会话时才算新的会计开始
        if new_state == "start" and (old_state == "idle" or (old_state == "list" and not device.session_id)):
            device.session_id = str(uuid.uuid4())
            device.session_start = timestamp
            device.scan_count = 0  # 重置扫描计数
            
            event['event_type'] = 'session_start'
            event['details'] = {
                'session_id': device.session_id,
                'start_time': datetime.fromtimestamp(timestamp).isoformat()
            }
            
            self._log_state_message(device.id, f"===== 会计开始 ===== Session: {device.session_id[:8]}...")
            
            # 发送会计开始通知（code 101）
            if self.notifier:
                self.notifier.send_session_start(device.id)
            
        # * -> scan: 扫描商品（只记录状态变化时的一次）
        elif new_state == "scan" and old_state != "scan":
            device.scan_count += 1

            event['event_type'] = 'product_scan'
            event['details'] = {
                'scan_number': device.scan_count,
                'session_id': device.session_id
            }

            self._log_state_message(device.id, f"扫描商品 #{device.scan_count}")

            # 发送扫描商品通知（code 102）
            if self.notifier:
                self.notifier.send_product_scan(device.id)
                # 发送 MQTT dismiss 命令
                self.notifier.send_mqtt_dismiss(device.id)
            
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

            # 发送扫描商品通知（code 102）
            if self.notifier:
                self.notifier.send_product_scan(device.id)
                # 发送 MQTT dismiss 命令
                self.notifier.send_mqtt_dismiss(device.id)
            
        # 会话结束（两种情况）：
        # 1. 正常买单：scan/list -> over
        # 2. 放弃购物：start/scan/list -> idle
        elif (new_state == "over" and old_state in ["scan", "list"]) or \
             (new_state == "idle" and old_state in ["start", "scan", "list"]):

            if device.session_id:
                duration = timestamp - device.session_start if device.session_start else 0

                # 区分结束类型
                end_type = "completed" if new_state == "over" else "abandoned"

                event['event_type'] = 'session_end'
                event['details'] = {
                    'session_id': device.session_id,
                    'end_type': end_type,
                    'duration_seconds': duration,
                    'total_scans': device.scan_count,
                    'end_time': datetime.fromtimestamp(timestamp).isoformat()
                }

                log_msg = f"===== 会话{'完成' if end_type == 'completed' else '放弃'} ===== "
                log_msg += f"时长: {duration:.1f}秒, 扫描: {device.scan_count}次"
                self._log_state_message(device.id, log_msg)

                # 无论哪种结束方式，都发送106信号
                if self.notifier:
                    self.notifier.send_session_end(device.id)

                # 重置设备状态
                device.reset()
            else:
                # 没有活跃会话但检测到结束状态，记录警告
                self._log_state_message(
                    device.id,
                    f"警告: 检测到会话结束状态({old_state} -> {new_state})，但没有活跃会话"
                )
                return {}

        # over -> idle: 不视为任何行为，静默处理
        elif old_state == "over" and new_state == "idle":
            # 不产生任何事件，返回空的 event
            return {}
        
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

    def _is_valid_transition(self, old_state: str, new_state: str) -> bool:
        """检查状态转换是否合法"""
        if old_state not in self.VALID_TRANSITIONS:
            return False
        return new_state in self.VALID_TRANSITIONS[old_state]
    
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