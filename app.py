# app.py

import os
import time
import subprocess
from datetime import datetime
import docker
from flask import Flask, jsonify, render_template, request
import logging
import threading
import sys
from dotenv import load_dotenv

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

    if level not in [logging.ERROR, logging.CRITICAL]:
        current_status["status_message"] = message


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
            else:
                 log_message(f"Device '{device_raw}' is not a recognized loop device format, using as is.", level=logging.INFO)

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
            else:
                 log_message(f"Device {device_path_for_command} is not a recognized loop device format, skipping detachment attempt.", level=logging.INFO)
        log_message("Finished cleaning up active swap partitions.", level=logging.INFO)
        return cleanup_successful
    except FileNotFoundError:
        log_message("swapon or losetup command not found. Make sure they are in the container's PATH.", level=logging.CRITICAL)
        current_status["error"] = "Swap commands not found."
        return False
    except Exception as e:
        log_message(f"An unexpected error occurred during swap cleanup: {e}", level=logging.ERROR)
        current_status["error"] = f"Unexpected error during swap cleanup: {e}"
        return False


def delete_existing_swapfile():
    swap_file_path = os.path.join(SWAP_WORK_DIR, SWAP_FILE)
    log_message(f"Checking for existing swap file: {swap_file_path}")
    if os.path.exists(swap_file_path):
        log_message(f"Existing swap file '{swap_file_path}' found. Attempting deletion...")
        try:
            log_message(f"Attempting to swapoff the file first: {swap_file_path}", level=logging.INFO)
            run_subprocess(["sudo", "swapoff", swap_file_path], check=False, description=f"Try disable swap file {swap_file_path}")
            log_message(f"Attempting to remove the file: {swap_file_path}", level=logging.INFO)
            delete_result = run_subprocess(["sudo", "rm", "-f", swap_file_path], check=True, description=f"Delete swap file {swap_file_path}")

            if delete_result and delete_result.returncode == 0:
                 log_message(f"Swap file '{swap_file_path}' deleted successfully (command returned 0).", level=logging.INFO)
                 return True
            else:
                 if os.path.exists(swap_file_path):
                     log_message(f"Failed to delete swap file '{swap_file_path}' (file still exists).", level=logging.ERROR)
                     return False
                 else:
                     log_message(f"Swap file '{swap_file_path}' deleted successfully (confirmed by non-existence).", level=logging.INFO)
                     return True
        except Exception as e:
            log_message(f"Error deleting swap file '{swap_file_path}': {e}", level=logging.ERROR)
            return False
    else:
        log_message(f"Existing swap file '{swap_file_path}' not found. No deletion needed.", level=logging.INFO)
        return True


