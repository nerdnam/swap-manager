# Dockerfile

# Python 3.9 Slim 이미지를 기반으로 합니다. (더 가벼운 이미지)
FROM python:3.9-slim

# 환경 변수 설정 (기본값 설정)
ENV PYTHONUNBUFFERED=1 \
    WEB_UI_PORT=5000 \
    SWAP_WORK_DIR=/mnt/SwapWork \
    SWAP_FILE=swapfile_512G.gb \
    TARGET_PROCESS_NAME="/bin/ollama serve" \
    CGROUP_NAME="my_large_process" \
    MEMORY_LIMIT="8G" \
    SWAP_LIMIT="512G" \
    SWAPINESS=200 \
    MAX_PID_RETRIES=5 \
    CONTAINER_START_TIMEOUT=30 \
    RESOURCE_CHECK_INTERVAL=30 \
    LOG_FILE=/var/log/my_app/swap_manager.log \
    DEBUG=false \
    CONTAINER_NAME=ix-ollama-ollama-1 \
    SWAP_FILE_PREFIX_TO_DELETE=swapfile

# 필요한 시스템 패키지 설치
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    sudo \
    procps \
    util-linux \
    coreutils \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

# 파이썬 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 작업 디렉토리 설정
WORKDIR /app

# 애플리케이션 파일 복사
COPY app.py .
COPY templates/ templates/
COPY static/ static/
COPY swap.env /app/swap.env

# ENTRYPOINT 설정
# 컨테이너 시작 시 "python app.py"를 실행합니다.
ENTRYPOINT ["python", "app.py"]

# CMD는 ENTRYPOINT에 전달될 기본 인자로 사용될 수 있으나, 여기서는 ENTRYPOINT만으로 충분합니다.
# 만약 app.py에 인자를 전달해야 한다면 CMD ["arg1", "arg2"] 와 같이 사용할 수 있습니다.
# CMD [] # 비워두거나 생략 가능