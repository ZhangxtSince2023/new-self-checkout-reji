import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any
import threading

class Logger:
    def __init__(self, log_dir: str = "log"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.lock = threading.Lock()
        
    def log(self, device_id: str, data: Dict[str, Any]):
        with self.lock:
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = self.log_dir / f"{today}.log"
            
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "device_id": device_id,
                "data": data
            }
            
            try:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            except Exception as e:
                print(f"Error writing to log: {e}")
    
    def get_logs_for_date(self, date: str) -> list:
        log_file = self.log_dir / f"{date}.log"
        
        if not log_file.exists():
            return []
        
        logs = []
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        logs.append(json.loads(line))
        except Exception as e:
            print(f"Error reading log file: {e}")
        
        return logs