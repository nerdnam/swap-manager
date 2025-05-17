<p align="center">
<img src="https://raw.githubusercontent.com/nerdnam/swap-manager/main/readme/favicon.png"  width="128" height="128" alt="Swap Manager Logo">
</p>
<h1 align="center">Swap Manager</h1>

<p align="center">
Python 기반의 애플리케이션으로, 지정된 프로세스(기본값: Ollama)를 모니터링하고 해당 프로세스에 대한 스왑(Swap) 공간 관리 및 Cgroup을 통한 리소스(메모리, 스왑) 제한을 자동화합니다. Flask 웹 UI를 통해 현재 상태를 확인할 수 있습니다. 낮은 사양의 서버에서 GPU 대신 CPU 및 NVMe SSD 스왑 메모리를 활용하여 대형 LLM(Ollama, DeepSeek 등)을 테스트하는 목적으로 개발되었습니다.
</p>

<p align="center">
<img src="https://raw.githubusercontent.com/nerdnam/swap-manager/main/readme/webui.png" alt="Swap Manager Web UI Screenshot">
</p>

## 최근 변경 사항 (2025-05-11)

*   **Flask 개발 서버 안정화:** Flask 앱 초기 실행 시 불필요한 재시작(reloader) 문제를 해결하여 초기화 로직이 중복 실행되지 않도록 수정했습니다.
*   **컨테이너 종료 시 스왑 자동 정리:**
    *   `docker stop` 명령으로 컨테이너가 정상 종료될 때, 생성했던 스왑 파일을 자동으로 삭제하고 관련된 스왑 설정을 비활성화(초기화)하는 기능을 추가했습니다.
    *   `docker restart` 명령 수행 시에도 (내부적으로 stop 후 start), 이전 스왑이 깨끗하게 정리되고 새로운 스왑이 설정되도록 개선되었습니다.
*   **Favicon 처리 개선:** 웹 UI의 Favicon이 정상적으로 표시되도록 관련 코드를 수정하고, `static` 폴더 관리를 명확히 했습니다.
*   **웹 UI 개선:**
    *   타이틀에 Favicon 이미지를 추가했습니다.
    *   페이지 하단에 GitHub 저장소 링크를 추가했습니다.
    *   상태 값에 따른 CSS 스타일 및 오류 메시지 박스 스타일을 개선했습니다.
    *   "상태 메시지" 항목을 추가하여 현재 애플리케이션의 주요 동작 상태를 UI에서 확인할 수 있도록 했습니다.

## 주요 기능

*   **자동 스왑 파일 생성 및 관리:**
    *   지정된 크기의 스왑 파일을 생성하고 활성화합니다. (losetup 사용)
    *   시스템의 `vm.swappiness` 값을 설정합니다.
    *   애플리케이션 시작 및 **종료 시** 기존/관리 스왑 파티션 및 관련 루프 장치를 정리합니다.
*   **Cgroup을 통한 리소스 제한:**
    *   타겟 프로세스(예: Ollama)의 PID를 찾아 Cgroup v2에 할당합니다.
    *   Cgroup을 통해 타겟 프로세스의 메모리 사용량과 스왑 사용량을 제한합니다.
*   **프로세스 모니터링 및 자동 재시작 (선택적):**
    *   `pgrep`을 사용하여 이름으로 타겟 프로세스의 PID를 찾습니다.
    *   PID를 찾지 못하면, 설정된 Docker 컨테이너 이름으로 해당 컨테이너 재시작을 시도합니다 (Docker SDK 사용).
*   **상태 모니터링 웹 UI:**
    *   Flask 기반의 웹 UI를 통해 현재 스왑 상태, Cgroup 상태, PID, 리소스 사용량, **상태 메시지**, 오류 메시지 등을 실시간으로 확인할 수 있습니다.
*   **유연한 설정:**
    *   `.env` 파일 또는 Docker 환경 변수를 통해 다양한 설정을 변경할 수 있습니다 (스왑 크기, Cgroup 이름, 리소스 제한 값, 대상 프로세스 이름 등).
*   **로그 관리:**
    *   애플리케이션 동작에 대한 상세 로그를 파일 및 콘솔에 기록합니다.
    *   애플리케이션 시작 시 이전 로그 파일을 삭제합니다.

## 디렉터리 구조

