import os
import json
import threading
import queue
import logging
import time
import requests
import base64
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

def worker(task_queue, session_manager, progress_lock, progress_stats):
    while True:
        try:
            task = task_queue.get(timeout=1)
        except queue.Empty:
            break
        
        try:
            # Registration
            # Retry loop for registration
            max_retries = 3
            file_path_on_server = None
            
            for attempt in range(max_retries):
                # Get current credentials
                session_id, asp_net_session_id = session_manager.get_credentials()
                
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

                try:
                    resp = requests.get(reg_url, params=params, headers=headers, timeout=10)
                    if resp.status_code == 200:
                        content = resp.text.strip()
                        if len(content) < 2 or content == "F":
                            # Failed registration
                            logger.warning(f"Registration failed for {task.sop_uid}: {content}")
                            # Try to renew session
                            if session_manager.renew_if_needed(session_id):
                                continue # Retry with new session
                            else:
                                break # Renewal failed
                        else:
                            file_path_on_server = content
                            break # Success
                    else:
                        logger.warning(f"Registration HTTP error {resp.status_code} for {task.sop_uid}")
                        if resp.status_code in [401, 403]:
                             if session_manager.renew_if_needed(session_id):
                                 continue
                except requests.RequestException as e:
                    logger.warning(f"Registration connection error for {task.sop_uid}: {e}")
                
                time.sleep(1) # wait before retry

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

def renew_session_key(user_id,user_password):
    """
    Renew the session key by making a TryToAu request.
    Returns the new session key if successful, None if failed.
    """
    renewal_url = "http://172.16.0.62:88/RequestDataReadOnly/RequestWorklistData"
    
    # Create the renewal payload using the exact structure from your example
    renewal_payload_data = {
        "RemoteCallAction": "AccountAction",
        "Command": "TryToAu",
        "Parameters": {
            "Account": {
                "SessionKey": None,
                "UserKey": 0,
                "UserID": user_id,
                "UserPassword": user_password,
                "LevelCode": 0,
                "LevelCode_Maro": "",
                "TimezoneKey": "",
                "UseDST": "N",
                "TimezoneName": "",
                "InstitutionCode_IHP": "",
                "FacilityCode_IHP": "",
                "CheckOption": "T",
                "SensitiveLoginID": "",
                "IsReferringPhysician": "",
                "UserHideCodes": "",
                "Department": "",
                "UserInstitutionCode": "",
                "UserIDForDisplay": "",
                "IsChangePwdLater": "F",
                "MFAType": "",
                "MFAInfo": None,
                "SearchText": "",
                "Force": "Y"
            },
            "Profile": None,
            "Worklist": None,
            "Worklist_Study_IDB": None,
            "SearchFilter": None,
            "RequestType": None,
            "Patient": None,
            "StationInfo": None,
            "Worklist_SendStatus": None,
            "WebSocketEntity": None,
            "LabelWorkInfo": None,
            "Device": "PC"
        },
        "oRequestTime": None,
        "EXIUrl": {
            "url": "",
            "LikeSearchList": []
        },
        "ECS_dbType": "MAIN",
        "strRequestTime": int(time.time() * 1000)  # Current timestamp in milliseconds
    }
    
    # Encode using the custom server padding format (replace = with - and add = at end)
    renewal_json = json.dumps(renewal_payload_data, separators=(',', ':'))
    renewal_encoded = base64.b64encode(renewal_json.encode('utf-8')).decode('ascii')
    
    # Apply custom padding: replace standard = padding with - and add final =
    # Remove standard padding first
    renewal_no_padding = renewal_encoded.rstrip('=')
    # Calculate how many padding chars were removed
    padding_count = len(renewal_encoded) - len(renewal_no_padding)
    # Add the custom padding: replace each = with - and add final =
    custom_padding = '-' * padding_count + '='
    renewal_payload = renewal_no_padding + custom_padding
    
    headers = {
        'Host': '172.16.0.62:88',
        'Content-Length': str(len(renewal_payload)),
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.71 Safari/537.36',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Accept': '*/*',
        'Origin': 'http://172.16.0.62:88',
        'Referer': 'http://172.16.0.62:88/',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'close'
    }

    
    try:
        print("Attempting to renew session key...")
        response = requests.post(renewal_url, headers=headers, data=renewal_payload)
        
        asp_net_session_id = response.cookies.get('ASP.NET_SessionId')
        
        if response.status_code == 200:
            # Check if response is {Nothing} which means session is still valid
            if response.text.strip() == '{Nothing}':
                print("Session renewal endpoint returned {Nothing} - this means the session is still valid for the renewal endpoint.")
                print("This could indicate a timing issue where the viewer session expired but the renewal session hasn't.")
                return None, None
                
            try:
                # Parse the response which should be a JSON array
                response_data = json.loads(response.text)
                if isinstance(response_data, list) and len(response_data) > 0:
                    account_data = response_data[0]
                    if "Parameters" in account_data and "Account" in account_data["Parameters"]:
                        session_key = account_data["Parameters"]["Account"].get("SessionKey")
                        if session_key:
                            print(f"Successfully renewed session key: {session_key[:20]}...")
                            return session_key, asp_net_session_id
                        else:
                            print("No SessionKey found in response")
                    else:
                        print("Unexpected response structure")
                else:
                    print("Empty or invalid response")
            except json.JSONDecodeError as e:
                print(f"Failed to parse renewal response: {e}")
        else:
            print(f"Session renewal failed with status code: {response.status_code}")
            
        # Print response for debugging
        print(f"Renewal response: {response.text[:500]}...")
        return None, None
        
    except Exception as e:
        print(f"Error during session renewal: {e}")
        return None, None

class SessionManager:
    def __init__(self, user_id, password):
        self.user_id = user_id
        self.password = password
        self.session_id = None
        self.asp_net_session_id = None
        self.lock = threading.Lock()

    def get_credentials(self):
        with self.lock:
            return self.session_id, self.asp_net_session_id

    def renew_if_needed(self, failed_session_id):
        with self.lock:
            # If session has changed since failure, it was already renewed
            if self.session_id != failed_session_id and self.session_id is not None:
                return True
            
            # Attempt renewal
            logger.info("Renewing session...")
            new_sess, new_asp = renew_session_key(self.user_id, self.password)
            if new_sess:
                self.session_id = new_sess
                self.asp_net_session_id = new_asp
                logger.info("Session renewed successfully.")
                return True
            else:
                logger.error("Session renewal failed.")
                return False

def main():
    print("DICOM Downloader Client")
    print("-----------------------")
    
    data_dir = os.path.abspath("data")
    if not os.path.exists(data_dir):
        print(f"Error: 'data' directory not found at {data_dir}")
        return

    # User Inputs
    user_id = input("Enter User ID: ").strip()
    user_password = input("Enter Password: ").strip()
    
    session_manager = SessionManager(user_id, user_password)
    if not session_manager.renew_if_needed(None):
        print("Initial authentication failed.")
        return
    
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
        t = threading.Thread(target=worker, args=(task_queue, session_manager, progress_lock, progress_stats))
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
