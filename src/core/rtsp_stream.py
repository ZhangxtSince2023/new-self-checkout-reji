import cv2
import threading
import time
from typing import Optional
import numpy as np

class RTSPStream:
    def __init__(self, device_id: str, rtsp_url: str):
        self.device_id = device_id
        self.rtsp_url = rtsp_url
        self.cap: Optional[cv2.VideoCapture] = None
        self.current_frame: Optional[np.ndarray] = None
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.reconnect_delay = 5
        
    def start(self):
        if self.is_running:
            return
        
        self.is_running = True
        self.thread = threading.Thread(target=self._capture_frames, daemon=True)
        self.thread.start()
    
    def stop(self):
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=5)
        if self.cap:
            self.cap.release()
            self.cap = None
    
    def _connect(self) -> bool:
        try:
            if self.cap:
                self.cap.release()
            
            self.cap = cv2.VideoCapture(self.rtsp_url)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            
            if not self.cap.isOpened():
                print(f"[{self.device_id}] Failed to connect to RTSP stream: {self.rtsp_url}")
                return False
            
            print(f"[{self.device_id}] Connected to RTSP stream: {self.rtsp_url}")
            return True
            
        except Exception as e:
            print(f"[{self.device_id}] Error connecting to RTSP stream: {e}")
            return False
    
    def _capture_frames(self):
        while self.is_running:
            if not self.cap or not self.cap.isOpened():
                if not self._connect():
                    time.sleep(self.reconnect_delay)
                    continue
            
            try:
                ret, frame = self.cap.read()
                if ret:
                    with self.lock:
                        self.current_frame = frame
                else:
                    print(f"[{self.device_id}] Failed to read frame, reconnecting...")
                    self.cap.release()
                    self.cap = None
                    time.sleep(self.reconnect_delay)
                    
            except Exception as e:
                print(f"[{self.device_id}] Error capturing frame: {e}")
                self.cap = None
                time.sleep(self.reconnect_delay)
    
    def get_frame(self) -> Optional[np.ndarray]:
        with self.lock:
            return self.current_frame.copy() if self.current_frame is not None else None
    
    def is_connected(self) -> bool:
        return self.cap is not None and self.cap.isOpened() and self.current_frame is not None