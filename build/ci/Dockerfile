ARG BASE_IMAGE=joshchankj/gasleakagent_base:latest
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000

WORKDIR /app

COPY documiner/backend/requirements.txt .
RUN pip config set global.index-url https://nexus.in.spdigital.sg/repository/pypi-proxy/simple \
    && pip install --no-cache-dir -r requirements.txt

COPY documiner/ .

WORKDIR /app/backend

EXPOSE 5000

CMD ["python", "app.py"]