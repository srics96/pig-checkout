FROM public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
VOLUME ["/tmp"]
USER 65534:65534
CMD ["ddtrace-run","uvicorn","main:app","--host","0.0.0.0","--port","8080","--no-access-log"]