def create_and_enable_swap():
    swap_file_path = os.path.join(SWAP_WORK_DIR, SWAP_FILE)
    log_message(f"Attempting to create and enable swap file '{swap_file_path}' (Size: {SWAP_SIZE}) using losetup method...", level=logging.INFO)
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
                 try:
                      run_subprocess(["sudo", "mkdir", "-p", SWAP_WORK_DIR], check=True, description="Create swap directory with sudo")
                      log_message(f"Swap directory '{SWAP_WORK_DIR}' created using sudo.", level=logging.INFO)
                 except Exception as sudo_e:
                      log_message(f"Failed to create swap directory '{SWAP_WORK_DIR}' even with sudo: {sudo_e}", level=logging.ERROR)
                      current_status["error"] = f"Failed to create swap directory: {sudo_e}"
                      return False

        log_message("Creating swap file using truncate...", level=logging.INFO)
        truncate_command = ["sudo", "truncate", "-s", SWAP_SIZE, swap_file_path]
        truncate_result = run_subprocess(truncate_command, check=True, description="Create swap file")
        if truncate_result is None or truncate_result.returncode != 0:
            current_status["error"] = "Failed to create swap file using truncate."
            return False
        if not os.path.exists(swap_file_path):
             log_message("Error: Swap file was not created after truncate command.", level=logging.ERROR)
             current_status["error"] = "Swap file not created after truncate."
             return False
        creation_success = True

        log_message("Setting swap file permissions...", level=logging.INFO)
        chmod_command = ["sudo", "chmod", "600", swap_file_path]
        chmod_result = run_subprocess(chmod_command, check=True, description="Set swap file permissions")
        if chmod_result is None or chmod_result.returncode != 0:
             log_message(f"Failed to set permissions for {swap_file_path}. Proceeding but may get mkswap/swapon warnings.", level=logging.WARNING)

        log_message("Finding an available loop device...", level=logging.INFO)
        find_loop_command = ["sudo", "losetup", "-f"]
        find_loop_result = run_subprocess(find_loop_command, check=True, description="Find available loop device")
        if find_loop_result is None or find_loop_result.returncode != 0 or not find_loop_result.stdout.strip():
            log_message("Failed to find an available loopback device.", level=logging.CRITICAL)
            current_status["error"] = "Failed to find available loopback device."
            run_subprocess(["sudo", "rm", "-f", swap_file_path], check=False, description="Cleanup swap file after losetup -f failure")
            return False
        loop_device = find_loop_result.stdout.strip()
        log_message(f"Found available loopback device: {loop_device}", level=logging.INFO)

        log_message(f"Attaching {swap_file_path} to {loop_device}...", level=logging.INFO)
        losetup_attach_command = ["sudo", "losetup", loop_device, swap_file_path]
        losetup_attach_result = run_subprocess(losetup_attach_command, check=True, description=f"Attach {swap_file_path} to {loop_device}")
        if losetup_attach_result is None or losetup_attach_result.returncode != 0:
            log_message(f"Failed to attach {swap_file_path} to {loop_device}.", level=logging.CRITICAL)
            current_status["error"] = f"Failed to attach swap file to loop device: {loop_device}"
            run_subprocess(["sudo", "rm", "-f", swap_file_path], check=False, description="Cleanup swap file after losetup attach failure")
            loop_device = None
            return False
        log_message(f"{swap_file_path} successfully attached to {loop_device}.", level=logging.INFO)

        log_message(f"Formatting loopback device {loop_device} as swap...", level=logging.INFO)
        mkswap_command = ["sudo", "mkswap", loop_device]
        mkswap_result = run_subprocess(mkswap_command, check=True, description=f"Format {loop_device} as swap")
        if mkswap_result is None or mkswap_result.returncode != 0:
            log_message(f"Failed to format {loop_device} as swap. Attempting to detach loop device and cleanup swap file.", level=logging.CRITICAL)
            current_status["error"] = f"Failed to format loop device as swap: {loop_device}"
            run_subprocess(["sudo", "losetup", "-d", loop_device], check=False, description=f"Detach failed loop device {loop_device}")
            run_subprocess(["sudo", "rm", "-f", swap_file_path], check=False, description="Cleanup swap file after mkswap failure")
            loop_device = None
            return False
        log_message(f"Loopback device {loop_device} formatted as swap.", level=logging.INFO)

        log_message(f"Activating swap on loopback device {loop_device}...", level=logging.INFO)
        swapon_command = ["sudo", "swapon", loop_device]
        swapon_result = run_subprocess(swapon_command, check=True, description=f"Activate swap on {loop_device}")
        if swapon_result is None or swapon_result.returncode != 0:
            log_message(f"Failed to activate swap on {loop_device}. Attempting to detach loop device and cleanup swap file.", level=logging.CRITICAL)
            current_status["error"] = f"Failed to activate swap on loop device: {loop_device}"
            run_subprocess(["sudo", "losetup", "-d", loop_device], check=False, description=f"Detach failed loop device {loop_device}")
            run_subprocess(["sudo", "rm", "-f", swap_file_path], check=False, description="Cleanup swap file after swapon failure")
            loop_device = None
            return False
        log_message(f"Swap successfully activated on {loop_device}.", level=logging.INFO)

        log_message(f"Setting swappiness to {SWAPINESS}...", level=logging.INFO)
        sysctl_command = ["sudo", "sysctl", f"vm.swappiness={SWAPINESS}"]
        sysctl_result = run_subprocess(sysctl_command, check=True, description="Set swappiness")
        if sysctl_result is None or sysctl_result.returncode != 0:
            log_message("Failed to set swappiness. Proceeding with default or previous value.", level=logging.WARNING)
        else:
             log_message(f"Successfully ran sysctl to set swappiness to {SWAPINESS}.", level=logging.INFO)
             try:
                 current_swappiness_result = run_subprocess(["sysctl", "-n", "vm.swappiness"], check=True, description="Get current swappiness", timeout=5)
                 if current_swappiness_result and current_swappiness_result.returncode == 0:
                     actual_swappiness = int(current_swappiness_result.stdout.strip())
                     current_status["swappiness"] = actual_swappiness
                     log_message(f"Verified swappiness is now {actual_swappiness}.", level=logging.INFO)
                 else:
                     log_message("Could not read current vm.swappiness after setting. UI might show intended value.", level=logging.WARNING)
             except Exception as e:
                  log_message(f"Could not read current vm.swappiness after setting: {e}. UI might show intended value.", level=logging.WARNING)

        log_message("Swap file setup completed successfully.", level=logging.INFO)
        current_status["swap_status"] = "Active"
        current_status["swap_creation_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_status["error"] = None
        return True

    except (subprocess.CalledProcessError, FileNotFoundError, OSError, Exception) as e:
        log_message(f"Error during swap setup steps: {e}", level=logging.ERROR)
        current_status["swap_status"] = "Failed (Setup Error)"
        current_status["swap_creation_time"] = "N/A"
        current_status["error"] = f"Swap setup error: {e}"

        log_message("Attempting cleanup after swap setup failure...", level=logging.WARNING)
        if loop_device and os.path.exists(loop_device):
             log_message(f"Attempting to detach loop device {loop_device}...", level=logging.INFO)
             run_subprocess(["sudo", "losetup", "-d", loop_device], check=False, description=f"Cleanup failed loop device {loop_device}")
        if creation_success and os.path.exists(swap_file_path):
            log_message(f"Attempting to clean up partially created swap file: {swap_file_path}", level=logging.INFO)
            run_subprocess(["sudo", "rm", "-f", swap_file_path], check=False, description="Clean up swap file on error")
        return False


def setup_swap():
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
            return False
        log_message("#1.3 Finished deleting existing target swap file.", level=logging.INFO)

        log_message("#1.4 Creating and enabling new swap file using losetup method...", level=logging.INFO)
        if not create_and_enable_swap():
            log_message("Failed to create and enable new swap file. Aborting setup.", level=logging.ERROR)
            current_status["swap_status"] = "Failed (Create/Enable)"
            current_status["swap_creation_time"] = "N/A"
            return False
        log_message("#1.4 Finished creating and enabling new swap file.", level=logging.INFO)

        log_message("##### Swap setup process finished successfully. Exiting setup_swap. #####", level=logging.INFO)
        current_status["swap_status"] = "Active"
        current_status["error"] = None
        return True
    except Exception as e:
        log_message(f"Critical error during overall swap setup sequence: {e}", level=logging.CRITICAL)
        current_status["swap_status"] = "Failed (Overall Error)"
        current_status["swap_creation_time"] = "N/A"
        current_status["error"] = f"Critical swap setup error: {e}"
        return False


docker_client = None
def initialize_docker_client():
    global docker_client
    log_message("Attempting to initialize Docker client...", level=logging.INFO)
    for attempt in range(5):
        try:
            client = docker.from_env()
            client.ping()
            log_message("Docker client initialized successfully.", level=logging.INFO)
            docker_client = client
            if current_status["error"] and "docker client" in current_status["error"].lower(): # Clear previous docker client error
                current_status["error"] = None
            return True
        except Exception as e:
            log_message(f"Error initializing Docker client (Attempt {attempt + 1}/5): {e}. Retrying in 5 seconds...", level=logging.WARNING)
            current_status["error"] = f"Docker connection failed: {e}"
            time.sleep(5)
    log_message("Failed to initialize Docker client after multiple retries. Container restart may not work.", level=logging.CRITICAL)
    current_status["error"] = "Failed to initialize Docker client after multiple retries." # This error will persist
    docker_client = None
    return False


def find_process_pid_by_name(process_name):
    log_message(f"#2. Attempting to find PID for process '{process_name}' using pgrep -f...", level=logging.INFO)
    try:
        pgrep_result = run_subprocess(["pgrep", "-f", process_name], check=True, description=f"Find PID for {process_name}")
        if pgrep_result and pgrep_result.returncode == 0 and pgrep_result.stdout:
            pids = pgrep_result.stdout.strip().splitlines()
            if pids:
                pid = int(pids[0])
                log_message(f"Found PID {pid} for process '{process_name}'.", level=logging.INFO)
                if current_status["error"] and (f"Process '{process_name}' not found" in current_status["error"] or "parse PID" in current_status["error"]):
                    current_status["error"] = None
                return pid
            else:
                log_message(f"pgrep -f '{process_name}' returned no PIDs.", level=logging.WARNING)
                current_status["error"] = f"Process '{process_name}' not found."
                return 0
    except FileNotFoundError:
        log_message("pgrep command not found. Make sure procps or similar package is installed.", level=logging.CRITICAL)
        current_status["error"] = "pgrep command not found."
        return 0
    except subprocess.CalledProcessError:
        log_message(f"Process '{process_name}' not found (pgrep return code 1).", level=logging.WARNING)
        current_status["error"] = f"Process '{process_name}' not found."
        return 0
    except ValueError:
        err_output = pgrep_result.stdout.strip() if 'pgrep_result' in locals() and pgrep_result else 'N/A'
        log_message(f"Could not parse PID from pgrep output: {err_output}", level=logging.ERROR)
        current_status["error"] = "Could not parse PID from pgrep output."
        return 0
    except Exception as e:
        log_message(f"An unexpected error occurred while finding PID for '{process_name}': {e}", level=logging.ERROR)
        current_status["error"] = f"Unexpected error finding PID: {e}"
        return 0


def restart_container(container_name):
    log_message(f"Attempting to restart container: {container_name}", level=logging.INFO)
    global docker_client
    if docker_client is None:
        log_message("Docker client is not initialized. Cannot restart container.", level=logging.ERROR)
        current_status["error"] = "Docker client not initialized for container restart."
        return False
    try:
        container = docker_client.containers.get(container_name)
        log_message(f"Restarting container '{container_name}'...", level=logging.INFO)
        container.restart()
        log_message(f"Container '{container_name}' restarted successfully.", level=logging.INFO)
        if current_status["error"] and "restarting container" in current_status["error"].lower():
            current_status["error"] = None
        return True
    except docker.errors.NotFound:
        log_message(f"Container '{container_name}' not found. Cannot restart.", level=logging.ERROR)
        current_status["error"] = f"Container '{container_name}' not found for restart."
        return False
    except docker.errors.APIError as e:
        log_message(f"Docker API error while restarting container '{container_name}': {e}", level=logging.ERROR)
        current_status["error"] = f"Docker API error restarting container: {e}"
        return False
    except Exception as e:
        log_message(f"An unexpected error occurred while restarting container '{container_name}': {e}", level=logging.ERROR)
        current_status["error"] = f"Unexpected error restarting container: {e}"
        return False


def create_cgroup():
    log_message(f"##### Entering create_cgroup function for '{CGROUP_NAME}' #####", level=logging.INFO)
    cgroup_base_path = f"/sys/fs/cgroup/{CGROUP_NAME}"
    log_message(f"Using cgroup base path: {cgroup_base_path}", level=logging.INFO)

    log_message("#1. Creating cgroup directory...", level=logging.INFO)
    mkdir_command = ["sudo", "mkdir", "-p", cgroup_base_path]
    mkdir_result = run_subprocess(mkdir_command, check=True, description=f"Create cgroup directory {cgroup_base_path}")
    if not mkdir_result or mkdir_result.returncode != 0:
        log_message("Failed to create cgroup directory.", level=logging.CRITICAL)
        current_status["cgroup_status"] = "Failed (Dir Creation)"
        return False
    log_message("Cgroup directory ensured.", level=logging.INFO)

    subtree_control_path = "/sys/fs/cgroup/cgroup.subtree_control"
    if os.path.exists(subtree_control_path):
        try:
            current_controllers = ""
            with open(subtree_control_path, "r") as f:
                current_controllers = f.read()
            if "+memory" not in current_controllers:
                log_message("Memory controller not enabled in root cgroup. Attempting to enable...", level=logging.INFO)
                enable_command = f"echo '+memory' > {subtree_control_path}"
                enable_result = run_subprocess(["sudo", "sh", "-c", enable_command], check=False, description="Enable memory controller")
                if enable_result and enable_result.returncode == 0:
                     try:
                         with open(subtree_control_path, "r") as f:
                             if "+memory" in f.read():
                                 log_message("Memory controller enabled successfully in root cgroup.", level=logging.INFO)
                             else:
                                 log_message("Enabled memory controller command ran, but '+memory' not found in subtree_control after write.", level=logging.WARNING)
                     except Exception as read_e:
                          log_message(f"Warning: Could not read subtree_control after enabling attempt: {read_e}", level=logging.WARNING)
                else:
                     log_message("Failed to run command to enable memory controller in root cgroup. Limits might not apply.", level=logging.WARNING)
            else:
                 log_message("Memory controller already enabled in root cgroup.", level=logging.INFO)
        except Exception as e:
             log_message(f"Warning: Could not manage memory controller in root cgroup: {e}. Memory limits might not work.", level=logging.WARNING)
             current_status["error"] = f"Warning: Could not manage root cgroup memory controller: {e}"
    else:
         log_message("'/sys/fs/cgroup/cgroup.subtree_control' not found. Assuming cgroup v1 or controller already delegated.", level=logging.WARNING)
    return os.path.exists(cgroup_base_path)


def set_cgroup_limits(pid):
    log_message(f"##### Entering set_cgroup_limits function for PID: {pid} in cgroup '{CGROUP_NAME}' #####", level=logging.INFO)
    if pid <= 0:
        log_message("Invalid PID provided for cgroup limit setting.", level=logging.WARNING)
        current_status["cgroup_status"] = "Failed (Invalid PID for Limits)"
        return False

    cgroup_base_path = f"/sys/fs/cgroup/{CGROUP_NAME}"
    cgroup_procs_path = os.path.join(cgroup_base_path, "cgroup.procs")
    cgroup_mem_max_path = os.path.join(cgroup_base_path, "memory.max")
    cgroup_swap_max_path = os.path.join(cgroup_base_path, "memory.swap.max")

    if not os.path.exists(cgroup_base_path):
        log_message(f"Cgroup base path does not exist: {cgroup_base_path}. Cannot set limits.", level=logging.ERROR)
        current_status["cgroup_status"] = "Failed (Cgroup Dir Missing)"
        return False

    log_message(f"#1. Adding PID {pid} to cgroup tasks file ({cgroup_procs_path})...", level=logging.INFO)
    write_pid_command = f'echo "{pid}" > "{cgroup_procs_path}"'
    full_command = ["sudo", "sh", "-c", write_pid_command]
    pid_add_result = run_subprocess(full_command, check=False, description=f"Add PID {pid} to cgroup.procs")
    if not pid_add_result or pid_add_result.returncode != 0:
         log_message(f"Failed to add PID {pid} to cgroup tasks using sudo sh -c.", level=logging.ERROR)
         current_status["cgroup_status"] = f"Failed (Add PID): Permission Denied or other error"
         return False
    log_message(f"Successfully added PID {pid} to cgroup tasks using sudo sh -c.", level=logging.INFO)

    mem_limit_applied = False
    if MEMORY_LIMIT.strip().upper() not in ["0G", "0", ""]:
        log_message(f"#2. Attempting to set memory limit: {MEMORY_LIMIT} to {cgroup_mem_max_path}...", level=logging.INFO)
        mem_value_to_write = MEMORY_LIMIT.strip().upper()
        if os.path.exists(cgroup_mem_max_path):
             write_mem_limit_command = f'echo "{mem_value_to_write}" > "{cgroup_mem_max_path}"'
             mem_set_result = run_subprocess(["sudo", "sh", "-c", write_mem_limit_command], check=False, description=f"Set memory limit to {MEMORY_LIMIT}")
             if not mem_set_result or mem_set_result.returncode != 0:
                 log_message(f"Failed to set memory limit using sudo sh -c to {cgroup_mem_max_path}.", level=logging.ERROR)
                 current_status["cgroup_status"] = f"Failed (Set Memory Limit): Permission Denied or other error"
             else:
                 log_message(f"Successfully set memory limit to {MEMORY_LIMIT} using sudo sh -c.", level=logging.INFO)
                 mem_limit_applied = True
        else:
             log_message(f"Memory limit file not found: {cgroup_mem_max_path}. Skipping memory limit setting.", level=logging.WARNING)
             current_status["cgroup_status"] = "Failed (Memory Limit File Missing)"
    else:
        log_message(f"Memory limit '{MEMORY_LIMIT}' implies no limit or zero, skipping explicit cgroup write for memory.max.", level=logging.INFO)
        mem_limit_applied = True

    swap_limit_applied = False
    if SWAP_LIMIT.strip().upper() not in ["0G", "0", ""]:
        log_message(f"#3. Attempting to set swap limit: {SWAP_LIMIT} to {cgroup_swap_max_path}...", level=logging.INFO)
        swap_value_to_write = SWAP_LIMIT.strip().upper()
        if os.path.exists(cgroup_swap_max_path):
            write_swap_limit_command = f'echo "{swap_value_to_write}" > "{cgroup_swap_max_path}"'
            swap_set_result = run_subprocess(["sudo", "sh", "-c", write_swap_limit_command], check=False, description=f"Set swap limit to {SWAP_LIMIT}")
            if not swap_set_result or swap_set_result.returncode != 0:
                 log_message(f"Failed to set swap limit using sudo sh -c to {cgroup_swap_max_path}.", level=logging.ERROR)
                 current_status["cgroup_status"] = f"Failed (Set Swap Limit): Permission Denied or other error"
            else:
                 log_message(f"Successfully set swap limit to {SWAP_LIMIT} using sudo sh -c.", level=logging.INFO)
                 swap_limit_applied = True
        else:
             log_message(f"Swap limit file not found: {cgroup_swap_max_path}. Skipping swap limit setting.", level=logging.WARNING)
             current_status["cgroup_status"] = "Failed (Swap Limit File Missing)"
    else:
        log_message(f"Swap limit '{SWAP_LIMIT}' implies no limit or zero, skipping explicit cgroup write for memory.swap.max.", level=logging.INFO)
        swap_limit_applied = True

    if not mem_limit_applied or not swap_limit_applied:
         log_message("Cgroup limits setup completed with some failures (see logs).", level=logging.WARNING)
         # Error status is already set by individual failing steps
         return True # PID was added, so partially successful
    else:
         log_message("Cgroup limits setup completed successfully.", level=logging.INFO)
         current_status["cgroup_status"] = "Configured"
         if current_status["error"] and ("limit" in current_status["error"].lower() or "cgroup" in current_status["error"].lower()):
             current_status["error"] = None
         return True


def monitor_resource_usage(pid):
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
        log_message(f"PID {pid} - Updated status: Mem: {current_status['memory_usage']}, Swap: {current_status['swap_usage']}", level=logging.DEBUG)
    except Exception as e:
        log_message(f"Error monitoring resources for PID {pid}: {e}", level=logging.ERROR)
        current_status["memory_usage"] = f"Error: {e}"
        current_status["swap_usage"] = f"Error: {e}"
        current_status["error"] = f"Monitoring error for PID {pid}: {e}"


def manage_resources():
    log_message("##### Entering manage_resources function in background thread #####", level=logging.INFO)

    log_message("Attempting initial swap setup...", level=logging.INFO)
    swap_setup_success = setup_swap()
    log_message(f"Initial swap setup completed. Success: {swap_setup_success}", level=logging.INFO)
    if swap_setup_success:
        if current_status["error"] and "swap setup" in current_status["error"].lower():
            current_status["error"] = None

    log_message("Attempting to initialize Docker client for container restart functionality...", level=logging.INFO)
    initialize_docker_client() # Errors handled inside

    pid_retries = 0
    log_message("Starting main resource monitoring loop...", level=logging.INFO)
    while True:
        try:
            log_message(f"--- Loop Start: Checking target process '{TARGET_PROCESS_NAME}' ---", level=logging.DEBUG)
            current_status["last_updated"] = datetime.now().isoformat()

            if current_status["pid"] <= 0 or \
               (current_status.get("last_successful_pid") and \
                current_status["pid"] != current_status.get("last_successful_pid")):
                log_message(f"Attempting to find target process PID (Retry {pid_retries + 1}/{MAX_PID_RETRIES})...", level=logging.INFO)
                pid = find_process_pid_by_name(TARGET_PROCESS_NAME)

                if pid > 0:
                    current_status["pid"] = pid
                    current_status["last_successful_pid"] = pid
                    log_message(f"Target process '{TARGET_PROCESS_NAME}' found with PID: {pid}", level=logging.INFO)
                    pid_retries = 0
                    # Error cleared within find_process_pid_by_name on success
                else:
                    pid_retries += 1
                    if pid_retries >= MAX_PID_RETRIES:
                        log_message(f"Failed to find target process '{TARGET_PROCESS_NAME}' PID after {MAX_PID_RETRIES} retries.", level=logging.ERROR)
                        current_status["pid"] = 0 # Ensure pid is 0
                        current_status["cgroup_status"] = "Unknown"
                        # Error set by find_process_pid_by_name

                        if docker_client is not None:
                             log_message(f"Attempting to restart container '{CONTAINER_NAME}' due to PID lookup failure.", level=logging.WARNING)
                             if restart_container(CONTAINER_NAME):
                                  log_message(f"Container '{CONTAINER_NAME}' restart initiated. Waiting for {CONTAINER_START_TIMEOUT}s...", level=logging.INFO)
                                  pid_retries = 0
                                  time.sleep(CONTAINER_START_TIMEOUT)
                                  continue
                             else:
                                  log_message(f"Failed to restart container '{CONTAINER_NAME}'. Manual intervention may be required.", level=logging.CRITICAL)
                                  pid_retries = 0 # Avoid rapid retries if restart fails
                        else:
                             log_message("Docker client not initialized. Cannot attempt container restart.", level=logging.WARNING)
                             pid_retries = 0 # Avoid rapid retries
                    else:
                         log_message(f"Target process '{TARGET_PROCESS_NAME}' not found. Retrying...", level=logging.WARNING)
                         time.sleep(CONTAINER_START_TIMEOUT / MAX_PID_RETRIES if MAX_PID_RETRIES > 0 else CONTAINER_START_TIMEOUT)
                         continue

            if current_status["pid"] > 0 and \
               (current_status["cgroup_status"] != "Configured" or \
                current_status.get("last_cgroup_pid") != current_status["pid"]):
                 log_message(f"Attempting cgroup setup for PID {current_status['pid']}...", level=logging.INFO)
                 if create_cgroup():
                     if set_cgroup_limits(current_status["pid"]):
                         log_message(f"Successfully applied cgroup settings for PID {current_status['pid']}.", level=logging.INFO)
                         current_status["cgroup_status"] = "Configured"
                         current_status["last_cgroup_pid"] = current_status["pid"]
                         # Error cleared within set_cgroup_limits on success
                     else:
                         log_message(f"Failed to apply cgroup limits for PID {current_status['pid']}. Will retry.", level=logging.ERROR)
                         # Error set by set_cgroup_limits
                 else:
                     log_message("Failed to create or ensure cgroup exists. Limits cannot be applied. Will retry.", level=logging.ERROR)
                     # Error set by create_cgroup

            elif current_status["pid"] > 0 and current_status["cgroup_status"] == "Configured" and current_status.get("last_cgroup_pid") == current_status["pid"]:
                 log_message(f"PID ({current_status['pid']}) remains the same and cgroup setup done. No changes needed.", level=logging.DEBUG)
                 current_status["status_message"] = f"Monitoring PID {current_status['pid']}. Limits applied."
                 if current_status["error"] and ("pid" in current_status["error"].lower() or "cgroup" in current_status["error"].lower()):
                     current_status["error"] = None

            if current_status["pid"] > 0:
                monitor_resource_usage(current_status["pid"])
            else:
                current_status["memory_usage"] = "N/A"
                current_status["swap_usage"] = "N/A"

            log_message(f"Sleeping for {RESOURCE_CHECK_INTERVAL} seconds...", level=logging.DEBUG)
            time.sleep(RESOURCE_CHECK_INTERVAL)

        except Exception as e:
            log_message(f"Unexpected error in resource management loop: {type(e).__name__} - {e}. Exiting thread.", level=logging.CRITICAL)
            current_status["error"] = f"Critical loop error: {e}"
            current_status["pid"] = 0
            current_status["cgroup_status"] = "Unknown"
            break
    log_message("##### Exiting manage_resources function in background thread #####", level=logging.INFO)


app = Flask(__name__)

@app.route('/')
def index():
    current_status["last_updated"] = datetime.now().isoformat()
    log_message(f"Web UI request from IP: {request.remote_addr}", level=logging.INFO)
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
    log_message(f"/status API request from IP: {request.remote_addr}", level=logging.INFO)
    return jsonify(current_status)

@app.route("/delete_all_swap", methods=['POST'])
def delete_all_swap_files():
    log_message("# Processing request to delete swap files...", level=logging.INFO)
    prefix = SWAP_FILE_PREFIX_TO_DELETE
    work_dir = SWAP_WORK_DIR
    deleted_count = 0
    errors = []

    try:
        log_message(f"Disabling active swap areas matching prefix '{prefix}' in '{work_dir}'...", level=logging.INFO)
        try:
            result = run_subprocess(["sudo", "swapon", "--show", "--noheadings", "--bytes", "--raw"], check=False, description="List active swaps for deletion")
            if result and result.returncode == 0 and result.stdout:
                swap_lines = result.stdout.strip().splitlines()
                for line in swap_lines:
                    parts = line.split()
                    if not parts: continue
                    swap_device = parts[0]
                    if swap_device.startswith('/') and work_dir in swap_device and os.path.basename(swap_device).startswith(prefix):
                        log_message(f"Found active swap target: {swap_device}. Disabling...", level=logging.INFO)
                        swapoff_res = run_subprocess(["sudo", "swapoff", swap_device], check=False, description=f"Disable swap {swap_device}")
                        if swapoff_res and swapoff_res.returncode == 0 and swap_device.startswith("/dev/loop"):
                             log_message(f"Attempting to detach loop device specifically: {swap_device}", level=logging.INFO)
                             run_subprocess(["sudo", "losetup", "-d", swap_device], check=False, description=f"Detach specific loop device {swap_device}")
                        elif swapoff_res and swapoff_res.returncode !=0:
                             errors.append(f"Failed to swapoff {swap_device}: {swapoff_res.stderr.strip() if swapoff_res.stderr else 'Unknown error'}")
            elif result is None:
                 msg = "Timeout occurred while listing swaps for deletion."
                 log_message(msg, level=logging.WARNING)
                 errors.append(msg)
            elif result and result.returncode != 0:
                 msg = f"Failed to list swaps for deletion. Stderr: {result.stderr.strip() if result.stderr else 'N/A'}"
                 log_message(msg, level=logging.WARNING)
                 errors.append(msg)
        except Exception as e:
            msg = f"Error disabling swap areas: {e}"
            log_message(msg, level=logging.WARNING)
            errors.append(msg)

        log_message(f"Deleting swap files starting with '{prefix}' in '{work_dir}'...", level=logging.INFO)
        if os.path.exists(work_dir):
            try:
                for filename in os.listdir(work_dir):
                    if filename.startswith(prefix):
                        swap_file_path = os.path.join(work_dir, filename)
                        log_message(f"Attempting to delete file: {swap_file_path}", level=logging.INFO)
                        try:
                            run_subprocess(["sudo", "swapoff", swap_file_path], check=False, description=f"Retry disable swap file {swap_file_path}")
                            delete_res = run_subprocess(["sudo", "rm", "-f", swap_file_path], check=True, description=f"Delete swap file {swap_file_path}")
                            if delete_res and delete_res.returncode == 0 and not os.path.exists(swap_file_path):
                                log_message(f"Successfully deleted file: {swap_file_path}", level=logging.INFO)
                                deleted_count += 1
                            else:
                                msg = f"Failed to delete file '{swap_file_path}' (or command failed)."
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
             errors.append(msg)

        # --- losetup -D 복구 (주의하여 사용) ---
        log_message("Attempting to detach all loop devices using 'losetup -D'. "
                    "This is a fallback and can affect other services. Use with caution.", level=logging.WARNING)
        losetup_d_all_result = run_subprocess(["sudo", "losetup", "-D"], check=False, description="Detach all loop devices (use with caution)")
        if losetup_d_all_result and losetup_d_all_result.returncode == 0:
            log_message("Successfully detached all loop devices using 'losetup -D'.", level=logging.INFO)
        elif losetup_d_all_result: # Command ran but failed
            msg = f"Command 'losetup -D' failed. Stderr: {losetup_d_all_result.stderr.strip() if losetup_d_all_result.stderr else 'N/A'}"
            log_message(msg, level=logging.WARNING)
            errors.append(msg)
        else: # Command timed out or other run_subprocess issue
            msg = "Command 'losetup -D' did not complete successfully (e.g., timeout)."
            log_message(msg, level=logging.WARNING)
            errors.append(msg)
        # --- losetup -D 복구 끝 ---

        log_message(f"Swap file deletion process finished. Deleted: {deleted_count} files.", level=logging.INFO)
        if errors:
            return jsonify({"message": f"Swap deletion process finished with errors. Deleted {deleted_count} files.", "errors": errors}), 500
        else:
            return jsonify({"message": f"Successfully deleted {deleted_count} swap files."}), 200

    except Exception as e:
        log_message(f"Critical error during swap deletion process: {e}", level=logging.ERROR)
        return jsonify({"error": f"An unexpected error occurred: {e}"}), 500


if __name__ == "__main__":
    log_message("Initializing application...", level=logging.INFO)
    log_message("Starting resource management thread...", level=logging.INFO)
    resource_thread = threading.Thread(target=manage_resources, daemon=True, name="ResourceMgrThread")
    resource_thread.start()
    log_message("Resource management thread started.", level=logging.INFO)

    log_message(f"Starting Flask web server on host 0.0.0.0, port {WEB_UI_PORT}...", level=logging.INFO)
    try:
        app.run(host='0.0.0.0', port=WEB_UI_PORT, debug=DEBUG_MODE, use_reloader=False)
    except Exception as e:
        log_message(f"Failed to start Flask web server: {e}", level=logging.CRITICAL)
        current_status["error"] = f"Flask server error: {e}"
    finally:
        log_message("Flask web server stopped.", level=logging.INFO)