<p align="center">
<img src="https://github.com/nerdnam/swap-manager/blob/main/readme/favicon.png"  width="300" height="300">
</p>

<p align="center">
<img src="https://github.com/nerdnam/swap-manager/blob/main/readme/webui.png" >
</p>


# Swap & Cgroup Manager for Ollama
# (or other large processes)

Python 기반의 애플리케이션으로, 지정된 프로세스(기본값: Ollama)를 모니터링하고 해당 프로세스에 대한 스왑(Swap) 공간 관리 및 Cgroup을 통한 리소스(메모리, 스왑) 제한을 자동화합니다. Flask 웹 UI를 통해 현재 상태를 확인할 수 있습니다. 낮은 사양의 서버에서 CPU 대신 스왑 메모리를 활용하여 대형 LLM(Ollama, DeepSeek 등)을 테스트하는 목적으로 개발되었습니다.

## 주요 기능

*   **자동 스왑 파일 생성 및 관리:**
    *   지정된 크기의 스왑 파일을 생성하고 활성화합니다. (losetup 사용)
    *   시스템의 `vm.swappiness` 값을 설정합니다.
    *   애플리케이션 시작 시 기존 스왑 파티션 및 관련 루프 장치를 정리합니다.
*   **Cgroup을 통한 리소스 제한:**
    *   타겟 프로세스(예: Ollama)의 PID를 찾아 Cgroup에 할당합니다.
    *   Cgroup을 통해 타겟 프로세스의 메모리 사용량과 스왑 사용량을 제한합니다.
*   **프로세스 모니터링 및 자동 재시작 (선택적):**
    *   `pgrep`을 사용하여 이름으로 타겟 프로세스의 PID를 찾습니다.
    *   PID를 찾지 못하면, 설정된 Docker 컨테이너 이름으로 해당 컨테이너 재시작을 시도합니다 (Docker SDK 사용).
*   **상태 모니터링 웹 UI:**
    *   Flask 기반의 웹 UI를 통해 현재 스왑 상태, Cgroup 상태, PID, 리소스 사용량, 오류 메시지 등을 실시간으로 확인할 수 있습니다.
*   **유연한 설정:**
    *   `.env` 파일 또는 Docker 환경 변수를 통해 다양한 설정을 변경할 수 있습니다 (스왑 크기, Cgroup 이름, 리소스 제한 값, 대상 프로세스 이름 등).
*   **로그 관리:**
    *   애플리케이션 동작에 대한 상세 로그를 파일 및 콘솔에 기록합니다.
    *   애플리케이션 시작 시 이전 로그 파일을 삭제합니다.

## 시스템 요구 사항

*   Docker 및 Docker Compose (Docker Compose는 권장 사항)
*   Python 3.9+ (소스에서 직접 빌드 시)
*   Linux 환경 (Cgroup 및 스왑 관리 기능 사용)
*   컨테이너 실행 시 `privileged: true` 및 `pid: host` 권한 필요

## 설치 및 실행

애플리케이션을 실행하는 두 가지 주요 방법이 있습니다: 소스 코드에서 직접 빌드하거나, 미리 빌드된 Docker Hub 이미지를 사용하는 것입니다.

### 공통 준비 사항 (두 방법 모두 필요)