```
swap-manager/
├── readme/                   # README.md에 사용될 이미지 등
│   ├── favicon.png           # README.md 헤더용 이미지
│   └── webui.png             # README.md용 웹 UI 스크린샷
├── static/                   # Flask 정적 파일 (CSS, JS, 실제 Favicon 등)
│   ├── favicon.ico           # 웹 브라우저 탭용 Favicon (ICO)
│   ├── favicon.png           # 웹 브라우저 탭 또는 UI 내부용 Favicon (PNG)
│   ├── favicon.svg           # (선택) Favicon (SVG)
│   └── style.css             # 웹 UI용 외부 CSS 파일
├── templates/                # Flask HTML 템플릿
│   └── status.html           # 웹 UI 페이지
├── .github/                  # GitHub Actions 워크플로우
│   └── workflows/
│       └── swap-manager-ghcr.yml # GHCR 이미지 빌드 및 푸시 자동화
├── .dockerignore             # Docker 빌드 시 제외할 파일/폴더
├── .gitignore                # Git 버전 관리에서 제외할 파일/폴더
├── Dockerfile                # Docker 이미지 빌드 스크립트
├── LICENSE                   # 프로젝트 라이선스 파일
├── README.md                 # 프로젝트 설명 및 안내 (이 파일)
├── app.py                    # 메인 애플리케이션 로직
├── docker-compose.yaml       # Docker Compose 설정
├── requirements.txt          # Python 의존성 목록
└── swap.env.example          # 환경 변수 설정 파일 예시 (실제 사용 시 swap.env로 복사)
```

## 시스템 요구 사항

*   Docker 및 Docker Compose (Docker Compose는 권장 사항)
*   Python 3.9+ (소스에서 직접 실행 시)
*   Linux 환경 (Cgroup v2 및 스왑 관리 기능 사용)
*   컨테이너 실행 시 `privileged: true` 및 `pid: host` 권한 필요 (Cgroup 및 시스템 정보 접근용)

## 설치 및 실행

### 공통 준비 사항 (두 방법 모두 필요)

