FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir \
    -i https://mirrors.aliyun.com/pypi/simple \
    --trusted-host mirrors.aliyun.com \
    -r requirements.txt

COPY main.py .
COPY network_access.py .
COPY static ./static

EXPOSE 8080

CMD ["python", "main.py", "--host", "0.0.0.0"]
