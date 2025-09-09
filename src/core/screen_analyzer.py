import threading
import time
import multiprocessing as mp
from multiprocessing import Queue, Process
from pathlib import Path
from typing import Dict, Optional
from ultralytics import YOLO
from .rtsp_stream import RTSPStream
from .device_state import DeviceStateManager
from ..utils.logger import Logger
from ..utils.config_loader import ConfigLoader


def detector_process(model_path: str, input_queue: Queue, output_queue: Queue):
    """
    检测器进程函数，在独立进程中运行YOLO模型
    """
    try:
        model = YOLO(str(model_path), task='classify')
        print(f"Detector process {mp.current_process().pid} initialized")
        
        while True:
            try:
                task = input_queue.get(timeout=1)
                if task is None:  # 停止信号
                    break
                
                device_id, frame, timestamp = task
                
                # 分析帧
                results = model(frame, verbose=False)
                
                if results and len(results) > 0:
                    result = results[0]
                    
                    if hasattr(result, 'probs') and result.probs is not None:
                        probs = result.probs
                        top1_idx = probs.top1
                        top1_conf = probs.top1conf.item() if hasattr(probs.top1conf, 'item') else float(probs.top1conf)
                        
                        class_names = model.names if hasattr(model, 'names') else {}
                        class_name = class_names.get(top1_idx, f"Class_{top1_idx}")
                        
                        analysis_result = {
                            'timestamp': timestamp,
                            'class': class_name,
                            'class_id': top1_idx,
                            'confidence': top1_conf,
                            'all_probs': {class_names.get(i, f"Class_{i}"): float(p) 
                                        for i, p in enumerate(probs.data.tolist()) if float(p) > 0.01}
                        }
                        
                        output_queue.put((device_id, analysis_result))
                
            except mp.queues.Empty:
                continue
            except Exception as e:
                print(f"Error in detector process {mp.current_process().pid}: {e}")
                
    except Exception as e:
        print(f"Failed to initialize detector process: {e}")
    finally:
        print(f"Detector process {mp.current_process().pid} shutting down")


class ScreenAnalyzer:
    def __init__(self, model_path: str = None, analysis_interval: float = None, detector_count: int = None):
        # 加载配置
        config_loader = ConfigLoader()
        detection_config = config_loader.get_detection_config()
        
        # 使用配置文件中的值，如果没有提供参数
        self.model_path = Path(model_path or detection_config.get('model_path', 'models/best.mlpackage'))
        self.detector_count = detector_count or detection_config.get('detector_count', 5)
        self.confidence_threshold = detection_config.get('confidence_threshold', 0.5)
        self.analysis_interval = analysis_interval or detection_config.get('analysis_interval', 0.5)
        
        self.streams: Dict[str, RTSPStream] = {}
        self.is_running = False
        self.threads: Dict[str, threading.Thread] = {}
        self.logger = Logger()
        self.state_manager = DeviceStateManager(logger=self.logger, config_loader=config_loader)
        
        # 多进程相关
        self.input_queue: Optional[Queue] = None
        self.output_queue: Optional[Queue] = None
        self.detector_processes: list[Process] = []
        self.result_thread: Optional[threading.Thread] = None
        
        print(f"Initializing ScreenAnalyzer with {self.detector_count} detector processes")
        
    def _start_detector_pool(self):
        """启动检测器进程池"""
        if self.detector_processes:
            print("Detector pool already running")
            return
            
        print(f"Starting {self.detector_count} detector processes...")
        
        # 创建队列
        self.input_queue = mp.Queue(maxsize=self.detector_count * 2)
        self.output_queue = mp.Queue()
        
        # 启动检测器进程
        for _ in range(self.detector_count):
            p = mp.Process(
                target=detector_process,
                args=(self.model_path, self.input_queue, self.output_queue),
                daemon=True
            )
            p.start()
            self.detector_processes.append(p)
            
        # 启动结果处理线程
        self.result_thread = threading.Thread(target=self._process_results, daemon=True)
        self.result_thread.start()
        
        print(f"Started {self.detector_count} detector processes successfully")
        
    def _stop_detector_pool(self):
        """停止检测器进程池"""
        print("Stopping detector pool...")
        
        # 发送停止信号
        if self.input_queue:
            for _ in range(self.detector_count):
                self.input_queue.put(None)
        
        # 等待进程结束
        for p in self.detector_processes:
            p.join(timeout=5)
            if p.is_alive():
                p.terminate()
                
        self.detector_processes.clear()
        
        # 停止结果处理线程
        if self.result_thread and self.result_thread.is_alive():
            self.result_thread.join(timeout=2)
            
        print("Detector pool stopped")
    
    def _process_results(self):
        """处理检测结果的线程"""
        while self.is_running:
            try:
                if not self.output_queue.empty():
                    device_id, results = self.output_queue.get(timeout=0.1)
                    
                    # 只记录置信度高于阈值的结果
                    if results['confidence'] >= self.confidence_threshold:
                        # 更新设备状态
                        state_event = self.state_manager.update_state(
                            device_id,
                            results['class'],  # 检测到的状态 (idle/start/scan/list)
                            results['confidence'],
                            results['timestamp']
                        )
                        
                        # 如果有状态变化，记录到日志
                        if state_event:
                            results['state_event'] = state_event
                        
                        self._log_results(device_id, results)
                              
            except:
                continue
    
    def add_stream(self, device_id: str, rtsp_url: str):
        if device_id not in self.streams:
            stream = RTSPStream(device_id, rtsp_url)
            self.streams[device_id] = stream
            print(f"Added stream for device {device_id}")
    
    def start_monitoring(self):
        if self.is_running:
            print("Monitoring already running")
            return
        
        self.is_running = True
        
        # 启动检测器池
        self._start_detector_pool()
        
        # 启动RTSP流
        for device_id, stream in self.streams.items():
            stream.start()
            thread = threading.Thread(target=self._monitor_device, args=(device_id,), daemon=True)
            thread.start()
            self.threads[device_id] = thread
            
        print(f"Started monitoring {len(self.streams)} devices with {self.detector_count} detector processes")
    
    def stop_monitoring(self):
        self.is_running = False
        
        # 停止流
        for stream in self.streams.values():
            stream.stop()
        
        # 停止监控线程
        for thread in self.threads.values():
            thread.join(timeout=5)
        
        self.threads.clear()
        
        # 停止检测器池
        self._stop_detector_pool()
        
        print("Monitoring stopped")
    
    def _monitor_device(self, device_id: str):
        """监控设备的线程函数"""
        stream = self.streams[device_id]
        frame_count = 0
        
        while self.is_running:
            try:
                frame = stream.get_frame()
                
                if frame is not None:
                    frame_count += 1
                    
                    # 将帧放入队列进行分析
                    try:
                        task = (device_id, frame, time.time())
                        self.input_queue.put(task, timeout=0.1)
                    except:
                        # 队列满，跳过这一帧
                        pass
                        
                time.sleep(self.analysis_interval)
                
            except Exception as e:
                print(f"[{device_id}] Error during monitoring: {e}")
                time.sleep(self.analysis_interval)
    
    def _log_results(self, device_id: str, results: Dict):
        """记录结果到日志"""
        self.logger.log(device_id, results)
    
    def get_device_status(self, device_id: str = None) -> Dict:
        """获取设备状态信息"""
        if device_id:
            return self.state_manager.get_device_session_info(device_id)
        else:
            return self.state_manager.get_all_devices_status()