1.  **환경 변수 설정 (`swap.env` 파일 생성):**
    애플리케이션 설정을 위해 `swap.env` 파일을 생성해야 합니다. 이 파일은 Docker Compose의 `env_file` 지시어나 `docker run`의 `--env-file` 옵션을 통해 컨테이너에 전달됩니다.
    로컬 시스템의 원하는 경로 (예: `/mnt/swap-manager/swap.env`)에 아래 예시를 참고하여 `swap.env` 파일을 생성하세요.

    ```env
    # /mnt/swap-manager/swap.env 예시

    # 스왑 파일 크기 (예: 512G, 8G, 1024M)
    SWAP_SIZE=512G
    # 스왑 파일 이름 (SWAP_SIZE 변수를 사용하여 동적으로 설정 가능)
    SWAP_FILE=swapfile_${SWAP_SIZE}.gb
    # vm.swappiness 설정 값 (시스템 기본값은 60, 최대 200)
    SWAPINESS=200

    # 리소스 관리를 적용할 대상 컨테이너의 이름 (Docker SDK 사용 시 필요 - 현재는 재시작 기능용)
    CONTAINER_NAME=ix-ollama-ollama-1
    # PID를 찾을 대상 프로세스의 전체 명령 라인 (pgrep -f 사용)
    TARGET_PROCESS_NAME=/bin/ollama serve
    # 사용할 Cgroup의 이름 (컨테이너 내부에 생성될 경로의 마지막 부분)
    CGROUP_NAME=my_large_process
    # Cgroup을 통해 설정할 메모리 제한 (컨테이너의 RAM 사용량 제한)
    MEMORY_LIMIT=4G
    # Cgroup을 통해 설정할 스왑 제한 (SWAP_SIZE와 동일하게 설정 가능)
    SWAP_LIMIT=512G # SWAP_SIZE 환경 변수를 참조하는 대신 명시적 값 권장

    # 대상 컨테이너의 PID를 찾기 위한 최대 재시도 횟수
    MAX_PID_RETRIES=5
    # PID를 찾지 못했을 때 컨테이너 재시작 후 대기하는 타임아웃 (초)
    CONTAINER_START_TIMEOUT=30
    # 리소스 관리 루프가 다음 작업을 수행하기까지 대기하는 주기 (초)
    RESOURCE_CHECK_INTERVAL=60

    # 애플리케이션 로그 파일이 저장될 경로 (컨테이너 내부 경로)
    LOG_FILE=/var/log/my_app/swap_manager.log
    # Flask 웹 서버가 실행될 포트 (컨테이너 내부 포트)
    WEB_UI_PORT=5000
    # 스왑 파일이 생성될 호스트 볼륨 마운트 경로 (컨테이너 내부 경로)
    SWAP_WORK_DIR=/mnt/SwapWork
    # 디버그 모드 활성화 여부 (로그 상세도 증가 등)
    DEBUG=true
    # 삭제할 스왑 파일 접두사
    SWAP_FILE_PREFIX_TO_DELETE=swapfile
    ```
    **참고:** `SWAP_LIMIT=${SWAP_SIZE}`와 같이 환경 변수 내에서 다른 환경 변수를 참조하는 기능은 쉘 스크립트나 일부 Docker Compose 버전에서 지원될 수 있지만, Python의 `os.environ.get`은 직접적으로 이를 해석하지 않습니다. `SWAP_LIMIT` 값을 명시적으로 설정하는 것이 (예: `SWAP_LIMIT=512G`) 가장 확실합니다.

2.  **필요한 호스트 디렉터리 생성:**
    컨테이너와 데이터를 공유하기 위한 호스트 디렉터리를 생성하고 적절한 권한을 부여합니다. `docker-compose.yaml` 또는 `docker run` 명령어에서 이 경로들을 사용합니다.

    ```bash
    # 스왑 파일 저장용 디렉터리 (SWAP_WORK_DIR에 매핑될 경로)
    sudo mkdir -p /mnt/SwapWork
    # 로그 파일 및 swap.env 파일 저장용 디렉터리
    sudo mkdir -p /mnt/swap-manager
    # 권한 설정 (필요한 경우, 보안에 유의하여 최소한의 권한 부여)
    sudo chmod -R 777 /mnt/SwapWork
    sudo chmod -R 777 /mnt/swap-manager
    ```

### 방법 1: 소스 코드에서 직접 빌드 및 실행

이 방법은 코드를 직접 수정하거나 최신 변경 사항을 바로 적용하고 싶을 때 유용합니다.

1.  **저장소 복제 (Clone the repository):**
    ```bash
    git clone https://github.com/nerdnam/swap-manager.git
    cd swap-manager # 복제된 디렉터리로 이동
    ```

2.  **(공통 준비 사항 1, 2 완료)**
    `swap.env` 파일은 복제된 `swap-manager` 디렉터리 내에 생성하거나, 위에서 지정한 `/mnt/swap-manager/swap.env` 경로를 사용하고 `docker-compose.yaml`에서 해당 경로를 정확히 지정합니다.

3.  **Docker 이미지 빌드:**
    프로젝트 루트(복제된 `swap-manager` 디렉터리)에 있는 `docker-compose.yaml` 파일을 사용하거나, 직접 빌드합니다.
    ```bash
    # docker-compose.yaml 사용 시 (프로젝트 루트에서)
    docker-compose build swap-manager
    ```
    또는, 직접 빌드:
    ```bash
    docker build -t your-custom-name/swap-manager:latest .
    ```
    (이 경우 아래 `docker-compose.yaml`의 `image` 항목을 `your-custom-name/swap-manager:latest`로 수정하거나, `build: .`을 사용합니다.)

