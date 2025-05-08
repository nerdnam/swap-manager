# Dockerfile

# Python 3.9 Slim 이미지를 기반으로 합니다. (더 가벼운 이미지)
FROM python:3.9-slim

# 환경 변수 설정 (기본값 설정)
# 이 값들은 docker-compose.yml의 env_file 또는 environment 설정으로 오버라이드될 수 있습니다.
ENV PYTHONUNBUFFERED=1 \
    # Flask 웹 서버 포트
    WEB_UI_PORT=5000 \
    # 스왑 파일 경로 및 이름 (컨테이너 내부 경로)
    SWAP_WORK_DIR=/mnt/SwapWork \
    SWAP_FILE=swapfile_512G.gb \
    # PID를 찾을 대상 프로세스 이름 (pgrep -f 사용)
    TARGET_PROCESS_NAME="/bin/ollama serve" \
    # 사용할 Cgroup의 이름 (컨테이너 내부에 생성될 경로의 마지막 부분)
    CGROUP_NAME="my_large_process" \
    # Cgroup을 통해 설정할 메모리 제한 (컨테이너의 RAM 사용량 제한)
    MEMORY_LIMIT="8G" \
    # Cgroup을 통해 설정할 스왑 제한 (컨테이너의 스왑 사용량 제한)
    SWAP_LIMIT="512G" \
    # vm.swappiness 설정 값 (시스템 기본값은 60, 최대 200)
    SWAPINESS=200 \
    # 대상 컨테이너의 PID를 찾기 위한 최대 재시도 횟수
    MAX_PID_RETRIES=5 \
    # PID를 찾지 못했을 때 컨테이너 재시작 후 대기하는 타임아웃 (초)
    CONTAINER_START_TIMEOUT=30 \
    # 리소스 관리 루프가 다음 작업을 수행하기까지 대기하는 주기 (초)
    # 이전 CONTAINER_CHECK_INTERVAL 대신 RESOURCE_CHECK_INTERVAL 사용
    RESOURCE_CHECK_INTERVAL=30 \
    # 애플리케이션 로그 파일이 저장될 경로 (컨테이너 내부 경로)
    LOG_FILE=/var/log/my_app/swap_manager.log \
    # 디버그 모드 활성화 여부 (로그 상세도 증가 등)
    DEBUG=false \
    # Docker SDK 사용 시 컨테이너 이름 (재시작 기능용)
    CONTAINER_NAME=ix-ollama-ollama-1 \
    # 삭제할 스왑 파일 접두사 (신규 추가 또는 기본값 설정)
    SWAP_FILE_PREFIX_TO_DELETE=swapfile

# 필요한 시스템 패키지 설치
# sudo: 권한 상승에 필요
# procps: pgrep 명령어를 포함
# util-linux: losetup, swapon, swapoff 명령어를 포함
# truncate: 스왑 파일 생성에 필요 (fallocate도 고려 가능)
# coreutils: chmod, rm 등 기본 유틸리티
# iproute2: ip 명령 (네트워크 관련 정보 확인 시 유용할 수 있음)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    sudo \
    procps \
    util-linux \
    coreutils \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

# 파이썬 의존성 설치
# requirements.txt 파일은 이 Dockerfile과 같은 디렉토리에 있어야 합니다.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 작업 디렉토리 설정
WORKDIR /app

# 애플리케이션 파일 복사
# app.py: 메인 파이썬 스크립트
# templates/: Flask 웹 UI 템플릿 디렉토리
COPY app.py .
COPY templates/ templates/

# swap.env 파일 복사
# 이 파일은 컨테이너 내부의 /app/swap.env로 복사되며,
# app.py에서 python-dotenv를 통해 로드될 수 있습니다 (docker-compose env_file 우선).
COPY swap.env /app/swap.env

# 실행 권한 설정 (일반적으로 Python 스크립트는 필요 없음)
# RUN chmod +x app.py

# 컨테이너 시작 시 실행될 명령어 설정
# docker-compose.yml의 command가 이 부분을 오버라이드합니다.
# CMD ["python", "app.py"]

# Flask 웹 서버 포트 노출 (선택 사항, docker-compose에서 이미 포트 매핑)
# EXPOSE ${WEB_UI_PORT}