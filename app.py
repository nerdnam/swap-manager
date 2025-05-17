# app.py

import os
import time
import subprocess
from datetime import datetime
import docker
from flask import Flask, jsonify, render_template, request, send_from_directory, Response
import logging
import threading
import sys
from dotenv import load_dotenv
import signal

# --- 환경 변수 로드 ---
if os.path.exists("/app/swap.env"):
    print("Found /app/swap.env, attempting to load.")
    load_dotenv("/app/swap.env")
else:
    print("/app/swap.env not found, relying on Docker-provided environment variables.")

SWAP_FILE = os.environ.get("SWAP_FILE", "swapfile")
SWAP_SIZE = os.environ.get("SWAP_SIZE", "512G")
SWAPINESS = int(os.environ.get("SWAPINESS", "200"))
CONTAINER_NAME = os.environ.get("CONTAINER_NAME", "ix-ollama-ollama-1")
CGROUP_NAME = os.environ.get("CGROUP_NAME", "my_large_process")
MEMORY_LIMIT = os.environ.get("MEMORY_LIMIT", "8G")
SWAP_LIMIT = os.environ.get("SWAP_LIMIT", "512G")
MAX_PID_RETRIES = int(os.environ.get("MAX_PID_RETRIES", "5"))
CONTAINER_START_TIMEOUT = int(os.environ.get("CONTAINER_START_TIMEOUT", "30"))
LOG_FILE = os.environ.get("LOG_FILE", "/var/log/my_app/swap_manager.log")
SWAP_FILE_PREFIX_TO_DELETE = os.environ.get("SWAP_FILE_PREFIX_TO_DELETE", "swapfile")
SWAP_WORK_DIR = os.environ.get("SWAP_WORK_DIR", "/mnt/SwapWork")
WEB_UI_PORT = int(os.environ.get("WEB_UI_PORT", "5000"))
DEBUG_MODE = os.environ.get("DEBUG", "False").lower() == "true"
RESOURCE_CHECK_INTERVAL = int(os.environ.get("RESOURCE_CHECK_INTERVAL", "30"))
TARGET_PROCESS_NAME = os.environ.get("TARGET_PROCESS_NAME", "/bin/ollama serve")


# --- 로그 파일 초기화 ---
if os.path.exists(LOG_FILE):
    print(f"Existing log file '{LOG_FILE}' found. Attempting to delete...")
    try:
        subprocess.run(["sudo", "rm", "-f", LOG_FILE], check=True, capture_output=True, text=True, timeout=10)
        print(f"Log file '{LOG_FILE}' deleted successfully.")
    except FileNotFoundError:
        print(f"Warning: 'sudo' or 'rm' command not found during log file deletion attempt.")
    except subprocess.TimeoutExpired:
        print(f"Warning: Log file deletion command timed out.")
    except subprocess.CalledProcessError as e:
        print(f"Error deleting log file '{LOG_FILE}': {e.stderr.strip()}")
    except Exception as e:
        print(f"An unexpected error occurred during log file deletion: {e}")
else:
    print(f"Log file '{LOG_FILE}' not found. No deletion needed.")