4.  **Docker Compose를 사용하여 애플리케이션 실행:**
    프로젝트 루트의 `docker-compose.yaml` 파일을 사용합니다. `env_file` 경로는 "공통 준비 사항"에서 생성한 `swap.env` 파일의 실제 위치로 지정해야 합니다.

    ```yaml
    # docker-compose.yaml (소스 빌드용)
    services:
      swap-manager:
        build: . # 현재 디렉터리의 Dockerfile을 사용하여 빌드
        container_name: swap-manager
        privileged: true
        pid: host
        ports:
          - "30067:5000" # 호스트포트:컨테이너포트 (swap.env의 WEB_UI_PORT와 일치)
        volumes:
          - /sys/fs/cgroup:/sys/fs/cgroup:rw
          - /var/run/docker.sock:/var/run/docker.sock
          - /mnt/SwapWork:/mnt/SwapWork:rwx       # 공통 준비 사항에서 생성한 스왑 작업 경로
          - /mnt/swap-manager:/var/log/my_app:rwx # 공통 준비 사항에서 생성한 로그 및 설정 저장 경로
          - /etc/localtime:/etc/localtime:ro
        env_file:
          - /mnt/swap-manager/swap.env # 공통 준비 사항에서 생성한 swap.env 파일 경로
        environment:
          - DOCKER_HOST=unix:///var/run/docker.sock
        restart: unless-stopped
    ```
    실행 (프로젝트 루트에서):
    ```bash
    docker-compose up -d swap-manager
    ```

### 방법 2: 미리 빌드된 Docker Hub 이미지 사용 (`nerdnam/swap-manager:latest`)

이 방법을 사용하면 소스 코드를 다운로드하거나 직접 빌드할 필요 없이 Docker Hub에서 이미지를 바로 가져와 실행할 수 있습니다.

1.  **(공통 준비 사항 1, 2 완료)**

2.  **Docker Compose를 사용하여 실행 (권장):**
    새로운 `docker-compose.yaml` 파일을 작성하거나, 기존 파일을 수정하여 `image` 지시어를 사용합니다. `env_file` 및 `volumes` 경로는 "공통 준비 사항"에서 생성한 실제 경로로 지정합니다.

    ```yaml
    # docker-compose.yaml (미리 빌드된 이미지 사용 예시)
    services:
      swap-manager:
        image: nerdnam/swap-manager:latest # Docker Hub 이미지 사용
        container_name: swap-manager
        privileged: true
        pid: host
        ports:
          - "30067:5000" # 호스트 포트:컨테이너 포트 (swap.env의 WEB_UI_PORT와 일치)
        volumes:
          - /sys/fs/cgroup:/sys/fs/cgroup:rw
          - /var/run/docker.sock:/var/run/docker.sock
          - /mnt/SwapWork:/mnt/SwapWork:rwx       # 공통 준비 사항에서 생성한 스왑 작업 경로
          - /mnt/swap-manager:/var/log/my_app:rwx # 공통 준비 사항에서 생성한 로그 및 설정 저장 경로
          - /etc/localtime:/etc/localtime:ro
        env_file:
          - /mnt/swap-manager/swap.env # 공통 준비 사항에서 생성한 swap.env 파일 경로
        environment:
          - DOCKER_HOST=unix:///var/run/docker.sock
        restart: unless-stopped
    ```
    원하는 경로에 위 내용으로 `docker-compose.yaml` 파일을 저장한 후 실행합니다:
    ```bash
    docker-compose -f /path/to/your/docker-compose.yaml up -d
    ```

3.  **`docker run` 명령어를 사용하여 직접 실행:**
    ```bash
    docker run -d \
      --name swap-manager \
      --privileged \
      --pid=host \
      -p 30067:5000 \
      -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -v /mnt/SwapWork:/mnt/SwapWork:rwx \
      -v /mnt/swap-manager:/var/log/my_app:rwx \
      -v /etc/localtime:/etc/localtime:ro \
      --env-file /mnt/swap-manager/swap.env \
      -e DOCKER_HOST=unix:///var/run/docker.sock \
      --restart unless-stopped \
      nerdnam/swap-manager:latest
    ```
    **참고:**
    *   위 `docker run` 명령어의 볼륨 경로 (`/mnt/...`) 및 `--env-file` 경로는 실제 환경에 맞게 수정해야 합니다.
    *   `-p 30067:5000` 포트 매핑은 `swap.env`의 `WEB_UI_PORT`가 `5000`으로 설정된 것을 가정합니다.

### 실행 후 확인 (모든 방법에 공통)

1.  **상태 확인:**
    웹 브라우저에서 `http://<호스트_IP>:30067` (또는 `docker-compose.yaml`이나 `docker run`에서 설정한 호스트 포트)로 접속하여 상태를 확인합니다.

2.  **로그 확인:**
    컨테이너 로그 확인:
    ```bash
    docker logs swap-manager -f
    ```
    호스트에 마운트된 로그 파일 확인 (경로는 설정에 따라 다름, 위 예시에서는 `/mnt/swap-manager/swap_manager.log`):
    ```bash
    tail -f /mnt/swap-manager/swap_manager.log
    ```