1.  **환경 변수 설정 (`swap.env` 파일 생성):**
    애플리케이션 설정을 위해 `swap.env` 파일을 생성해야 합니다. 프로젝트 루트의 `swap.env.example` 파일을 복사하여 `swap.env`로 만들고 내용을 수정합니다. 이 파일은 Docker Compose의 `env_file` 지시어나 `docker run`의 `--env-file` 옵션을 통해 컨테이너에 전달됩니다.
    실행 환경에 따라 이 파일의 경로를 적절히 지정해야 합니다 (예: `/mnt/Data3/swap-manager/swap.env`).

    ```env
    # 예시: /mnt/Data3/swap-manager/swap.env

    # 스왑 파일 크기 (예: 512G, 8G, 1024M)
    SWAP_SIZE=64G
    # 스왑 파일 이름 (SWAP_SIZE 변수를 사용하여 동적으로 설정 가능하나, Python에서 직접 해석 안됨)
    SWAP_FILE=swapfile_64G.gb # SWAP_SIZE 값에 맞춰 명시적으로 기입 권장
    # vm.swappiness 설정 값 (시스템 기본값은 60, 최대 200)
    SWAPINESS=200

    # 리소스 관리를 적용할 대상 컨테이너의 이름 (Docker SDK 사용 시 필요 - 현재는 재시작 기능용)
    CONTAINER_NAME=ix-ollama-ollama-1
    # PID를 찾을 대상 프로세스의 전체 명령 라인 (pgrep -f 사용)
    TARGET_PROCESS_NAME=/bin/ollama serve
    # 사용할 Cgroup의 이름 (컨테이너 내부에 생성될 경로의 마지막 부분)
    CGROUP_NAME=my_large_process
    # Cgroup을 통해 설정할 메모리 제한 (컨테이너의 RAM 사용량 제한)
    MEMORY_LIMIT=8G
    # Cgroup을 통해 설정할 스왑 제한 (SWAP_SIZE와 동일하게 설정 권장)
    SWAP_LIMIT=64G

    # 대상 컨테이너의 PID를 찾기 위한 최대 재시도 횟수
    MAX_PID_RETRIES=5
    # PID를 찾지 못했을 때 컨테이너 재시작 후 대기하는 타임아웃 (초)
    CONTAINER_START_TIMEOUT=30
    # 리소스 관리 루프가 다음 작업을 수행하기까지 대기하는 주기 (초)
    RESOURCE_CHECK_INTERVAL=30

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
    **참고:** `SWAP_FILE` 및 `SWAP_LIMIT` 값은 `SWAP_SIZE`와 일치하도록 수동으로 설정하는 것이 좋습니다.

2.  **필요한 호스트 디렉터리 생성:**
    컨테이너와 데이터를 공유하기 위한 호스트 디렉터리를 생성하고 적절한 권한을 부여합니다.
    ```bash
    # 스왑 파일 저장용 디렉터리 (docker-compose.yaml의 /mnt/SwapWork에 매핑될 경로)
    sudo mkdir -p /mnt/Data3/SwapWork
    # 로그 파일 및 swap.env 파일 저장용 디렉터리
    sudo mkdir -p /mnt/Data3/swap-manager
    # 권한 설정 (필요한 경우, 보안에 유의하여 최소한의 권한 부여)
    # sudo chmod -R 777 /mnt/Data3/SwapWork # 보안에 매우 유의
    # sudo chmod -R 777 /mnt/Data3/swap-manager # 보안에 매우 유의
    ```
    (실제 프로덕션 환경에서는 `777` 권한 대신 더 제한적인 권한을 사용하는 것을 권장합니다.)

---

### 방법 1: GHCR의 Docker 이미지 사용 (권장)

GitHub Container Registry (GHCR)에 미리 빌드된 이미지를 사용합니다.

1.  **(공통 준비 사항 1, 2 완료)**

2.  **Docker Compose를 사용하여 실행:**
    원하는 경로에 아래 내용으로 `docker-compose.yaml` 파일을 저장합니다. `env_file` 및 `volumes`의 호스트 경로는 "공통 준비 사항"에서 생성한 실제 경로로 수정해야 합니다.

    ```yaml
    # docker-compose.yaml
    services:
      swap-manager:
        image: ghcr.io/nerdnam/swap-manager:latest # 또는 특정 버전: ghcr.io/nerdnam/swap-manager:0.0.1
        container_name: swap-manager
        privileged: true
        pid: host
        ports:
          - "30067:5000" # <호스트_포트>:<컨테이너_내부_WEB_UI_PORT>
        volumes:
          - /sys/fs/cgroup:/sys/fs/cgroup:rw
          - /var/run/docker.sock:/var/run/docker.sock
          - /mnt/Data3/SwapWork:/mnt/SwapWork:rwx # 스왑 파일 저장 경로 (호스트:컨테이너)
          - /mnt/Data3/swap-manager/log:/var/log/my_app:rwx # 로그 저장 경로 (호스트:컨테이너)
          - /etc/localtime:/etc/localtime:ro
        env_file:
          - /mnt/Data3/swap-manager/swap.env # 실제 swap.env 파일 경로
        environment:
          - DOCKER_HOST=unix:///var/run/docker.sock # 컨테이너 내 Docker 클라이언트 설정
        restart: unless-stopped
    ```
    실행:
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
      -v /mnt/Data3/SwapWork:/mnt/SwapWork:rwx \
      -v /mnt/Data3/swap-manager/log:/var/log/my_app:rwx \
      -v /etc/localtime:/etc/localtime:ro \
      --env-file /mnt/Data3/swap-manager/swap.env \
      -e DOCKER_HOST=unix:///var/run/docker.sock \
      --restart unless-stopped \
      ghcr.io/nerdnam/swap-manager:latest
    ```

---

### 방법 2: 소스 코드에서 직접 빌드 및 실행 (개발용)

1.  **저장소 복제:**
    ```bash
    git clone https://github.com/nerdnam/swap-manager.git
    cd swap-manager
    ```

2.  **(공통 준비 사항 1, 2 완료)**
    `swap.env` 파일은 복제된 `swap-manager` 디렉터리 내 (예: `/mnt/Data3/swap-manager/swap.env`를 사용) 또는 프로젝트 루트에 `swap.env`로 만들고 `docker-compose.yaml`의 `env_file` 경로를 `./swap.env`로 수정합니다.

