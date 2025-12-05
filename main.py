import os
import json
import threading
import queue
import logging
import time
import requests
from urllib.parse import urljoin

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_URL = "http://172.16.0.62:88"
REGISTRATION_ENDPOINT = "/RequestDataReadOnly/ExportImage2DCM/"

class DownloadTask:
    def __init__(self, sop_uid, output_dir, done_marker):
        self.sop_uid = sop_uid
        self.output_dir = output_dir
        self.done_marker = done_marker

def find_tasks(data_dir):
    tasks = []
    for root, dirs, files in os.walk(data_dir):
        if "response.json" in files:
            json_path = os.path.join(root, "response.json")
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # Parse structure
                # studyserieslist -> list of studies
                studies = data.get("studyserieslist", [])
                if not studies:
                    continue

                for study_idx, study in enumerate(studies):
                    series_list = study.get("serieslist", [])
                    for series_idx, series in enumerate(series_list):
                        image_list = series.get("imagelist", [])
                        for image in image_list:
                            sop_uid = image.get("uid")
                            if not sop_uid:
                                continue
                            
                            study_dir_name = f"study_{study_idx}"
                            series_dir_name = f"series_{series_idx}"
                            
                            output_dir = os.path.join(root, study_dir_name, series_dir_name)
                            
                            # Unique marker for this image
                            done_marker = os.path.join(output_dir, f".{sop_uid}.done")
                            
                            tasks.append(DownloadTask(sop_uid, output_dir, done_marker))
                            
            except Exception as e:
                logger.error(f"Error parsing {json_path}: {e}")
    return tasks

def worker(task_queue, session_id, asp_net_session_id, progress_lock, progress_stats):
    while True:
        try:
            task = task_queue.get(timeout=1)
        except queue.Empty:
            break
        
        try:
            # Registration
            params = {
                'strSession': session_id,
                'strSOPUID': task.sop_uid,
                'bAnonymize': 'false',
                'bKeepOriginal': 'false'
            }
            headers = {
                'Host': '172.16.0.62:88',
                'Connection': 'close',
                'Cookie': f'ASP.NET_SessionId={asp_net_session_id}'
            }
            
            reg_url = urljoin(BASE_URL, REGISTRATION_ENDPOINT)
            

            max_retries = 3
            file_path_on_server = None
            
            for attempt in range(max_retries):
                try:
                    resp = requests.get(reg_url, params=params, headers=headers, timeout=10)
                    if resp.status_code == 200:
                        content = resp.text.strip()
                        if len(content) < 2 or content == "F":
                            logger.warning(f"Registration failed for {task.sop_uid}: {content}")
                            break 

                        else:
                            file_path_on_server = content
                            break 
                    else:
                        logger.warning(f"Registration HTTP error {resp.status_code} for {task.sop_uid}")
                except requests.RequestException as e:
                    logger.warning(f"Registration connection error for {task.sop_uid}: {e}")
                
                time.sleep(1)

            if not file_path_on_server:
                with progress_lock:
                    progress_stats['failed'] += 1
                continue
            
            # Download
            # Response looks like: /Documents/7d061e4430104af782701af37528c0db/IM_NoComp1.2.3...dcm
            download_url = urljoin(BASE_URL, file_path_on_server)
            filename = os.path.basename(file_path_on_server)
            local_file_path = os.path.join(task.output_dir, filename)
            
            os.makedirs(task.output_dir, exist_ok=True)
            
            download_success = False
            for attempt in range(max_retries):
                try:
                    with requests.get(download_url, stream=True, timeout=20) as r:
                        r.raise_for_status()
                        with open(local_file_path, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192): 
                                f.write(chunk)
                    download_success = True
                    break
                except Exception as e:
                    logger.warning(f"Download failed for {filename}: {e}")
                    time.sleep(1)
            
            if download_success:
                # Create done marker
                with open(task.done_marker, 'w') as f:
                    f.write("done")
                with progress_lock:
                    progress_stats['success'] += 1
            else:
                with progress_lock:
                    progress_stats['failed'] += 1
                    
        except Exception as e:
            logger.error(f"Unexpected error processing task {task.sop_uid}: {e}")
            with progress_lock:
                progress_stats['failed'] += 1
        finally:
            task_queue.task_done()

def main():
    print("DICOM Downloader Client")
    print("-----------------------")
    
    data_dir = os.path.abspath("data")
    if not os.path.exists(data_dir):
        print(f"Error: 'data' directory not found at {data_dir}")
        return

    # User Inputs
    asp_net_session_id = input("Enter ASP.NET_SessionId: ").strip()
    session_id = input("Enter strSession: ").strip()
    
    try:
        num_threads = int(input("Enter number of threads (default 4): ").strip() or "4")
    except ValueError:
        num_threads = 4

    print("\nScanning for tasks...")
    all_tasks = find_tasks(data_dir)
    print(f"Found total {len(all_tasks)} potential images.")
    
    # Filter done tasks
    pending_tasks = []
    for t in all_tasks:
        if not os.path.exists(t.done_marker):
            pending_tasks.append(t)
            
    print(f"Already completed: {len(all_tasks) - len(pending_tasks)}")
    print(f"Remaining to download: {len(pending_tasks)}")
    
    if not pending_tasks:
        print("All tasks completed!")
        return

    confirm = input("\nStart downloading? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Aborted.")
        return

    task_queue = queue.Queue()
    for t in pending_tasks:
        task_queue.put(t)
        
    progress_stats = {'success': 0, 'failed': 0}
    progress_lock = threading.Lock()
    
    threads = []
    for _ in range(num_threads):
        t = threading.Thread(target=worker, args=(task_queue, session_id, asp_net_session_id, progress_lock, progress_stats))
        t.start()
        threads.append(t)
        
    # Monitor loop
    total = len(pending_tasks)
    try:
        while any(t.is_alive() for t in threads):
            with progress_lock:
                s = progress_stats['success']
                f = progress_stats['failed']
            print(f"Progress: {s}/{total} success, {f} failed", end='\r')
            time.sleep(1)
            
            # Check if queue is empty but threads are still running (finishing up)
            if task_queue.empty() and all(not t.is_alive() for t in threads):
                break
                
    except KeyboardInterrupt:
        print("\nStopping...")
        # We can't easily kill threads in Python, but we can drain the queue so they stop
        while not task_queue.empty():
            try:
                task_queue.get_nowait()
                task_queue.task_done()
            except queue.Empty:
                break
                
    for t in threads:
        t.join()
        
    print("\nDownload finished.")
    print(f"Final stats: {progress_stats['success']} success, {progress_stats['failed']} failed.")

if __name__ == "__main__":
    main()
