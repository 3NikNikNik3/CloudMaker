FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /api

RUN apt-get update && apt-get install -y --no-install-recommends qemu-system-x86 qemu-utils socat && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ ./api/

EXPOSE 8000

#CMD ["unicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
#CMD ["python3" "main.py"]
CMD ["fastapi", "run", "api/main.py", "--host", "0.0.0.0", "--port", "8000"]
