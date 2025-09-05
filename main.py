#!/usr/bin/env python3
import signal
import sys
import time
from src.utils import ConfigLoader
from src.core import ScreenAnalyzer

def signal_handler(sig, frame):
    print("\nReceived interrupt signal. Shutting down...")
    sys.exit(0)

def main():
    print("=" * 60)
    print("Self-Checkout Screen Monitoring System")
    print("=" * 60)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        print("\n[1] Loading configuration...")
        config = ConfigLoader()
        rtsp_urls = config.get_rtsp_urls()
        detection_config = config.get_detection_config()
        
        if not rtsp_urls:
            print("No devices found in configuration!")
            return
        
        print(f"Found {len(rtsp_urls)} device(s) to monitor:")
        for device_id, url in rtsp_urls.items():
            print(f"  - Device {device_id}: {url}")
        
        print(f"\n[2] Initializing analyzer with detection config:")
        print(f"  - Model path: {detection_config.get('model_path', 'models/best.mlpackage')}")
        print(f"  - Detector count: {detection_config.get('detector_count', 5)}")
        print(f"  - Confidence threshold: {detection_config.get('confidence_threshold', 0.5)}")
        print(f"  - Analysis interval: {detection_config.get('analysis_interval', 0.5)}s")
        
        analyzer = ScreenAnalyzer()  # 现在所有参数都从配置文件读取
        
        print("\n[3] Adding RTSP streams...")
        for device_id, rtsp_url in rtsp_urls.items():
            analyzer.add_stream(device_id, rtsp_url)
        
        print("\n[4] Starting monitoring...")
        analyzer.start_monitoring()
        
        print("\n" + "=" * 60)
        print("System is running. Press Ctrl+C to stop.")
        print("Analysis results will be saved to log/ directory")
        print("=" * 60 + "\n")
        
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        try:
            analyzer.stop_monitoring()
            print("Monitoring stopped successfully")
        except:
            pass

if __name__ == "__main__":
    main()