## 환경 변수 상세

`swap.env` 파일 또는 Docker 실행 시 환경 변수를 통해 다음 항목들을 설정할 수 있습니다.

| 변수명                      | 설명                                                                             |        기본값 (Dockerfile)         |
| :-------------------------- | :------------------------------------------------------------------------------- | :-------------------------------- |
| `SWAP_FILE`                 | 생성될 스왑 파일의 이름                                                           | `swapfile_512G.gb`                |
| `SWAP_SIZE`                 | 스왑 파일의 크기 (예: `512G`, `8G`, `1024M`)                                      | `512G`                            |
| `SWAPINESS`                 | 시스템의 `vm.swappiness` 값 (0-200)                                               | `200`                             |
| `CONTAINER_NAME`            | 모니터링 및 재시작 대상 Docker 컨테이너 이름                                       | `ix-ollama-ollama-1`              |
| `TARGET_PROCESS_NAME`       | `pgrep -f`로 PID를 찾을 대상 프로세스의 전체 명령 라인                             | `/bin/ollama serve`               |
| `CGROUP_NAME`               | 생성/사용할 Cgroup의 이름 (예: `my_large_process`)                                | `my_large_process`                |
| `MEMORY_LIMIT`              | Cgroup을 통해 설정할 메모리 제한 (예: `8G`, `0G` 또는 `0`은 제한 없음 의미로 해석) | `8G`                              |
| `SWAP_LIMIT`                | Cgroup을 통해 설정할 스왑 제한 (예: `512G`, `0G` 또는 `0`은 제한 없음 의미로 해석)| `512G`                             |
| `MAX_PID_RETRIES`           | PID 찾기 최대 재시도 횟수                                                         | `5`                               |
| `CONTAINER_START_TIMEOUT`   | PID 찾기 실패 후 컨테이너 재시작 시 대기 시간 (초)                                 | `30`                              |
| `RESOURCE_CHECK_INTERVAL`   | 리소스 관리 루프의 주기 (초)                                                       | `30`                              |
| `LOG_FILE`                  | 로그 파일 경로 (컨테이너 내부)                                                    | `/var/log/my_app/swap_manager.log` |
| `SWAP_FILE_PREFIX_TO_DELETE`| `/delete_all_swap` 엔드포인트에서 삭제할 스왑 파일 접두사                          | `swapfile`                        |
| `SWAP_WORK_DIR`             | 스왑 파일 생성 경로 (컨테이너 내부)                                               | `/mnt/SwapWork`                    |
| `WEB_UI_PORT`               | Flask 웹 UI 포트 (컨테이너 내부)                                                  | `5000`                             |
| `DEBUG`                     | 디버그 모드 활성화 (`true` 또는 `false`)                                           | `false`                           |


## API 엔드포인트

*   `GET /`: 현재 상태를 보여주는 HTML 페이지를 렌더링합니다.
*   `GET /status`: 현재 상태 정보를 JSON 형태로 반환합니다.
*   `POST /delete_all_swap`: `SWAP_WORK_DIR` 내에서 `SWAP_FILE_PREFIX_TO_DELETE`로 시작하는 모든 스왑 파일을 찾아 비활성화하고 삭제합니다. 관련된 루프 장치 해제를 시도하며, 최후의 수단으로 `losetup -D`를 실행할 수 있습니다 (주의 필요).

## 주의사항

*   이 애플리케이션은 시스템의 스왑 및 Cgroup 설정을 변경하므로, **충분한 이해와 테스트 후 사용**해야 합니다.
*   컨테이너는 `privileged: true` 및 `pid: host` 권한으로 실행되어야 합니다. 이는 보안상 위험을 수반할 수 있으므로 신뢰할 수 있는 환경에서만 사용하십시오.
*   `/delete_all_swap` 엔드포인트의 `losetup -D` 명령어는 시스템의 모든 루프 장치를 해제할 수 있으므로, 사용에 각별한 주의가 필요합니다. 이 기능은 특정 환경에서 다른 방법으로 루프 장치가 해제되지 않을 때 최후의 수단으로 고려될 수 있습니다.

## 향후 개선 방향 (TODO)

*   `/delete_all_swap` 엔드포인트에서 특정 파일에 연결된 루프 장치만 선별적으로 해제하는 로직 고도화.
*   웹 UI를 통한 설정 변경 기능 추가.
*   더 상세한 리소스 사용량 통계 및 그래프 제공.

## 라이선스

[MIT](LICENSE)