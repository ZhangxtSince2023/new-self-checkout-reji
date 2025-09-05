import yaml
from pathlib import Path
from typing import Dict, Any

class ConfigLoader:
    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = Path(config_path)
        self.config = self.load_config()
    
    def load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        return config
    
    def get_devices(self) -> Dict[str, Dict[str, Any]]:
        return self.config.get('device', {})
    
    def get_rtsp_urls(self) -> Dict[str, str]:
        rtsp_urls = {}
        devices = self.get_devices()
        
        for device_id, device_info in devices.items():
            hostip = device_info.get('hostip')
            if hostip:
                rtsp_url = f"rtsp://{hostip}:8554/{device_id}"
                rtsp_urls[device_id] = rtsp_url
        
        return rtsp_urls
    
    def get_detection_config(self) -> Dict[str, Any]:
        return self.config.get('detection', {})