3.  **Docker Compose를 사용하여 빌드 및 실행:**
    프로젝트 루트의 `docker-compose.yaml` 파일을 다음과 같이 수정하거나, 별도의 개발용 compose 파일을 사용합니다.

    ```yaml
    # docker-compose.yaml (소스 빌드용)
    services:
      swap-manager:
        build:
          context: .
          dockerfile: Dockerfile
        container_name: swap-manager
        privileged: true
        pid: host
        ports:
          - "30067:5000"
        volumes:
          - /sys/fs/cgroup:/sys/fs/cgroup:rw
          - /var/run/docker.sock:/var/run/docker.sock
          - /mnt/Data3/SwapWork:/mnt/SwapWork:rwx
          - /mnt/Data3/swap-manager/log:/var/log/my_app:rwx
          - ./app.py:/app/app.py # 코드 변경 시 바로 반영 (개발 편의성)
          - ./static:/app/static # 정적 파일 변경 시 바로 반영
          - ./templates:/app/templates # 템플릿 변경 시 바로 반영
          - /etc/localtime:/etc/localtime:ro
        env_file:
          - /mnt/Data3/swap-manager/swap.env # 또는 ./swap.env
        environment:
          - DOCKER_HOST=unix:///var/run/docker.sock
          - FLASK_DEBUG=1 # Flask 디버그 모드 활성화 (개발 시 유용)
        restart: unless-stopped
    ```
    실행 (프로젝트 루트에서):
    ```bash
    docker-compose up -d --build
    ```

---

### 실행 후 확인 (모든 방법에 공통)

1.  **상태 확인:**
    웹 브라우저에서 `http://<호스트_IP>:30067` (또는 설정한 호스트 포트)로 접속하여 상태를 확인합니다.

2.  **로그 확인:**
    컨테이너 로그:
    ```bash
    docker logs swap-manager -f
    ```
    호스트에 마운트된 로그 파일 (위 예시에서는 `/mnt/Data3/swap-manager/log/swap_manager.log`):
    ```bash
    tail -f /mnt/Data3/swap-manager/log/swap_manager.log
    ```

3.  **종료 시 정리 확인:**
    `docker stop swap-manager` 및 `docker restart swap-manager` 명령을 실행했을 때, 로그를 통해 스왑 파일 및 관련 설정이 정상적으로 정리(삭제 및 비활성화)되는지 확인합니다.

## 환경 변수 상세

`swap.env` 파일 또는 Docker 실행 시 환경 변수를 통해 다음 항목들을 설정할 수 있습니다. (아래는 `swap.env` 예시 값 기준, Dockerfile 기본값은 다를 수 있음)

| 변수명                      | 설명                                                                             | `swap.env` 예시 값               |
| :-------------------------- | :------------------------------------------------------------------------------- | :-------------------------------- |
| `SWAP_SIZE`                 | 스왑 파일의 크기 (예: `512G`, `8G`, `1024M`)                                      | `64G`                             |
| `SWAP_FILE`                 | 생성될 스왑 파일의 이름 (보통 `SWAP_SIZE`와 연관지어 명명)                        | `swapfile_64G.gb`                 |
| `SWAPINESS`                 | 시스템의 `vm.swappiness` 값 (0-200)                                               | `200`                             |
| `CONTAINER_NAME`            | 모니터링 및 재시작 대상 Docker 컨테이너 이름                                       | `ix-ollama-ollama-1`              |
| `TARGET_PROCESS_NAME`       | `pgrep -f`로 PID를 찾을 대상 프로세스의 전체 명령 라인                             | `/bin/ollama serve`               |
| `CGROUP_NAME`               | 생성/사용할 Cgroup의 이름 (예: `my_large_process`)                                | `my_large_process`                |
| `MEMORY_LIMIT`              | Cgroup을 통해 설정할 메모리 제한 (예: `8G`)                                       | `8G`                              |
| `SWAP_LIMIT`                | Cgroup을 통해 설정할 스왑 제한 (예: `64G`)                                        | `64G`                             |
| `MAX_PID_RETRIES`           | PID 찾기 최대 재시도 횟수                                                         | `5`                               |
| `CONTAINER_START_TIMEOUT`   | PID 찾기 실패 후 컨테이너 재시작 시 대기 시간 (초)                                 | `30`                              |
| `RESOURCE_CHECK_INTERVAL`   | 리소스 관리 루프의 주기 (초)                                                       | `30`                              |
| `LOG_FILE`                  | 로그 파일 경로 (컨테이너 내부)                                                    | `/var/log/my_app/swap_manager.log` |
| `SWAP_FILE_PREFIX_TO_DELETE`| `/delete_all_swap` 엔드포인트에서 삭제할 스왑 파일 접두사                          | `swapfile`                        |
| `SWAP_WORK_DIR`             | 스왑 파일 생성 경로 (컨테이너 내부)                                               | `/mnt/SwapWork`                    |
| `WEB_UI_PORT`               | Flask 웹 UI 포트 (컨테이너 내부)                                                  | `5000`                             |
| `DEBUG`                     | 디버그 모드 활성화 (`true` 또는 `false`)                                           | `true`                            |


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

[MIT 라이선스](LICENSE)