# --- 로깅 설정 ---
log_dir = os.path.dirname(LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    try:
        os.makedirs(log_dir, exist_ok=True)
        print(f"Log directory created: {log_dir}")
    except OSError as e:
        print(f"Error creating log directory {log_dir}: {e}")
        LOG_FILE = os.path.basename(LOG_FILE)
        print(f"Logging to current directory: {LOG_FILE} (Fallback)")

logging.basicConfig(level=logging.INFO if not DEBUG_MODE else logging.DEBUG,
                    format='%(asctime)s %(levelname)s: %(message)s',
                    handlers=[
                        logging.FileHandler(LOG_FILE),
                        logging.StreamHandler(sys.stdout)
                    ])

log = logging.getLogger(__name__)


# --- 전역 변수로 스레드 및 종료 플래그 관리 ---
resource_thread = None
shutdown_flag = threading.Event()


# --- 상태 정보 저장 변수 ---
current_status = {
    "container_name": CONTAINER_NAME,
    "target_process_name": TARGET_PROCESS_NAME,
    "pid": 0,
    "cgroup_name": CGROUP_NAME,
    "memory_limit_set": MEMORY_LIMIT,
    "swap_limit_set": SWAP_LIMIT,
    "swap_file_path": os.path.join(SWAP_WORK_DIR, SWAP_FILE),
    "swap_file_size": SWAP_SIZE,
    "swappiness": SWAPINESS,
    "last_updated": datetime.now().isoformat(),
    "status_message": "Initializing...",
    "error": None,
    "swap_status": "Unknown",
    "cgroup_status": "Unknown",
    "swap_creation_time": "N/A",
    "system_memory_limit": MEMORY_LIMIT,
    "swap_memory_limit": SWAP_LIMIT,
    "memory_usage": "N/A",
    "swap_usage": "N/A"
}


def log_message(message, level=logging.INFO):
    if level == logging.ERROR:
        log.error(message)
        current_status["error"] = message
    elif level == logging.WARNING:
        log.warning(message)
    elif level == logging.CRITICAL:
        log.critical(message)
        current_status["error"] = message
    elif level == logging.DEBUG and not DEBUG_MODE:
         pass
    else:
        log.info(message)


def run_subprocess(command, check=True, description="", timeout=60):
    cmd_str = ' '.join(command) if isinstance(command, list) else command
    log_message(f"Attempting to execute command: {cmd_str} ({description})", level=logging.DEBUG)
    try:
        use_shell = isinstance(command, str)
        result = subprocess.run(command, check=check, capture_output=True, text=True, shell=use_shell, timeout=timeout)
        log_message(f"Command executed: {cmd_str}. Return code: {result.returncode}", level=logging.DEBUG)
        if result.stdout:
            stdout_preview = result.stdout.strip()[:500] + ('...' if len(result.stdout.strip()) > 500 else '')
            log_message(f"Command stdout: {stdout_preview}", level=logging.DEBUG)
        if result.stderr:
            stderr_preview = result.stderr.strip()[:500] + ('...' if len(result.stderr.strip()) > 500 else '')
            log_message(f"Command stderr: {stderr_preview}", level=logging.WARNING if not check or result.returncode == 0 else logging.ERROR)
        return result
    except subprocess.TimeoutExpired as e:
        log_message(f"Error: Command '{cmd_str}' timed out after {timeout} seconds. {e}", level=logging.ERROR)
        current_status["error"] = f"Command timed out ({description}): {e}"
        if check: raise e
        return None
    except subprocess.CalledProcessError as e:
        log_message(f"Error executing command '{cmd_str}': Check={check}, ReturnCode={e.returncode}. Stderr: {e.stderr.strip()}", level=logging.ERROR)
        current_status["error"] = f"Command failed ({description}): {e.stderr.strip() or e.stdout.strip() or e}"
        if check: raise e
        return e
    except FileNotFoundError as e:
        cmd_name = command.split(maxsplit=1)[0] if isinstance(command, str) else command[0]
        log_message(f"Error: Command not found: {cmd_name}. Make sure it's installed and in PATH. {e}", level=logging.CRITICAL)
        current_status["error"] = f"Command not found: {cmd_name}"
        raise e
    except Exception as e:
        log_message(f"An unexpected error occurred while running command '{cmd_str}': {e}", level=logging.ERROR)
        current_status["error"] = f"Unexpected error running command ({description}): {e}"
        raise e


def cleanup_all_swap_partitions():
    # ... (이전과 동일한 내용) ...
    log_message("##### Entering cleanup_all_swap_partitions function #####", level=logging.INFO)
    cleanup_successful = True
    try:
        log_message("Listing active swap devices with 'sudo swapon --show --noheadings --bytes --raw'...", level=logging.INFO)
        result = run_subprocess(["sudo", "swapon", "--show", "--noheadings", "--bytes", "--raw"], check=False, description="List active swaps")

        if result is None or result.returncode != 0 or not result.stdout:
            log_message("No active swap partitions found or failed to list them.", level=logging.INFO)
            return True

        swap_lines = result.stdout.strip().splitlines()
        swap_devices_raw = [line.split(maxsplit=1)[0] for line in swap_lines if line.strip()]
        log_message(f"Found active swap devices (raw): {', '.join(swap_devices_raw)}", level=logging.INFO)

        for device_raw in swap_devices_raw:
            device_path_for_command = device_raw
            if device_raw.startswith("/loop") and not device_raw.startswith("/dev/"):
                 device_path_for_command = f"/dev{device_raw}"
                 log_message(f"Interpreting raw device '{device_raw}' as loop device, will attempt swapoff/detach on '{device_path_for_command}'", level=logging.INFO)
            elif device_raw.startswith("/dev/loop"):
                 log_message(f"Device '{device_raw}' is already in /dev/loop format, using as is.", level=logging.INFO)

            log_message(f"Attempting to swapoff device: {device_path_for_command}", level=logging.INFO)
            swapoff_result = run_subprocess(["sudo", "swapoff", device_path_for_command], check=False, description=f"Swapoff {device_path_for_command}")
            if swapoff_result is None or swapoff_result.returncode != 0:
                log_message(f"Failed to swapoff device {device_path_for_command}.", level=logging.ERROR)
                cleanup_successful = False

            if device_path_for_command.startswith("/dev/loop"):
                log_message(f"Attempting to detach loop device: {device_path_for_command}", level=logging.INFO)
                losetup_d_command = ["sudo", "losetup", "-d", device_path_for_command]
                losetup_result = run_subprocess(losetup_d_command, check=False, description=f"Detach loop device {device_path_for_command}")
                if losetup_result is None or losetup_result.returncode != 0:
                    log_message(f"Failed to detach loop device {device_path_for_command}.", level=logging.ERROR)
                    cleanup_successful = False
                else:
                    log_message(f"Successfully detached loop device {device_path_for_command}.", level=logging.INFO)
        log_message("Finished cleaning up active swap partitions.", level=logging.INFO)
        return cleanup_successful
    except FileNotFoundError:
        log_message("swapon or losetup command not found. Make sure they are in the container's PATH.", level=logging.CRITICAL)
        current_status["error"] = "Swap commands not found for cleanup."
        return False
    except Exception as e:
        log_message(f"An unexpected error occurred during swap cleanup: {e}", level=logging.ERROR)
        current_status["error"] = f"Unexpected error during swap cleanup: {e}"
        return False


def delete_existing_swapfile():
    # ... (이전과 동일한 내용) ...
    swap_file_path = os.path.join(SWAP_WORK_DIR, SWAP_FILE)
    log_message(f"Checking for existing swap file: {swap_file_path}")
    if os.path.exists(swap_file_path):
        log_message(f"Existing swap file '{swap_file_path}' found. Attempting deletion...")
        try:
            log_message(f"Attempting to remove the file: {swap_file_path}", level=logging.INFO)
            run_subprocess(["sudo", "rm", "-f", swap_file_path], check=True, description=f"Delete swap file {swap_file_path}")

            if not os.path.exists(swap_file_path):
                 log_message(f"Swap file '{swap_file_path}' deleted successfully (confirmed by non-existence).", level=logging.INFO)
                 return True
            else:
                 log_message(f"Failed to delete swap file '{swap_file_path}' (file still exists after rm -f attempt).", level=logging.ERROR)
                 return False
        except Exception as e:
            log_message(f"Error deleting swap file '{swap_file_path}': {e}", level=logging.ERROR)
            return False
    else:
        log_message(f"Existing swap file '{swap_file_path}' not found. No deletion needed.", level=logging.INFO)
        return True

def create_and_enable_swap():
    # ... (이전과 동일한 내용) ...
    swap_file_path = os.path.join(SWAP_WORK_DIR, SWAP_FILE)
    log_message(f"Attempting to create and enable swap file '{swap_file_path}' (Size: {SWAP_SIZE}) using losetup method...", level=logging.INFO)
    current_status["status_message"] = f"Creating swap file {SWAP_FILE}..."
    creation_success = False
    loop_device = None

    try:
        if not os.path.exists(SWAP_WORK_DIR):
             log_message(f"Swap directory '{SWAP_WORK_DIR}' does not exist. Creating...", level=logging.INFO)
             try:
                 os.makedirs(SWAP_WORK_DIR, exist_ok=True)
                 log_message(f"Swap directory '{SWAP_WORK_DIR}' created.", level=logging.INFO)
             except OSError as e:
                 log_message(f"Failed to create swap directory '{SWAP_WORK_DIR}': {e}. Trying with sudo...", level=logging.WARNING)
                 run_subprocess(["sudo", "mkdir", "-p", SWAP_WORK_DIR], check=True, description="Create swap directory with sudo")
                 log_message(f"Swap directory '{SWAP_WORK_DIR}' created using sudo.", level=logging.INFO)

        log_message("Creating swap file using truncate...", level=logging.INFO)
        truncate_command = ["sudo", "truncate", "-s", SWAP_SIZE, swap_file_path]
        run_subprocess(truncate_command, check=True, description="Create swap file")
        creation_success = True
        if not os.path.exists(swap_file_path):
             log_message("Error: Swap file was not created after truncate command (existence check failed).", level=logging.ERROR)
             current_status["error"] = "Swap file not created after truncate (existence check)."
             return False

        log_message("Setting swap file permissions...", level=logging.INFO)
        chmod_command = ["sudo", "chmod", "600", swap_file_path]
        run_subprocess(chmod_command, check=True, description="Set swap file permissions")

        log_message("Finding an available loop device...", level=logging.INFO)
        find_loop_command = ["sudo", "losetup", "-f"]
        find_loop_result = run_subprocess(find_loop_command, check=True, description="Find available loop device")
        if not find_loop_result.stdout.strip():
            log_message("Failed to find an available loopback device (no output from losetup -f).", level=logging.CRITICAL)
            current_status["error"] = "Failed to find available loopback device (no output)."
            if creation_success:
                run_subprocess(["sudo", "rm", "-f", swap_file_path], check=False, description="Cleanup swap file after losetup -f failure")
            return False
        loop_device = find_loop_result.stdout.strip()
        log_message(f"Found available loopback device: {loop_device}", level=logging.INFO)

        log_message(f"Attaching {swap_file_path} to {loop_device}...", level=logging.INFO)
        losetup_attach_command = ["sudo", "losetup", loop_device, swap_file_path]
        run_subprocess(losetup_attach_command, check=True, description=f"Attach {swap_file_path} to {loop_device}")
        log_message(f"{swap_file_path} successfully attached to {loop_device}.", level=logging.INFO)

        log_message(f"Formatting loopback device {loop_device} as swap...", level=logging.INFO)
        mkswap_command = ["sudo", "mkswap", loop_device]
        run_subprocess(mkswap_command, check=True, description=f"Format {loop_device} as swap")
        log_message(f"Loopback device {loop_device} formatted as swap.", level=logging.INFO)

        log_message(f"Activating swap on loopback device {loop_device}...", level=logging.INFO)
        swapon_command = ["sudo", "swapon", loop_device]
        run_subprocess(swapon_command, check=True, description=f"Activate swap on {loop_device}")
        log_message(f"Swap successfully activated on {loop_device}.", level=logging.INFO)
        current_status["status_message"] = "Swap activated."

        log_message(f"Setting swappiness to {SWAPINESS}...", level=logging.INFO)
        sysctl_command = ["sudo", "sysctl", f"vm.swappiness={SWAPINESS}"]
        run_subprocess(sysctl_command, check=True, description="Set swappiness")
        log_message(f"Successfully ran sysctl to set swappiness to {SWAPINESS}.", level=logging.INFO)
        try:
            current_swappiness_result = run_subprocess(["sysctl", "-n", "vm.swappiness"], check=True, description="Get current swappiness", timeout=5)
            actual_swappiness = int(current_swappiness_result.stdout.strip())
            current_status["swappiness"] = actual_swappiness
            log_message(f"Verified swappiness is now {actual_swappiness}.", level=logging.INFO)
        except Exception as e:
            log_message(f"Could not read current vm.swappiness after setting: {e}. UI might show intended value.", level=logging.WARNING)

        log_message("Swap file setup completed successfully.", level=logging.INFO)
        current_status["swap_status"] = "Active"
        current_status["swap_creation_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_status["error"] = None
        current_status["status_message"] = "Swap setup complete and active."
        return True

    except (subprocess.CalledProcessError, FileNotFoundError, OSError, Exception) as e:
        log_message(f"Error during swap setup steps: {type(e).__name__} - {e}", level=logging.ERROR)
        current_status["swap_status"] = "Failed (Setup Error)"
        current_status["swap_creation_time"] = "N/A"
        if not current_status["error"]:
            current_status["error"] = f"Swap setup error: {e}"
        current_status["status_message"] = "Swap setup failed."

        log_message("Attempting cleanup after swap setup failure...", level=logging.WARNING)
        if loop_device and os.path.exists(loop_device):
             log_message(f"Attempting to detach loop device {loop_device}...", level=logging.INFO)
             run_subprocess(["sudo", "losetup", "-d", loop_device], check=False, description=f"Cleanup failed loop device {loop_device}")
        if creation_success and os.path.exists(swap_file_path):
            log_message(f"Attempting to clean up partially created swap file: {swap_file_path}", level=logging.INFO)
            run_subprocess(["sudo", "rm", "-f", swap_file_path], check=False, description="Clean up swap file on error")
        return False

def setup_swap():
    # ... (이전과 동일한 내용) ...
    log_message("##### Entering setup_swap function #####", level=logging.INFO)
    current_status["swap_status"] = "Setting up..."
    current_status["error"] = None

    try:
        log_message("#1.1 Testing sudo command...", level=logging.INFO)
        id_result = run_subprocess(["sudo", "id"], check=False, description="Test sudo id")
        if id_result is None or id_result.returncode != 0:
             log_message("sudo id command failed or timed out. Sudo may not be configured correctly.", level=logging.ERROR)
             current_status["error"] = "Sudo command failed. Check container privileges and sudo installation."

        log_message("#1.1 Testing losetup list command...", level=logging.INFO)
        list_result = run_subprocess(["sudo", "losetup", "-a"], check=False, description="List loop devices")
        if list_result is None or list_result.returncode != 0:
            log_message("sudo losetup -a command failed or timed out.", level=logging.WARNING)

        log_message("#1.2 Cleaning up all active swap partitions and loop devices...", level=logging.INFO)
        if not cleanup_all_swap_partitions():
             log_message("Cleanup of existing swap partitions/devices failed. Continuing...", level=logging.WARNING)
        log_message("#1.2 Finished cleaning up active swap partitions and loop devices.", level=logging.INFO)

        log_message("#1.3 Deleting existing target swap file...", level=logging.INFO)
        if not delete_existing_swapfile():
            log_message("Failed to delete existing target swap file. Aborting setup.", level=logging.ERROR)
            current_status["swap_status"] = "Failed (Delete Existing)"
            current_status["swap_creation_time"] = "N/A"
            current_status["status_message"] = "Failed to delete existing swap file."
            return False
        log_message("#1.3 Finished deleting existing target swap file.", level=logging.INFO)

        log_message("#1.4 Creating and enabling new swap file using losetup method...", level=logging.INFO)
        if not create_and_enable_swap():
            log_message("Failed to create and enable new swap file. Aborting setup.", level=logging.ERROR)
            current_status["status_message"] = "Failed to create/enable new swap file."
            return False
        log_message("#1.4 Finished creating and enabling new swap file.", level=logging.INFO)

        log_message("##### Swap setup process finished successfully. Exiting setup_swap. #####", level=logging.INFO)
        current_status["swap_status"] = "Active"
        current_status["error"] = None
        current_status["status_message"] = "Swap setup successful."
        return True
    except Exception as e:
        log_message(f"Critical error during overall swap setup sequence: {e}", level=logging.CRITICAL)
        current_status["swap_status"] = "Failed (Overall Error)"
        current_status["swap_creation_time"] = "N/A"
        current_status["error"] = f"Critical swap setup error: {e}"
        current_status["status_message"] = "Critical swap setup error."
        return False

docker_client = None
def initialize_docker_client():
    # ... (이전과 동일한 내용) ...
    global docker_client
    log_message("Attempting to initialize Docker client...", level=logging.INFO)
    current_status["status_message"] = "Initializing Docker client..."
    for attempt in range(5):
        try:
            client = docker.from_env()
            client.ping()
            log_message("Docker client initialized successfully.", level=logging.INFO)
            docker_client = client
            if current_status["error"] and "docker client" in current_status["error"].lower():
                current_status["error"] = None
            current_status["status_message"] = "Docker client connected."
            return True
        except Exception as e:
            log_message(f"Error initializing Docker client (Attempt {attempt + 1}/5): {e}. Retrying in 5 seconds...", level=logging.WARNING)
            current_status["error"] = f"Docker connection failed: {e}"
            current_status["status_message"] = f"Docker client connection failed (attempt {attempt+1})."
            time.sleep(5)
    log_message("Failed to initialize Docker client after multiple retries. Container restart may not work.", level=logging.CRITICAL)
    current_status["error"] = "Failed to initialize Docker client after multiple retries."
    current_status["status_message"] = "Docker client connection failed permanently."
    docker_client = None
    return False

def find_process_pid_by_name(process_name):
    # ... (이전과 동일한 내용) ...
    log_message(f"#2. Attempting to find PID for process '{process_name}' using pgrep -f...", level=logging.INFO)
    current_status["status_message"] = f"Finding PID for {process_name}..."
    try:
        pgrep_result = run_subprocess(["pgrep", "-f", process_name], check=True, description=f"Find PID for {process_name}")
        pids = pgrep_result.stdout.strip().splitlines()
        if pids:
            pid = int(pids[0])
            log_message(f"Found PID {pid} for process '{process_name}'.", level=logging.INFO)
            current_status["pid"] = pid
            current_status["status_message"] = f"PID {pid} found for {process_name}."
            if current_status["error"] and (f"Process '{process_name}' not found" in current_status["error"] or "parse PID" in current_status["error"]):
                current_status["error"] = None
            return pid
        else:
            log_message(f"pgrep -f '{process_name}' returned output but no PIDs extracted.", level=logging.WARNING)
            current_status["error"] = f"Process '{process_name}' found by pgrep, but no PID extracted."
            current_status["status_message"] = f"No PID extracted for {process_name}."
            current_status["pid"] = 0
            return 0
    except FileNotFoundError:
        current_status["status_message"] = "pgrep command not found."
        current_status["pid"] = 0
        return 0
    except subprocess.CalledProcessError:
        log_message(f"Process '{process_name}' not found (pgrep return code 1).", level=logging.WARNING)
        current_status["error"] = f"Process '{process_name}' not found."
        current_status["status_message"] = f"Process '{process_name}' not found."
        current_status["pid"] = 0
        return 0
    except ValueError:
        log_message("Could not parse PID from pgrep output.", level=logging.ERROR)
        current_status["error"] = "Could not parse PID from pgrep output."
        current_status["status_message"] = "Failed to parse PID."
        current_status["pid"] = 0
        return 0
    except Exception as e:
        log_message(f"An unexpected error occurred while finding PID for '{process_name}': {e}", level=logging.ERROR)
        current_status["error"] = f"Unexpected error finding PID: {e}"
        current_status["status_message"] = "Error finding PID."
        current_status["pid"] = 0
        return 0

def restart_container(container_name):
    # ... (이전과 동일한 내용) ...
    log_message(f"Attempting to restart container: {container_name}", level=logging.INFO)
    current_status["status_message"] = f"Restarting container {container_name}..."
    global docker_client
    if docker_client is None:
        log_message("Docker client is not initialized. Cannot restart container.", level=logging.ERROR)
        current_status["error"] = "Docker client not initialized for container restart."
        current_status["status_message"] = "Cannot restart: Docker client not ready."
        return False
    try:
        container = docker_client.containers.get(container_name)
        log_message(f"Restarting container '{container_name}'...", level=logging.INFO)
        container.restart()
        log_message(f"Container '{container_name}' restarted successfully.", level=logging.INFO)
        current_status["status_message"] = f"Container {container_name} restart initiated."
        if current_status["error"] and "restarting container" in current_status["error"].lower():
            current_status["error"] = None
        return True
    except docker.errors.NotFound:
        log_message(f"Container '{container_name}' not found. Cannot restart.", level=logging.ERROR)
        current_status["error"] = f"Container '{container_name}' not found for restart."
        current_status["status_message"] = f"Cannot restart: Container {container_name} not found."
        return False
    except docker.errors.APIError as e:
        log_message(f"Docker API error while restarting container '{container_name}': {e}", level=logging.ERROR)
        current_status["error"] = f"Docker API error restarting container: {e}"
        current_status["status_message"] = "Docker API error during restart."
        return False
    except Exception as e:
        log_message(f"An unexpected error occurred while restarting container '{container_name}': {e}", level=logging.ERROR)
        current_status["error"] = f"Unexpected error restarting container: {e}"
        current_status["status_message"] = "Unexpected error during restart."
        return False

def create_cgroup():
    # ... (이전과 동일한 내용, subtree_control 관련 주석 포함) ...
    log_message(f"##### Entering create_cgroup function for '{CGROUP_NAME}' #####", level=logging.INFO)
    current_status["status_message"] = f"Creating Cgroup {CGROUP_NAME}..."
    cgroup_base_path = f"/sys/fs/cgroup/{CGROUP_NAME}"
    log_message(f"Using cgroup base path: {cgroup_base_path}", level=logging.INFO)
    try:
        log_message("#1. Creating cgroup directory...", level=logging.INFO)
        mkdir_command = ["sudo", "mkdir", "-p", cgroup_base_path]
        run_subprocess(mkdir_command, check=True, description=f"Create cgroup directory {cgroup_base_path}")
        log_message("Cgroup directory ensured.", level=logging.INFO)

        subtree_control_path = "/sys/fs/cgroup/cgroup.subtree_control"
        if os.path.exists(subtree_control_path):
            try:
                with open(subtree_control_path, "r") as f:
                    current_controllers = f.read()
                if "+memory" not in current_controllers:
                    log_message("Memory controller not enabled in root cgroup. Attempting to enable...", level=logging.INFO)
                    enable_command = f"echo '+memory' > {subtree_control_path}"
                    enable_result = run_subprocess(["sudo", "sh", "-c", enable_command], check=False, description="Enable memory controller")
                    if enable_result and enable_result.returncode == 0:
                        with open(subtree_control_path, "r") as f_check:
                            if "+memory" in f_check.read():
                                log_message("Memory controller enabled successfully in root cgroup.", level=logging.INFO)
                            else:
                                log_message("Enabled memory controller command ran, but '+memory' not found in subtree_control after write.", level=logging.WARNING)
                    else:
                        log_message("Failed to run command to enable memory controller in root cgroup. Limits might not apply.", level=logging.WARNING)
                else:
                    log_message("Memory controller already enabled in root cgroup.", level=logging.INFO)
            except Exception as e:
                 log_message(f"Warning: Could not manage memory controller in root cgroup: {e}. Memory limits might not work.", level=logging.WARNING)
                 current_status["error"] = f"Warning: Could not manage root cgroup memory controller: {e}"
        else:
             log_message("'/sys/fs/cgroup/cgroup.subtree_control' not found. Assuming cgroup v1 or controller already delegated.", level=logging.WARNING)
        
        current_status["status_message"] = f"Cgroup {CGROUP_NAME} directory ready."
        return True
    except subprocess.CalledProcessError:
        log_message("Failed to create cgroup directory.", level=logging.CRITICAL)
        current_status["cgroup_status"] = "Failed (Dir Creation)"
        current_status["status_message"] = "Failed to create Cgroup directory."
        return False
    except Exception as e:
        log_message(f"Unexpected error in create_cgroup: {e}", level=logging.ERROR)
        current_status["cgroup_status"] = "Failed (Unexpected Error)"
        current_status["status_message"] = "Error creating Cgroup."
        return False

def set_cgroup_limits(pid):
    # ... (이전과 동일한 내용) ...
    log_message(f"##### Entering set_cgroup_limits function for PID: {pid} in cgroup '{CGROUP_NAME}' #####", level=logging.INFO)
    current_status["status_message"] = f"Setting Cgroup limits for PID {pid}..."
    if pid <= 0:
        log_message("Invalid PID provided for cgroup limit setting.", level=logging.WARNING)
        current_status["cgroup_status"] = "Failed (Invalid PID for Limits)"
        current_status["status_message"] = "Cannot set Cgroup limits: Invalid PID."
        return False

    cgroup_base_path = f"/sys/fs/cgroup/{CGROUP_NAME}"
    cgroup_procs_path = os.path.join(cgroup_base_path, "cgroup.procs")
    cgroup_mem_max_path = os.path.join(cgroup_base_path, "memory.max")
    cgroup_swap_max_path = os.path.join(cgroup_base_path, "memory.swap.max")

    if not os.path.exists(cgroup_base_path):
        log_message(f"Cgroup base path does not exist: {cgroup_base_path}. Cannot set limits.", level=logging.ERROR)
        current_status["cgroup_status"] = "Failed (Cgroup Dir Missing)"
        current_status["status_message"] = "Cannot set Cgroup limits: Directory missing."
        return False

    try:
        log_message(f"#1. Adding PID {pid} to cgroup tasks file ({cgroup_procs_path})...", level=logging.INFO)
        write_pid_command = f'echo "{pid}" > "{cgroup_procs_path}"'
        run_subprocess(["sudo", "sh", "-c", write_pid_command], check=True, description=f"Add PID {pid} to cgroup.procs")
        log_message(f"Successfully added PID {pid} to cgroup tasks using sudo sh -c.", level=logging.INFO)

        mem_limit_set_successfully = False
        if MEMORY_LIMIT.strip().upper() not in ["0G", "0", ""]:
            log_message(f"#2. Attempting to set memory limit: {MEMORY_LIMIT} to {cgroup_mem_max_path}...", level=logging.INFO)
            if os.path.exists(cgroup_mem_max_path):
                 write_mem_limit_command = f'echo "{MEMORY_LIMIT.strip().upper()}" > "{cgroup_mem_max_path}"'
                 run_subprocess(["sudo", "sh", "-c", write_mem_limit_command], check=True, description=f"Set memory limit to {MEMORY_LIMIT}")
                 log_message(f"Successfully set memory limit to {MEMORY_LIMIT} using sudo sh -c.", level=logging.INFO)
                 mem_limit_set_successfully = True
            else:
                 log_message(f"Memory limit file not found: {cgroup_mem_max_path}. Skipping memory limit setting.", level=logging.WARNING)
                 current_status["cgroup_status"] = "Configured (Mem Limit File Missing)" # 부분 성공으로 간주
                 mem_limit_set_successfully = True 
        else:
            log_message(f"Memory limit '{MEMORY_LIMIT}' implies no specific limit, skipping cgroup write.", level=logging.INFO)
            mem_limit_set_successfully = True

        swap_limit_set_successfully = False
        if SWAP_LIMIT.strip().upper() not in ["0G", "0", ""]:
            log_message(f"#3. Attempting to set swap limit: {SWAP_LIMIT} to {cgroup_swap_max_path}...", level=logging.INFO)
            if os.path.exists(cgroup_swap_max_path):
                write_swap_limit_command = f'echo "{SWAP_LIMIT.strip().upper()}" > "{cgroup_swap_max_path}"'
                run_subprocess(["sudo", "sh", "-c", write_swap_limit_command], check=True, description=f"Set swap limit to {SWAP_LIMIT}")
                log_message(f"Successfully set swap limit to {SWAP_LIMIT} using sudo sh -c.", level=logging.INFO)
                swap_limit_set_successfully = True
            else:
                 log_message(f"Swap limit file not found: {cgroup_swap_max_path}. Skipping swap limit setting.", level=logging.WARNING)
                 current_status["cgroup_status"] = current_status["cgroup_status"].replace("Configured (", "Configured (Mem/Swap ") if "Mem Limit File Missing" in current_status["cgroup_status"] else "Configured (Swap Limit File Missing)"
                 swap_limit_set_successfully = True
        else:
            log_message(f"Swap limit '{SWAP_LIMIT}' implies no specific limit, skipping cgroup write.", level=logging.INFO)
            swap_limit_set_successfully = True
        
        if "Failed" not in current_status["cgroup_status"]: # 심각한 실패가 아니었다면
            if not current_status["cgroup_status"].startswith("Configured ("): # 경고도 없었다면
                current_status["cgroup_status"] = "Configured"
        
        log_message("Cgroup limits setup process completed.", level=logging.INFO)
        current_status["status_message"] = f"Cgroup limits processed for PID {pid}."
        if "Failed" not in current_status["cgroup_status"]:
             if current_status["error"] and ("limit" in current_status["error"].lower() or "cgroup" in current_status["error"].lower()):
                 current_status["error"] = None
        return True

    except subprocess.CalledProcessError as e:
        log_message(f"Failed to set cgroup limits for PID {pid}: {e}", level=logging.ERROR)
        current_status["cgroup_status"] = "Failed (Set Limits)"
        current_status["status_message"] = f"Failed to set Cgroup limits for PID {pid}."
        return False
    except Exception as e:
        log_message(f"Unexpected error in set_cgroup_limits for PID {pid}: {e}", level=logging.ERROR)
        current_status["cgroup_status"] = "Failed (Unexpected Error)"
        current_status["status_message"] = "Error setting Cgroup limits."
        return False

def monitor_resource_usage(pid):
    # ... (이전과 동일한 내용) ...
    if pid <= 0:
        current_status["memory_usage"] = "N/A"
        current_status["swap_usage"] = "N/A"
        return
    try:
        status_file_path = f"/proc/{pid}/status"
        if not os.path.exists(status_file_path):
            log_message(f"Process status file not found for PID {pid}. Process may have exited.", level=logging.WARNING)
            current_status["memory_usage"] = "N/A"
            current_status["swap_usage"] = "N/A"
            if current_status["pid"] == pid:
                current_status["pid"] = 0
                current_status["status_message"] = f"Monitored process PID {pid} disappeared."
            return

        with open(status_file_path, 'r') as f:
            status_content = f.read()
        mem_usage = "N/A"
        swap_usage = "N/A"
        for line in status_content.splitlines():
            if line.startswith("VmRSS:"):
                mem_usage = line.split(":")[1].strip()
            elif line.startswith("VmSwap:"):
                swap_usage = line.split(":")[1].strip()

        current_status["memory_usage"] = mem_usage
        current_status["swap_usage"] = swap_usage
        log_message(f"PID {pid} - Updated status: Mem: {mem_usage}, Swap: {swap_usage}", level=logging.DEBUG)
    except Exception as e:
        log_message(f"Error monitoring resources for PID {pid}: {e}", level=logging.ERROR)
        current_status["memory_usage"] = "Error"
        current_status["swap_usage"] = "Error"
        current_status["error"] = f"Monitoring error for PID {pid}: {e}"


# --- 정리 작업 함수 ---
def cleanup_resources_on_exit(triggered_by_signal=False):
    # ... (이전과 동일한 내용) ...
    log_message("##### Initiating cleanup due to shutdown #####", level=logging.INFO)
    current_status["status_message"] = "Shutting down and cleaning up resources..."

    global resource_thread
    if resource_thread and resource_thread.is_alive() and not shutdown_flag.is_set():
        log_message("Signaling resource management thread to stop...", level=logging.INFO)
        shutdown_flag.set()
        resource_thread.join(timeout=10)
        if resource_thread.is_alive():
            log_message("Resource management thread did not stop gracefully.", level=logging.WARNING)
        else:
            log_message("Resource management thread stopped.", level=logging.INFO)
    elif shutdown_flag.is_set() and resource_thread and not resource_thread.is_alive():
        log_message("Resource management thread already stopped or shutdown initiated.", level=logging.INFO)

    log_message("Cleaning up active swap partitions and loop devices...", level=logging.INFO)
    if not cleanup_all_swap_partitions():
        log_message("Cleanup of existing swap partitions/devices encountered issues.", level=logging.WARNING)
    else:
        log_message("Successfully cleaned up active swap partitions and loop devices.", level=logging.INFO)
    current_status["swap_status"] = "Cleaned Up"

    log_message("Deleting target swap file (if it was managed by this instance)...", level=logging.INFO)
    if not delete_existing_swapfile():
        log_message("Failed to delete target swap file or it did not exist.", level=logging.WARNING)
    else:
        log_message("Target swap file deleted successfully (if it existed).", level=logging.INFO)

    log_message("##### Cleanup finished. #####", level=logging.INFO)
    current_status["status_message"] = "Shutdown complete."


def handle_signal(signum, frame):
    # ... (이전과 동일한 내용) ...
    signal_name = signal.Signals(signum).name
    log_message(f"Received signal {signal_name}. Initiating graceful shutdown...", level=logging.INFO)
    
    if shutdown_flag.is_set():
        log_message("Shutdown already in progress.", level=logging.INFO)
        return
    shutdown_flag.set()

    cleanup_resources_on_exit(triggered_by_signal=True)
    
    log_message(f"Exiting due to signal {signal_name}.", level=logging.INFO)
    sys.exit(0)


# --- 리소스 관리 스레드 ---
def manage_resources():
    # ... (이전과 동일한 내용) ...
    log_message("##### Entering manage_resources function in background thread #####", level=logging.INFO)
    current_status["status_message"] = "Resource manager thread started."

    if shutdown_flag.is_set(): return

    log_message("Attempting initial swap setup...", level=logging.INFO)
    swap_setup_success = setup_swap()
    log_message(f"Initial swap setup completed. Success: {swap_setup_success}", level=logging.INFO)
    if swap_setup_success and current_status["error"] and "swap setup" in current_status["error"].lower():
        current_status["error"] = None

    if shutdown_flag.is_set(): return

    log_message("Attempting to initialize Docker client for container restart functionality...", level=logging.INFO)
    initialize_docker_client()
    if docker_client and current_status["error"] and "docker client" in current_status["error"].lower():
         current_status["error"] = None

    pid_retries = 0
    log_message("Starting main resource monitoring loop...", level=logging.INFO)
    while not shutdown_flag.is_set():
        try:
            loop_start_time = time.time()
            current_status["last_updated"] = datetime.now().isoformat()
            current_status["status_message"] = "Monitoring resources..."

            if current_status["pid"] <= 0 or \
               (current_status.get("last_successful_pid") and current_status["pid"] != current_status.get("last_successful_pid")):
                log_message(f"Attempting to find target process PID (Retry {pid_retries + 1}/{MAX_PID_RETRIES})...", level=logging.INFO)
                find_process_pid_by_name(TARGET_PROCESS_NAME)

                if current_status["pid"] > 0:
                    current_status["last_successful_pid"] = current_status["pid"]
                    pid_retries = 0
                else:
                    pid_retries += 1
                    if pid_retries >= MAX_PID_RETRIES:
                        log_message(f"Failed to find target process '{TARGET_PROCESS_NAME}' PID after {MAX_PID_RETRIES} retries.", level=logging.ERROR)
                        current_status["cgroup_status"] = "Unknown"

                        if docker_client is not None and not shutdown_flag.is_set():
                             log_message(f"Attempting to restart container '{CONTAINER_NAME}' due to PID lookup failure.", level=logging.WARNING)
                             if restart_container(CONTAINER_NAME):
                                  log_message(f"Container '{CONTAINER_NAME}' restart initiated. Waiting for {CONTAINER_START_TIMEOUT}s...", level=logging.INFO)
                                  current_status["status_message"] = f"Container {CONTAINER_NAME} restarting..."
                                  shutdown_flag.wait(timeout=CONTAINER_START_TIMEOUT)
                                  pid_retries = 0
                                  if shutdown_flag.is_set(): break
                                  continue
                             else:
                                  log_message(f"Failed to restart container '{CONTAINER_NAME}'. Manual intervention may be required.", level=logging.CRITICAL)
                                  pid_retries = 0
                        else:
                             log_message("Docker client not initialized or shutdown in progress. Cannot attempt container restart.", level=logging.WARNING)
                             pid_retries = 0
                    else:
                         log_message(f"Target process '{TARGET_PROCESS_NAME}' not found. Retrying...", level=logging.WARNING)
                         wait_time_pid_fail = CONTAINER_START_TIMEOUT / MAX_PID_RETRIES if MAX_PID_RETRIES > 0 else 5
                         shutdown_flag.wait(timeout=wait_time_pid_fail)
                         if shutdown_flag.is_set(): break
                         continue

            if current_status["pid"] > 0 and not shutdown_flag.is_set():
                if (current_status["cgroup_status"] != "Configured" and not current_status["cgroup_status"].startswith("Configured (")) or \
                    current_status.get("last_cgroup_pid") != current_status["pid"]: # "Configured (with warnings)"도 포함
                    log_message(f"Attempting cgroup setup for PID {current_status['pid']}...", level=logging.INFO)
                    if create_cgroup():
                        if set_cgroup_limits(current_status["pid"]):
                            log_message(f"Successfully applied cgroup settings for PID {current_status['pid']}.", level=logging.INFO)
                            current_status["last_cgroup_pid"] = current_status["pid"]
                elif (current_status["cgroup_status"] == "Configured" or current_status["cgroup_status"].startswith("Configured (")) and \
                     current_status.get("last_cgroup_pid") == current_status["pid"]:
                    current_status["status_message"] = f"Monitoring PID {current_status['pid']}. Limits applied."
                    if current_status["error"] and ("PID" in current_status["error"] or "Cgroup" in current_status["error"]):
                         current_status["error"] = None

            if current_status["pid"] > 0 and not shutdown_flag.is_set():
                monitor_resource_usage(current_status["pid"])
            elif current_status["pid"] <= 0 and current_status["status_message"] == "Monitoring resources...":
                current_status["status_message"] = "Waiting for target process..."
                current_status["memory_usage"] = "N/A"
                current_status["swap_usage"] = "N/A"

            loop_duration = time.time() - loop_start_time
            actual_sleep_time = max(0.1, RESOURCE_CHECK_INTERVAL - loop_duration)
            if not shutdown_flag.is_set():
                log_message(f"Loop duration: {loop_duration:.2f}s. Sleeping for {actual_sleep_time:.2f} seconds...", level=logging.DEBUG)
                shutdown_flag.wait(timeout=actual_sleep_time)
            
            if shutdown_flag.is_set():
                log_message("Shutdown signal received during main loop, exiting.", level=logging.INFO)
                break
        except Exception as e:
            log_message(f"Unexpected error in resource management loop: {type(e).__name__} - {e}. Loop will restart after short delay.", level=logging.ERROR)
            current_status["error"] = f"Critical loop error: {e}"
            current_status["pid"] = 0
            current_status["cgroup_status"] = "Unknown"
            current_status["status_message"] = "Resource manager loop error, restarting loop..."
            shutdown_flag.wait(timeout=5)
            if shutdown_flag.is_set(): break
            continue
            
    log_message("Resource management loop is terminating.", level=logging.INFO)
    current_status["status_message"] = "Resource manager thread stopped."
    log_message("##### Exiting manage_resources function in background thread #####", level=logging.INFO)


# --- Flask 앱 초기화 ---
app = Flask(__name__)

# --- Flask 웹 서버 라우트 ---
@app.route('/')
def index():
    current_status["last_updated"] = datetime.now().isoformat()
    template_path = os.path.join(app.root_path, 'templates', 'status.html')
    if not os.path.exists(template_path):
        log_message(f"Template file not found: {template_path}. Serving basic JSON.", level=logging.WARNING)
        return jsonify(current_status)
    try:
        return render_template('status.html', current_status=current_status)
    except Exception as e:
        log_message(f"Error rendering status.html template: {e}", level=logging.ERROR)
        current_status["error"] = f"Template rendering error: {e}"
        return jsonify(current_status), 500

@app.route('/status')
def status_json():
    current_status["last_updated"] = datetime.now().isoformat()
    return jsonify(current_status)

@app.route('/favicon.ico')
def favicon():
    static_dir = os.path.join(app.root_path, 'static')
    favicon_path = os.path.join(static_dir, 'favicon.ico')
    if os.path.exists(favicon_path):
        return send_from_directory(static_dir, 'favicon.ico', mimetype='image/vnd.microsoft.icon')
    else:
        log_message("favicon.ico not found in static folder. Sending 204 No Content.", level=logging.DEBUG)
        return Response(status=204, mimetype='image/x-icon')


@app.route("/delete_all_swap", methods=['POST'])
def delete_all_swap_files():
    # ... (이전과 동일한 내용) ...
    log_message("# Processing request to delete swap files...", level=logging.INFO)
    current_status["status_message"] = "Processing request to delete swap files..."
    prefix = SWAP_FILE_PREFIX_TO_DELETE
    work_dir = SWAP_WORK_DIR
    deleted_count = 0
    errors = []

    log_message("Attempting to cleanup all active swap partitions first...", level=logging.INFO)
    if not cleanup_all_swap_partitions():
        msg = "Issues encountered during general swap partition cleanup. Continuing with target file deletion."
        log_message(msg, level=logging.WARNING)
        errors.append(msg)
    
    log_message(f"Specifically deleting swap files starting with '{prefix}' in '{work_dir}' (if any)...", level=logging.INFO)
    if os.path.exists(work_dir):
        try:
            for filename in os.listdir(work_dir):
                if filename.startswith(prefix):
                    swap_file_path = os.path.join(work_dir, filename)
                    log_message(f"Attempting to process file for deletion: {swap_file_path}", level=logging.INFO)
                    try:
                        run_subprocess(["sudo", "rm", "-f", swap_file_path], check=True, description=f"Delete swap file {swap_file_path}")
                        if not os.path.exists(swap_file_path):
                            log_message(f"Successfully deleted file: {swap_file_path}", level=logging.INFO)
                            deleted_count += 1
                        else:
                            msg = f"Command to delete file '{swap_file_path}' ran, but file still exists."
                            log_message(msg, level=logging.ERROR)
                            errors.append(msg)
                    except subprocess.CalledProcessError as e:
                        msg = f"Failed to delete file '{swap_file_path}': {e.stderr.strip() or e}"
                        log_message(msg, level=logging.ERROR)
                        errors.append(msg)
                    except Exception as e:
                        msg = f"Error processing file '{swap_file_path}': {e}"
                        log_message(msg, level=logging.ERROR)
                        errors.append(msg)
        except Exception as list_e:
             msg = f"Error listing directory '{work_dir}': {list_e}"
             log_message(msg, level=logging.ERROR)
             errors.append(msg)
    else:
         msg = f"Swap work directory '{work_dir}' not found. Cannot delete files."
         log_message(msg, level=logging.WARNING)

    final_message = f"Swap file deletion process finished. Deleted: {deleted_count} files matching prefix '{prefix}'."
    log_message(final_message, level=logging.INFO)
    current_status["status_message"] = final_message
    if errors:
        current_status["error"] = "; ".join(errors)
        return jsonify({"message": final_message, "errors": errors}), 500
    else:
        current_status["error"] = None
        return jsonify({"message": final_message}), 200


# --- 메인 실행 블록 ---
if __name__ == "__main__":
    current_status["status_message"] = "Application starting..."
    log_message("Initializing application...", level=logging.INFO)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log_message("Starting resource management thread...", level=logging.INFO)
    resource_thread = threading.Thread(target=manage_resources, name="ResourceMgrThread")
    resource_thread.daemon = True
    resource_thread.start()
    log_message("Resource management thread started.", level=logging.INFO)
    current_status["status_message"] = "Application initialized."

    log_message(f"Starting Flask web server on host 0.0.0.0, port {WEB_UI_PORT}...", level=logging.INFO)
    try:
        # Flask 개발 서버의 재로더는 항상 False로 설정하여 불필요한 재시작 및 초기화 반복 방지
        app.run(host='0.0.0.0', port=WEB_UI_PORT, debug=DEBUG_MODE, use_reloader=False)
    except (KeyboardInterrupt, SystemExit) as e:
        log_message(f"Flask server shutting down due to {type(e).__name__}...", level=logging.INFO)
        current_status["status_message"] = "Flask server shutting down..."
    except Exception as e:
        log_message(f"Failed to start or run Flask web server: {e}", level=logging.CRITICAL)
        current_status["error"] = f"Flask server error: {e}"
        current_status["status_message"] = "Flask server error."
        if not shutdown_flag.is_set(): # 이미 종료 중이 아니라면 정리 시도
            shutdown_flag.set()
            cleanup_resources_on_exit()
    finally:
        log_message("Performing final application shutdown steps...", level=logging.INFO)
        # 시그널 핸들러에서 cleanup이 이미 호출되었거나 진행 중일 수 있으므로, shutdown_flag로 확인
        if not shutdown_flag.is_set(): # 아직 shutdown_flag가 설정되지 않았다면 (예: app.run()이 다른 이유로 정상 종료)
            log_message("Flask server stopped (not by signal). Initiating cleanup.", level=logging.INFO)
            shutdown_flag.set() # 다른 스레드에 알림
            cleanup_resources_on_exit()
        else: # 시그널 핸들러에서 이미 cleanup을 시작/완료했을 가능성 높음
            # resource_thread가 아직 살아있고 join 안됐다면 여기서 마지막으로 시도
            if resource_thread and resource_thread.is_alive():
                log_message("Ensuring resource management thread is stopped (final check)...", level=logging.INFO)
                resource_thread.join(timeout=5) # 짧은 타임아웃으로 대기
                if resource_thread.is_alive():
                     log_message("Resource management thread still alive at final shutdown.", level=logging.WARNING)

        log_message("Flask web server and application stopped.", level=logging.INFO)
        current_status["status_message"] = "Application stopped."
        logging.shutdown() # 모든 로그 플러시 및 핸들러 닫기